# S6 / OPEN-178 — Live verification note (2026-11-23)

> Recorded on branch `laya-decision-integration` (force-added like
> `NOTES-output-schema.md` / `05-LIVE-VERIFICATION.md`; the plan dir is under the
> repo's gitignored `docs/*`). Pairs with `06-pre-dispatch-triage.md`.

**Status: all four Task-3 legs PROVEN live on Vaayu with real MLX laya.** The
pre-dispatch triage removes the tester subagent on a confident-no task, keeps it
on a confident-yes task, and is a no-op with `backend="off"`. Net reduction
measured on one identical request.

## What is wired (unit-green: 140 decide + 274 decide/loop; full suite 4069 pass)
- `triage(engine, task_text) -> Triage` (`decide/gates.py`): `needs_tests` (noul)
  -> `skip_tests` only when `should_act` AND P(true) < 0.25; `complexity` (score)
  -> `extra_attempts` delta (0 unless confident-hard, raises the budget only,
  never starves); `domain` (choice) -> label. Abstain/unavailable -> safe default.
- Wired at the top of `loop/engine.py::run_task`: built once off the event loop
  (`asyncio.to_thread`), feeds the fix budget, gates the tester dispatch. The
  deterministic verify gate runs regardless of `skip_tests`.
- `needs_tests` training-label row DEFERRED (ledgered ruling): on the skip path
  its only signal is a function of triage's own decision = self-confirming leak;
  LABEL-SOURCES §#3 gives it 0 fittable rows today.

## Projects
Greenfield, driven with `rudra --auto --allow-shell --stream`, `provider=vaayu`,
`base_url=https://a.taila2bfc0.ts.net/v1`, `model=vaayu`, ctx 16384,
`max_questions=0`, `max_fix_attempts=2`, `timeout=600` (OPEN-183 workaround),
shell on.
- `~/rudra-verify/open178-skip` — request: "Create a single file named LICENSE …
  the full text of the MIT License. That is the only work."
- `~/rudra-verify/open178-code` — request: "Implement a function fib(n) in fib.py
  … Do not write any tests yourself."

## Run 1 — skip leg, `backend="auto"` (laya on). Step 1 ✓
`run 4fe2e7f73940`. The `"kind":"decision"` records (logged to
`.rudra/run/logs/`, §8a satisfied):
```
needs_tests val=0.068  conf=0.932  acted=True   abstain=False
complexity  val=0.9016 conf=0.375  acted=False  abstain=True
domain      val=node   conf=0.193  acted=False  abstain=True
```
- Gate GREEN, 1 task DONE, 1 file (`LICENSE`).
- `usage.json` roles = **planner, coder, reviewer** — **NO tester**. The gate
  reached green with no test judgement (the state that normally dispatches the
  tester); the only reason it did not fire is `skip_tests`, and the decision
  log proves `needs_tests acted=True`.

## Run 3 — needs-tests leg, `backend="auto"` (laya on). Step 2 ✓
`open178-code`. Decisions:
```
needs_tests val=0.9227 conf=0.923 acted=False abstain=False
complexity  val=0.9771 conf=0.365 acted=False abstain=True
domain      val=python conf=0.515 acted=False abstain=True
```
- laya is confident the code task DOES need tests (P=0.92 >= 0.25 margin), so
  `skip_tests` is False.
- `usage.json` roles = planner, coder, **tester**, reviewer — the tester RAN
  (4 calls, 270.1s, 28,295 in). 1 task DONE, 2 files (fib.py + the tester's test).

## Run 2c — identity leg, `backend="off"`. Step 3 ✓
`open178-skip`, same LICENSE request, `[decide] backend="off"`.
- `laya decision records: 0` — triage made no call (engine unavailable ->
  `Triage.safe()`), today's path exactly.
- `usage.json` roles = planner, coder, **tester**, reviewer — the tester RAN
  (1 call, 144.2s, 6,743 in). 1 task DONE.

## Step 4 — net reduction (the whole point) ✓
**Same request, same project (`open178-skip`), only `[decide] backend` flipped:**

| | roles | tester | tester cost |
|---|---|---|---|
| Run 1 `backend=auto` (triage) | planner, coder, reviewer | **skipped** | 0 |
| Run 2c `backend=off` (today)  | planner, coder, **tester**, reviewer | ran | 1 call, 144.2s, 6,743 tok |

Triage ON removes an entire subagent invocation (one Vaayu model call, ~144s,
~6.7k tokens on this run) for one ~100 ms local laya forward pass. A net
reduction in calls AND subagents on a real request. The laya call that bought
the saving is the single logged `needs_tests acted=True` decision in Run 1.

## Honest notes
- Vaayu was intermittently unstable during this session (two backend=off attempts
  died mid-planner on `RemoteProtocolError` / `502 ProviderUnavailable` before
  Run 2c landed clean — OPEN-183 territory, not a triage bug). All four legs are
  from clean end-to-end runs.
- `complexity`/`domain` abstained on every live task (zero-shot, below the 0.75
  gate — expected per LABEL-SOURCES; they stay zero-shot until rows accrue). The
  budget-raise path (`extra_attempts`) is therefore unit-proven, not yet seen
  acting live; it only ever RAISES the budget, so an abstain is the safe no-op.
