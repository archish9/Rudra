# NOTES — laya real output schema (S1 Task 3)

> **Load-bearing artifact.** S2–S7 (esp. `normalize.py`, the catalog, the fit
> pipeline) depend on these verbatim shapes. Recorded from a REAL run on this
> machine, not the model card (which documents only `result["answers"]`).
>
> Tracked on branch `laya-decision-integration` via `git add -f` (the plan dir is
> under the repo's gitignored `docs/*`; force-added here so a resuming S2–S7
> session finds it in the worktree). The other plan files (00-master, 01–07) stay
> in the main checkout — read them at their absolute main-repo path.

**Recorded:** 2026-11-19, Apple Silicon macOS, `laya-mlx==0.7.0`, MLX backend.
**Model:** `convaiinnovations/laya` (see "Repo id" below). Install: `uv pip install laya-mlx` → 3 pkgs (`laya-mlx`, `mlx`, `mlx-metal`), **no torch**.
**Probe:** `scripts/decide_probe.py` (committed with this note). ONNX backend **unverified on this machine** — confirm before S4 ships on Linux.

---

## Repo id — PLAN CORRECTION

The plan (master §4, Task 5) says `mlx_repo = "aac6fef/laya-mlx"`. **That is wrong.**
The installed `laya_mlx` defaults to and ships pins for `convaiinnovations/laya`:

```
laya_mlx.load(model_id_or_path='convaiinnovations/laya', ...)   # package default
DEFAULT_MODELS   = {'english': ('convaiinnovations/laya', None),
                    'multilingual': ('convaiinnovations/laya', 'multilingual'),
                    'typed-decisions': ('convaiinnovations/laya', 'typed-decisions')}
PINNED_REVISIONS = {'convaiinnovations/laya': '55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851', ...}
```

`convaiinnovations/laya` loaded and predicted successfully. **Use
`convaiinnovations/laya` for both MLX and ONNX defaults.** `onnx_model` in the
plan was already `convaiinnovations/laya` (correct). Action for Task 5: set
`DecideConfig.mlx_repo = "convaiinnovations/laya"` and fix
`backends.py::DEFAULT_MLX_REPO` (currently `"aac6fef/laya-mlx"`) to match.

## Calibration caveat (carry to S3)

Load emits: `RuntimeWarning: laya: this checkpoint ships temperatures outside
[0.5, 5] ... clamping choice:11+ ... Treat confidence from the affected buckets
as uncalibrated.` The shipped checkpoint is **over-confident / partly
uncalibrated** out of the box (matches `DecisionResult` docstring + master §9).
S3's fit/calibrate pipeline is what earns trustworthy confidence; until then,
treat `answer_confidence` as a raw gate, not a probability of correctness.

---

## Enums

```
QTYPES      = {'choice': 0, 'score': 1, 'noul': 2}
QTYPE_NAMES = {0: 'choice', 1: 'score', 2: 'noul'}
GATE_STATES = ('passed', 'abstained', 'unevaluated')
```

## Question-dict format (input to predict/decide)

```python
questions = {
  "<qid>": {
    "type": "choice" | "score" | "noul",
    "instructions": "… references `state_field` in backticks …",
    # choice: criteria is {label: description|None}
    # score:  criteria is ["0 trivial: …", "1 easy: …", …]   (ordered levels)
    # noul:   NO criteria (yes/no)
    "criteria": {...} | [...],
  },
}
state = "free text"  |  {"state_field": "value", ...}   # instructions ref fields by name
```

## `Agent.predict(state, questions)` → dict

Top-level keys: **`['answers', 'model', 'usage']`** (NOT `{qid: ...}` at top level —
answers are under `result["answers"]`).

```
{
  "model": "laya-rl-agent",
  "answers": { "<qid>": <ANSWER>, ... },
  "usage": {"input_tokens", "output_tokens", "state_tokens",
            "state_tokens_dropped", "truncated", "truncated_questions"},
}
```

### `<ANSWER>` shape per qtype (verbatim)

Common to all: `type`, `probabilities`, `confidence`, `answer_confidence`,
`action: {"act_probability": float}`.

**choice** — the decided label is under key `"choice"`:
```json
{"type":"choice","choice":"code",
 "probabilities":{"code":0.6908,"writing":0.101,"other":0.2083},
 "confidence":0.2592,"answer_confidence":0.6908,
 "action":{"act_probability":1.0}}
```

**score** — the decided level is a FLOAT under `"score"`, with a `"legend"`:
```json
{"type":"score","score":1.1945,
 "legend":{"0":"trivial: a one-liner","1":"easy: short answer","2":"moderate: several steps","3":"hard: long multi-step reasoning"},
 "probabilities":{"0":0.2682,"1":0.2987,"2":0.4036,"3":0.0295},
 "confidence":0.1459,"answer_confidence":0.4036,
 "action":{"act_probability":1.0}}
```

**noul** — value is **P(true)** under `"noul"`; probabilities keyed `"false"`/`"true"`:
```json
{"type":"noul","noul":0.0916,
 "confidence":0.9084,"answer_confidence":0.9084,
 "action":{"act_probability":1.0}}
```
(probabilities dict for noul, from DecisionResult: `{"false":0.9084,"true":0.0916}`)

## `decide(runner, state, *, questions=, return_details=True)` → `DecisionResult`

Dataclass fields and their shapes:

| field | shape |
|---|---|
| `values` | `{qid: <ANSWER>}` (schema-shaped; same as `answers` when `questions=` used) |
| `confidence` | `{qid: float}` — `1 − H(p)/log(k)` for choice/score (laya's docstring calls this "normalized entropy"; verified: difficulty 1−1.184/ln4 ≈ 0.1459); `max(p,1-p)` for noul. NOT the gated quantity — `answer_confidence` is. |
| `probabilities` | `{qid: {label/level/"true"/"false": p}}` |
| `answers` | `{qid: <ANSWER>}` (the raw per-field answer, == predict's `answers`) |
| `answer_confidence` | `{qid: float}` = `max(p)` — **the quantity `min_confidence` gates on** |
| `usage` | token dict (as above) |
| `routing` | **`None`** for plain questions (only populated by `Router`/routing schemas) |

`answer_confidence` value can be `None` for a field with no usable confidence —
**`None` ≠ `0.0`**; treat missing as abstain.

## The abstain / route mechanism — `min_confidence` gate

There is **NO literal `route` or `abstain` key** (master §9's anticipated case).
Pass `min_confidence=<threshold>` to `decide(...)`; each `values[qid]` then gains:

```json
// abstained (answer_confidence 0.6908 < 0.75):
{..., "low_confidence": true, "abstention": "abstained", "abstention_threshold": 0.75}
// passed (answer_confidence 0.9084 >= 0.75):
{..., "abstention": "passed", "abstention_threshold": 0.75}
```

Without `min_confidence`, **no `abstention` key is present** at all.

### Normalizer contract (drives Task 4 `to_decision`)

The engine SHOULD always call `decide(..., min_confidence=<per-question threshold>)`
so `abstention` is populated. Then map laya → `Decision`:

- `value`     ← choice: `answer["choice"]`; score: `answer["score"]` (float);
                noul: `answer["noul"]` (P(true))
- `probabilities` ← `answer["probabilities"]`
- `confidence`    ← `answer["answer_confidence"]` (the gated quantity)
- `abstain`   ← `answer.get("abstention") == "abstained"`;
                **force `abstain=True` if `abstention` key is MISSING or
                `answer_confidence` is None** (never act blind)
- `route`     ← `"auto"` iff `abstention == "passed"`, else `"review"`
- `prediction_set` ← not emitted by laya here → `None` (optionally derive from
                `probabilities` above a mass threshold; defer unless a caller needs it)
- `backend`   ← `"mlx"` | `"onnx"`

Note `action.act_probability` (1.0 here) is a separate HOOK-based act signal
(OptionStabilityHook etc.); with no hooks configured it is 1.0 and is NOT the
gate. The `min_confidence`/`abstention` path is the gate Rudra uses.

## Threading / blocking

`predict` runs a synchronous MLX forward pass (blocking). Task 6 decides whether
the engine calls it in a thread for the async loop. One load → many predicts;
load downloads the checkpoint on first call (~1–2 min cold here, 5 files).
