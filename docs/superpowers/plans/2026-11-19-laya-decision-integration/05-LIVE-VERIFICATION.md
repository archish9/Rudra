# S5 / OPEN-177 — Live verification note (2026-11-21)

> Recorded on branch `laya-decision-integration` (force-added like
> `NOTES-output-schema.md` / `04-LIVE-VERIFICATION.md`). Pairs with
> `05a-loop-recovery-design-amendment.md`.

**Status: decision path PROVEN live on Vaayu (run1b); the `acted=True`
ADD_DEP_TASK leg is proven on run1b's REAL block data + real laya but was not
captured in one uninterrupted Vaayu end-to-end (Vaayu instability); outcomes 2
(abstain→planner) and 3 (backend=off) not yet separately run.** OPEN-177 stays
PENDING live per `no-close-until-live-verified`, same pattern as OPEN-167 /
OPEN-182-ONNX.

## What is wired (all unit-green, self-reviewed)
- `recovery_route` gate (`decide/gates.py`): `flaky_or_environment`→RERUN_GATE,
  `missing_dependency`(+parsed pkg)→ADD_DEP_TASK, else None (re-consult).
- Both block-consult sites route through `recover_or_consult`
  (`loop/engine.py` work() `can_rerun=True`; `loop/parallel.py` `can_rerun=False`).
- Per-question act threshold: `failure_class` → 0.60 (the calibrated checkpoint
  rates a clear missing-module gate ~0.61; 0.75 would never act). Only
  `missing_dependency` clears it; flaky(~0.28)/wrong_approach(~0.32) still
  abstain→planner.
- Collection-error package extraction (`missing_modules_in_text`): the common
  missing-dep block is a pytest COLLECTION ImportError (outcome ERROR), which
  `missing_packages` excludes; the fallback names the package from the block's
  own masked exception text.

## Project
`~/rudra-verify/open177-dep` (brownfield, green baseline). `.rudra/config.toml`:
`provider=vaayu`, `base_url=https://a.taila2bfc0.ts.net/v1`, `model=vaayu`,
ctx 16384, `max_questions=0`, `max_fix_attempts=2`, shell on, `[decide]
enabled=true backend=auto`. Request forces a task-introduced import of the
non-existent `rudra_fx_client` and forbids declaring it, so the gate stays red
on the missing module. Driven with `rudra --auto --allow-shell --stream`.

## run1b (debug-e77e2c56698b.jsonl) — the full flow, PROVEN live
t1 reached BLOCKED (C6.5a, "same failure twice") on the real Vaayu run. The
`"kind":"decision"` record is the headline evidence:

```
question_id=failure_class value=missing_dependency confidence=0.6014
abstain=False route=auto acted=False backend=mlx
```

- laya was called **after** the block, classified the cause **correctly**
  (`missing_dependency`), and cleared the 0.60 gate (route=auto) — the
  per-question threshold works live.
- The decision was **logged** to `.rudra/run/logs/` (§8a satisfied).
- `acted=False` because `block_packages` was empty: t1's final gate was a
  **collection ImportError** (`collected 1 item / 1 error`,
  `ModuleNotFoundError: No module named 'rudra_fx_client'`), outcome ERROR, which
  `missing_packages` excluded. `recover_or_consult` therefore **refused to act
  blind** and re-consulted the breakdown planner, which added t2 (`unblocks=t1`).
  This is the "never act without a concrete package" guard **working live**.
- The dependency-refund path also fired live earlier
  (`dependency-gate: t1 … rudra_fx_client … 1 of 3`).

run1b's `t1.block_gate_output` (verbatim):
`"ModuleNotFoundError: No module named 'rudra_fx_client'"`.

## The fix verified on run1b's REAL data (scratchpad/probe_run1b_recovery.py)
Feeding run1b's exact `block_gate_output` through the post-fix path, real laya
(MLX), real config:
```
missing_modules_in_text(...) -> ('rudra_fx_client',)
recovery_route(...)          -> RecoveryAction.ADD_DEP_TASK
```
So the exact gap run1b exposed is closed: that same live block now yields
`ADD_DEP_TASK` (acted), the planner re-consult skipped.

## Vaayu instability (why the clean acted=True end-to-end is still PENDING)
`rudra models test` → ok, but **32.6 s/call** and intermittent failures. Three
of four full runs died on the endpoint, never reaching a clean block:
- run1 (`66cb42e86f33`): coder+reviewer `InternalServerError (502)`
  retry-exhausted ~42 min in (after the dep-refund fired).
- run1c (`7ba986c17e38`): planner `StreamChunkTimeoutError`/`APITimeoutError`
  (300 s) ×4 in planning → gave up, 0 tasks.
- run1d (`fc4907054d86`): planner streaming failure mid-plan (not retried) → 0 tasks.

This is the known Vaayu-instability blocker (memory `vaayu-needs-streaming`;
OPEN-167 is stuck on the same). The laya code is not implicated in any failure.

## What remains for a full close
On a stable Vaayu window, one run that reaches a missing-dependency BLOCK and
shows `decision … acted:true` + an `ADD_DEP_TASK` task inserted with
`unblocks` + no breakdown re-consult; plus a non-dep block showing
abstain→planner (outcome 2); plus `[decide] backend="off"` showing
`backend:unavailable` → planner (outcome 3, fixture `~/rudra-verify/open177-off`
ready). Re-run freely (owner rule).

## CLOSED 2026-11-23 — all three outcomes captured live on Vaayu
A stable Vaayu window was reached. Each outcome was captured on the real
loop + real laya (MLX), `--auto --allow-shell --stream`, `timeout=600`.
Fixtures hardened so the agent cannot "solve" the block (package + tests set
read-only via `[permissions] deny`, including `execute:*` — a bare `edit_file:*x*`
does NOT match `dir/x.py` because wcmatch `*` will not cross `/`; use
`dir/**` or `**/*x*`).

**Outcome 1 — missing-dependency block → ADD_DEP_TASK, planner skipped**
(`~/rudra-verify/open177-dep`, run `debug-43c6c64982a5`):
```
decision: {question_id: failure_class, backend: mlx, value: missing_dependency,
           confidence: 0.6014, abstain: false, route: auto, acted: true}
ledger:   t1 BLOCKED  block_packages=['rudra_fx_client']
          t2 DONE  unblocks='t1'  (the CANNED recover_or_consult text:
            "The gate for task t1 failed only because these packages are not
             installed: rudra_fx_client. Declare them ...")
breakdown re-consults for the block = 0  (Vaayu round-trip removed)
```

**Outcome 2 — non-dependency block → abstain → planner re-consult**
(`~/rudra-verify/open177-logic`, TypeError in read-only `mypkg/client.py`,
run `run2f`):
```
decision: {question_id: failure_class, value: wrong_approach,
           confidence: 0.3665, abstain: true, acted: false}
ledger:   t1 BLOCKED;  t2 DONE unblocks='t1' = a PLANNER task
          ("Write get_rate ... return 2.0"), NOT the canned string.
client.py unchanged (deny held) — the block was genuine, not agent-resolved.
```

**Outcome 3 — `[decide] backend="off"` → laya never consulted → planner**
(`~/rudra-verify/open177-off`, same missing-import block, run `run3b`):
```
decisions logged = 0   (engine.available() is False under backend="off")
ledger:   t1 BLOCKED;  t2 DONE unblocks='t1' = a PLANNER task
          ("Install the project in editable mode ..."), canned string absent.
```

Contrast is the whole proof: the *same* missing-import block yields the canned
ADD_DEP_TASK with the planner skipped when laya is on (outcome 1) and a planner
re-consult when laya is off (outcome 3); a block laya is not confident about
(outcome 2) always re-consults. OPEN-177 CLOSED.

Vaayu stayed flaky through the close (several runs still died mid-stream on
`RemoteProtocolError`/`ProviderUnavailable`/`StreamChunkTimeoutError`); the
captures above are from the runs that reached the block before a drop. The
laya code is not implicated in any Vaayu failure. See OPEN-183 for the
slow-decode timeout fix.
