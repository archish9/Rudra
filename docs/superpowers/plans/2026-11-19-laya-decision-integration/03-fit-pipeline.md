# S3 — Fit-from-Archive Pipeline (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:executing-plans` or
> `superpowers:subagent-driven-development`. Read `00-master.md` first, then this,
> then `NOTES-output-schema.md` and `LABEL-SOURCES.md` (both force-added on the
> `laya-decision-integration` branch).

**Goal:** Turn Rudra's archived run labels into saved laya **calibration**,
loaded by `DecisionEngine` at build. Archive → per-question labelled rows →
`records_from_labeled` (one forward pass each) → `fit_temperatures` (+ optional
`fit_binning`) + `fit_abstention_thresholds` → `save_calibration` + a sidecar
`meta.json`. At engine build, `load_calibration` **iff** the meta matches the
selected backend+model, else zero-shot. Expose `rudra decide fit` /
`rudra decide status`.

**Depends on:** S1 (engine/backends/normalize), S2 (`catalog.py`,
`LABEL-SOURCES.md`, `log.py::record_label`).

**Spec:** `00-master.md` §4 (fit-from-archive paragraph).

---

## PLAN CORRECTION — real laya API (read before coding)

The earlier draft of this plan assumed `agent.fit(rows, questions=)`,
`agent.save_calibration(dir)`, `laya.VegaAgent.min_rows(questions)`, and "a fitted
head that beats the zero-shot engine." **None of that exists** — verified against
the installed `laya-mlx==0.7.0` (`laya_mlx/runtime.py`, `laya_mlx/calibrate.py`).
This is the same class of error S1 caught on the imagined `route`/`abstain` keys;
recorded here so no task reimplements the wrong surface.

**What laya actually is and does:**

- The typed-decision model is **fixed and zero-shot**. There is **no supervised
  head training** from your rows. "Fit" means **confidence calibration**: making
  the `answer_confidence` the `min_confidence`/abstention gate reads trustworthy.
- The real calibration surface on a loaded agent (`runtime.py`):
  - `records_from_labeled(agent, pairs) -> list[Record]` where
    `pairs = [(state, questions, targets)]`. It runs **one forward pass per
    labelled state** to get logits and returns `(qtype, logits, target[:k], k)`
    tuples. (`laya_mlx/calibrate.py:676`.)
  - `agent.fit_temperatures(records, compute_ece=False, seed=0) -> dict` — fits &
    installs per-bucket temperatures. (`runtime.py:1318`.)
  - `agent.fit_binning(records, min_bucket_n=200) -> dict` — optional histogram
    binning on top of temperatures. Floor **200 per bucket** (`MIN_BINNING_BUCKET_N`),
    far above anything a 63-run archive holds — so binning stays **off** until data
    justifies it. (`runtime.py:1333`.)
  - `laya_mlx.calibrate.fit_abstention_thresholds(records, temperature,
    temperature_by_options, *, binning_map=None, target_error=0.10,
    min_bucket_n=MIN_ABSTAIN_BUCKET_N, conservative=True) -> dict` — one
    `min_confidence` cut **per option-count bucket** (e.g. `"choice:3-5"`).
    **Returns `{}` when every bucket is below `min_bucket_n`** — i.e. a tiny
    calibration set yields no gate, and the caller keeps the default threshold.
    (`calibrate.py:511`.)
  - `agent.save_calibration(path)` / `agent.load_calibration(path)` — write/read a
    **single JSON file** (not a dir) carrying `temperature`,
    `temperature_by_options`, `binning_map`, and the checkpoint identity it was
    fitted for. `load_calibration` **warns but still loads** on a checkpoint
    mismatch. (`runtime.py:1345`/`:1380`.)

**The target vector.** `records_from_labeled` wants `targets[qid]` as a vector
over the question's option markers; laya reads the gold option as
`int(np.argmax(target[:k]))` (`calibrate.py:574`). So S3 builds a **one-hot** over
the catalog `criteria` order:
- **choice** → one-hot of width k at the gold label's index in the `criteria` dict
  order.
- **noul** → width-2 `{false, true}` → `[1,0]` (false) or `[0,1]` (true).
- **score** → width-(#levels) one-hot at the gold level index.

**The honest consequence (from `LABEL-SOURCES.md`).** On the current local archive
**no question can be fitted**: only `complexity`/`domain` are cleanly derivable and
both sit below laya's bucket floors; every other decision needs instrumentation
that only accrues once S4–S6 ship. So `rudra decide fit` today will correctly
report **"all zero-shot, nothing served."** That is the right answer, not a
failure. S3's deliverable is the **working, correct pipeline + floor honesty +
staleness guard + CLI**, ready the moment real rows land. Do **not** fake a fit.

---

## Global Constraints

See `00-master.md` §3. Additionally, specific to S3:

- **No hardcoded 35-row floor.** The plan's "35" was a README heuristic; laya's
  real floors are `MIN_ABSTAIN_BUCKET_N` (per-bucket abstention) and `200`
  (binning). **Enforcement = laya's own honesty**: the fitters decline / return
  `{}` on too-little data, and S3 passes that verdict through unchanged. S3 also
  logs a **per-question preflight row count** so "why did it fit nothing" is
  answerable from `.rudra/run/logs/` (§8a).
- **Fit never forces a head.** There is no head; never fabricate temperatures or
  thresholds. If a fitter returns empty/declines, that question stays zero-shot and
  the engine keeps `DEFAULT_MIN_CONFIDENCE`.
- **Binning off by default.** Only fit binning when a bucket clears 200; otherwise
  temperatures + abstention thresholds only.
- **Verify gate absolute:** `.venv/bin/ruff check src/ tests/` → "All checks
  passed!", `.venv/bin/ruff format --check`, `uv run pytest -q` never goes down.
- **S3 wires no Vaayu call site**, so the master live-run rule does not bind here
  (it binds S4–S7). S3's verification is unit tests **plus** a real
  `rudra decide fit` / `rudra decide status` run on the local archive. laya is a
  sidecar: tests must pass **with laya absent** (fit path is skipped/declines
  gracefully), and the real-archive run is done where laya is installed.

## Review Focus

- **Too little data → no gate, not a bad gate.** A question laya's fitters decline
  must stay zero-shot; `should_act` still gates on abstain. (Task 2, Task 3)
- **Weight-free tests.** Unit tests must not download a checkpoint or require laya.
  Fit laya's own fitters with **synthetic records** (per `records_from_labeled`
  docstring: "Tests stay weight-free by passing synthetic records to
  `fit_temperature_map`"), and stub the agent in `build_dataset`/engine tests.
  (all tasks)
- **Stale calibration.** A loaded calibration whose recorded backend+model id does
  not match the selected backend is **ignored** (not merely warned, which is all
  laya does). (Task 3)
- **Target/label correctness.** `targets[qid]` one-hot order must match the
  `catalog.py` `criteria` order exactly, or the gold index is wrong and
  calibration trains on noise. The `state` must match the fields the catalog
  `instructions` reference. (Task 1)

---

## Task 1: Archive → labelled rows builder

**Files:**
- Create: `src/rudra/decide/fit.py` (builder half)
- Test: `tests/decide/test_fit_dataset.py`

**Interfaces — Consumes:** `LABEL-SOURCES.md` verdicts (only `complexity` and
`domain` are archive-derivable today; the rest have no label and are skipped),
`catalog.py` question dicts, the archive layout under
`$XDG_STATE_HOME/rudra/runs/<project>/<run>/` (`ledger.json`, `facts.json`,
`meta.json`). **Produces:**

```python
def build_dataset(runs_dir, question_set) -> list[tuple[state, questions, targets]]
#   state     : the text/dict the catalog instructions reference
#   questions : the subset of question_set that has a gold label for this row
#   targets   : {qid: one_hot_list}  (width = len(criteria); noul => width 2)
def one_hot(index: int, width: int) -> list[float]
def derivable_questions(question_set) -> dict      # only the qids LABEL-SOURCES marks derivable
```

Label derivation, archive-backed (per `LABEL-SOURCES.md`):
- `complexity` (score, 3 levels), per task, **only for `done`/`blocked` tasks**
  (pending/dropped never finished → no outcome signal, skip): proxy from
  `attempts`/`seconds`/`status` — `blocked` or `attempts >= 3` → 2 (hard);
  `attempts <= 1` and `seconds <= 60` → 0 (trivial); else → 1 (normal). The
  `state` is `{"task": <task description>}`. (noisy but real)
- `domain` (choice), per run (applied to every task row of that run): scan
  `facts.json` for a **stack-ish fact** (key containing
  `stack`/`language`/`lang`/`domain`/`framework`, mirroring the S2 audit); map its
  lowercased value to a `_domain_criteria()` PROFILE name by substring → that
  label; a stack-ish fact present but matching no profile → `other`; **no stack-ish
  fact → skip `domain` for that run** (no label — never a wrong `other`).
- Every other qid in `question_set`: **no label → row omitted** for that qid (no
  crash). A malformed run is logged and skipped.

- [ ] **Step 1: Write failing tests** with a fixture of 2–3 fake archived runs:
```python
def test_build_dataset_extracts_state_and_onehot_targets_for_derivable_questions(): ...
def test_rows_skipped_when_label_absent(): ...        # no crash, qid omitted
def test_onehot_order_matches_catalog_criteria_order(): ...
def test_malformed_run_is_skipped_not_raised(): ...
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `build_dataset` / `one_hot` / `derivable_questions`,
      reading only `LABEL-SOURCES.md`-derivable fields. Swallow a malformed run
      (log on `rudra.decide`, skip).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

## Task 2: Fit + calibrate + honest floor (laya's own)

**Files:**
- Modify: `src/rudra/decide/fit.py`
- Test: `tests/decide/test_fit_run.py`

**Interfaces — Produces:**

```python
@dataclass(frozen=True)
class FitReport:
    per_question: dict[str, QFit]   # qid -> rows, served, note
    calibration_written: bool

def fit_calibration(agent, rows, question_set, out_path) -> FitReport
#   1. records = records_from_labeled(agent, rows)            # one fwd pass each
#   2. agent.fit_temperatures(records)                        # installs temps
#   3. thresholds = fit_abstention_thresholds(records, agent.temperature,
#                      agent.temperature_by_options)          # {} when too small
#   4. install thresholds onto the agent only if non-empty
#   5. binning: SKIP unless a bucket clears 200 (document; off today)
#   6. agent.save_calibration(out_path) + write meta.json ONLY if anything served
#      (non-empty thresholds OR fitted temps for a question with enough rows)
```

`QFit` records per question: `n_rows`, `served` (did it get a non-default gate?),
and a `note` (`"served"` / `"declined: too few rows"` / `"no label in archive"`).
Row counts and the served/declined verdict go to the `rudra.decide` logger (§8a).

- [ ] **Step 1: Write failing tests** (synthetic records, NO weights, NO laya
      import — stub the agent and monkeypatch `records_from_labeled` /
      `fit_abstention_thresholds` to deterministic returns):
```python
def test_empty_thresholds_leaves_question_zero_shot(): ...   # {} => served False
def test_served_flag_reflects_nonempty_thresholds(): ...
def test_calibration_written_only_when_something_served(tmp_path): ...
def test_nothing_served_writes_no_file(tmp_path): ...
```
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Pass laya's own served/declined verdict through
      unchanged; never fabricate a threshold; write the file + meta only when
      something was served.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

## Task 3: Load calibration at engine build + staleness guard

**Files:**
- Modify: `src/rudra/decide/engine.py` (`build` loads the calibration when present
  and fresh), `src/rudra/decide/backends.py` (expose selected `model_id` + a
  `load_calibration(path)` passthrough on the backend)
- Test: `tests/decide/test_calibration_load.py`

**Interfaces — Produces:** `DecisionEngine.build` resolves the calibration path
(`cfg.decide.calibration_dir` or the default under `state/paths.py`), reads the
sidecar `meta.json`, and calls `agent.load_calibration(file)` **iff**
`meta["backend"] == selected_backend` AND `meta["model_id"] == selected_model_id`.
Mismatch or absent → zero-shot (no load), logged. The guard is **stricter than
laya**, which only warns on mismatch and loads anyway.

`meta.json` (written in Task 2, beside the calibration file) records: `backend`
(`"mlx"`/`"onnx"`), `model_id` (the repo/revision the agent reports), `fitted_at`
(ISO), and per-question `row_counts`.

- [ ] **Step 1: Write failing tests:** matching meta loads the file; mismatched
      `model_id` is ignored without error (stays zero-shot); absent dir/meta →
      zero-shot; a corrupt meta is ignored, not raised.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement load + the meta round-trip. `build` still never raises.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

## Task 4: `rudra decide fit` / `rudra decide status` CLI + diagnostics

**Files:**
- Modify: `src/rudra/cli.py` (add a `decide` Typer sub-app with `fit` and `status`)
- Test: `tests/decide/test_cli_fit.py`

**Interfaces — Produces:**
- `rudra decide fit [--runs DIR] [--project PATH]` builds the dataset, loads the
  backend, fits, saves; prints the honest per-question report (served / declined /
  no-label) and the preflight row counts. With laya absent or `backend="off"`:
  prints "decision backend unavailable — nothing to fit" and exits 0 (not an error).
- `rudra decide status` prints the active backend, whether a fresh calibration is
  loaded (and why not, if stale/absent), and which questions are served vs
  zero-shot.

Route all numbers through the `rudra.decide` logger too (§8a) so a fit is visible
in `.rudra/run/logs/`.

- [ ] **Step 1: Write failing CLI test** (Typer runner): `fit` on a fixture archive
      runs end-to-end and prints a report (with the agent/laya stubbed); `fit` with
      backend unavailable exits 0 with the unavailable message; `status` prints the
      backend.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit.

## Done criteria (S3)

- [ ] `rudra decide fit` runs on the **real local archive** where laya is installed
      and prints an honest per-question report. Expected today:
      **all zero-shot / nothing served** (per `LABEL-SOURCES.md`) — recorded in the
      "Live verification" note below as the real output, not a failure.
- [ ] A question laya's fitters decline (too few rows) stays zero-shot;
      `should_act` still gates it.
- [ ] Calibration round-trips (save → load) and a mismatched-`model_id`
      calibration is **ignored**, not acted on.
- [ ] Tests pass **with laya absent** (weight-free, agent stubbed); `ruff` clean;
      `uv run pytest -q` not down; `TODO.md` updated (OPEN-175 → DONE, `file:line`
      evidence); committed to the branch, **not merged**.

## Live verification (fill at end of S3)

- [ ] `rudra decide status` output (backend + served/zero-shot): …
- [ ] `rudra decide fit --runs <archive>` per-question report + row counts: …
- [ ] Confirm "nothing served" is the correct, expected result on this archive.

## Handoff

S4–S7 now have: an `engine` that is calibrated-or-zero-shot (transparently),
`catalog.py` questions, `should_act` gating, and `rudra decide status` to see what
is served. They wire one decision each at its call site. Real fits begin accruing
once S4–S6 instrumentation (`log.py::record_label`) lands outcome rows; re-run
`rudra decide fit` then.
