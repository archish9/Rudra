# S4 / OPEN-176 — Live verification note (2026-11-20)

> Recorded on branch `laya-decision-integration` (the main-checkout `04-clarify-gating.md`
> cannot be edited from this worktree). Force-added like `NOTES-output-schema.md`.

**Status: VERIFIED end-to-end on Vaayu (2026-11-21).** The 2026-11-20 MLX-only
proof below stands; the end-to-end `rudra --plan` runs that were PENDING an
endpoint have now run against Vaayu and pass all three paths. OPEN-176 meets
`no-close-until-live-verified`.

## End-to-end live run — Vaayu, 2026-11-21
Vaayu back up (`https://a.taila2bfc0.ts.net/v1`, `http_code=200`; `rudra models
test` → Construct/Reach/Tools ok, 26.2s/call, ctx 16384). Driven under the
`scratchpad/live_run.py` pty harness (`interactive=True`) with `--plan --stream`
against the greenfield project `~/rudra-verify/open176-skip` (`max_questions=5`).
The `"kind":"decision"` record in each run's `debug-<id>.jsonl` is the headline
evidence:

| Run | Request | `[decide] backend` | decision record | clarify stage |
|---|---|---|---|---|
| 7f736a03118e | "create a Python script that prints the numbers 1 to 100" | auto→mlx | `backend:mlx, abstain:false, value:0.0952, conf:0.9048, acted:true` | **SKIPPED** — `clarify-gate` NOTICE "clarify skipped: laya judged the request unambiguous"; 0 `ask_user`; a 1-task plan still produced |
| b96112db149c | "make it better" | auto→mlx | `backend:mlx, abstain:true, route:review, value:0.2761, conf:0.7239, acted:false` | **RAN** — no skip NOTICE; 2 `ask_user` calls |
| bde222ce1b2d | "create a Python script that prints the numbers 1 to 100" | off | `backend:unavailable, abstain:true, value:null, acted:false` | **RAN (not skipped)** — no skip NOTICE even on a maximally-clear request, because `should_act` requires `backend != unavailable` |

Proves the full gate matrix live: confident-not-ambiguous → skip the Vaayu
clarify round-trip; abstain → clarify runs; backend off → clarify runs. Run 3
was wall-capped at 540s mid-clarify (the decision is logged at gate time, before
the cap), which is why it shows 0 `ask_user`; the load-bearing fact is the absent
skip NOTICE + `backend:unavailable`.

## Environment
`laya-mlx==0.7.0` installed into the dev `.venv` (sidecar, NOT in `uv.lock`; a
later `uv sync` drops it). MLX backend, `convaiinnovations/laya`, **zero-shot** —
no calibration, because MLX fit needs torch (excluded, master §3) and
`needs_clarification` has 0 archive rows. `rudra decide status` → all zero-shot.

## What ran — real MLX, default thresholds, no manipulation
`min_confidence=0.75`, `AMBIGUITY_MARGIN=0.25`.

- Engine builds + decides in ~0.05–0.11 s/call.
- `skip_clarify` → **True (skip)** at the real gate on genuinely-clear requests:
  - "create a Python script that prints the numbers 1 to 100" — P(true)=0.095, conf=0.905
  - "build a command-line calculator supporting + - * /" — P(true)=0.119, conf=0.881
  - "write a function that reverses a string, with tests" — P(true)=0.100, conf=0.900
- `skip_clarify` → **False (abstain → today's Vaayu clarify)** on vague requests:
  - "make it better" — P(true)=0.276 (abstain)
  - "add dark mode" — P(true)=0.254 (abstain)
  - "build a flask hello world app" — P(true)=0.519 (abstain) — an uncalibrated
    OUTLIER: the shipped checkpoint rates this maximally-clear request as 52%
    ambiguous (see `NOTES-output-schema.md` calibration caveat). Earlier reading
    that laya "always abstains" was skewed by this one sample; clearer/narrower
    requests clear the safe gate zero-shot.

Conclusion: the gate is **not inert** — some clear requests skip at the safe 0.75
gate today. Calibration (when torch-at-fit + rows accrue) would help the
borderline band, not enable the feature.

## (HISTORICAL — 2026-11-20) What had NOT run then — now done, see the Vaayu section above
Showing the clarify Vaayu turn actually removed in `.rudra/run/logs/`. Blocked:
no reachable generative backend on 2026-11-20.

- **Vaayu** host OFFLINE — tailscale node `a` (100.94.62.117) "offline, last seen
  1d ago"; `https://a.taila2bfc0.ts.net` curl → `http_code=000` (timeout).
- **NVIDIA** — `nemotron-3-super-120b-a12b` is `410 Gone` (memory stale);
  `nemotron-3-ultra-550b-a55b` is reachable and tool-calls, but Rudra sends
  `session_id` to every `openai_compatible` provider (`providers.py:169
  accepts_session_id=True`, `factory.py:133`) and NVIDIA `400`s
  `Unsupported parameter(s): session_id`. Filed as **OPEN-181**.

## (HISTORICAL) The procedure that was followed — ran 2026-11-21, results above
Run under a pty (so `interactive=True`; `--auto` forces `interactive=False` →
gate never fires) with `--plan` (runs plan() incl. the clarify gate, then stops —
no writes):
1. a clear request ("create a Python script that prints 1 to 100") → confirm the
   NOTICE "clarify skipped", a `decision` debug line with `acted:true`, and NO
   planner `model_call` for stage `clarify`.
2. "make it better" → clarify runs: `decision` `acted:false` + a clarify
   `model_call`.
3. `[decide] backend="off"` → clarify runs; decision `backend:"unavailable"`.

Harness used for the pty: `scratchpad/live_run.py` (pty fork + idle-newline so a
no-skip clarify ask cannot hang + a wall-time cap).
