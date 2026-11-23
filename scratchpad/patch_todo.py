import pathlib

p = pathlib.Path("/Users/archish/Documents/ai-ml/Rudra/TODO.md")
t = p.read_text()

old_next = """**Next: OPEN-177** (laya S5). **Owner priority: the laya decision-model
integration (OPEN-177 … OPEN-179 + OPEN-182; S1/OPEN-173, S2/OPEN-174, S3/OPEN-175, the OPEN-180 run-gate and S4/OPEN-176 now DONE) is
the most important work and must be finished before anything else in this ledger.** Complete the whole laya track in order"""
new_next = """**Next: OPEN-178** (laya S6). **Owner priority: the laya decision-model
integration (OPEN-177 … OPEN-179 + OPEN-182; S1/OPEN-173, S2/OPEN-174, S3/OPEN-175, the OPEN-180 run-gate and S4/OPEN-176 now DONE) is
the most important work and must be finished before anything else in this ledger.**
**OPEN-177 (S5) is CODE-COMPLETE + unit-green + decision-path live-proven on Vaayu
(run1b); stays open PENDING a full end-to-end `acted=true` capture + outcomes 2/3
on a stable Vaayu window (3 of 4 live runs died on Vaayu instability).**
Complete the whole laya track in order"""
assert old_next in t, "next-block not found"
t = t.replace(old_next, new_next)

old_177 = """### OPEN-177 — S5 #1 loop-recovery routing (removes the block re-consult Vaayu call) — PENDING

**Needs 175; independent of 176, 178, 179.** On a BLOCKED task, classify the gate
failure with laya `choice` and take a deterministic existing recovery instead of
re-consulting the `breakdown` planner (`loop/parallel.py:620`); abstain → re-consult
as today. All existing bounds (C6.5a, `dependencies.py`, `regressions.py`) stay and
must still fire. **Ends with a live Vaayu run** (skip, no-skip, backend-off). Plan:
`docs/superpowers/plans/2026-11-19-laya-decision-integration/05-loop-recovery.md`."""
new_177 = """### OPEN-177 — S5 #1 loop-recovery routing (removes the block re-consult Vaayu call) — PENDING (live close only)

**STATUS: code DONE + unit-green (4052 pass) + decision-path LIVE-PROVEN on Vaayu;
full end-to-end `acted=true` + outcomes 2/3 PENDING a stable Vaayu window.**
Built on branch `laya-decision-integration` (unmerged), commits
`a12f272`(design) `b1fcff9`(Task1) `c54dc22`+`668de46`(Task2) `f6406bd`(per-question
threshold) `ebe65a4`(collection-error fix) `75437b5`(live note).

Owner chose **Option 2** (design amendment `05a-…`): on a BLOCKED task
`recovery_route` maps **only** `flaky_or_environment`→RERUN_GATE and
`missing_dependency`(+parsed pkg)→ADD_DEP_TASK; every other class / abstain /
review / unavailable → re-consult `breakdown` as today. Wired at **both** sites
(`loop/engine.py` work() `can_rerun=True` + `loop/parallel.py` `can_rerun=False`)
via `recover_or_consult`. All bounds (C6.5a, `dependencies.py`, `regressions.py`)
still fire — laya acts only AFTER a task is BLOCKED. `failure_class` gated at a
per-question **0.60** (the calibrated checkpoint rates a clear missing-module gate
~0.61; at 0.75 it never acts); flaky(~0.28)/wrong_approach(~0.32) still abstain.

**Live (`05-LIVE-VERIFICATION.md`):** run1b on Vaayu reached a BLOCKED task and
logged `failure_class=missing_dependency conf=0.60 route=auto` + the safe
no-package fallback to the planner. The `acted=true` ADD_DEP_TASK leg is proven on
run1b's REAL block data + real laya via the collection-error fix. 3 of 4 full runs
died on Vaayu instability (502 / 300s timeout / stream failure) before a clean
block. **Close only after one stable-Vaayu run shows `decision … acted:true` + an
ADD_DEP_TASK task (`unblocks`) with the planner re-consult skipped, a non-dep block
→ planner (outcome 2), and `backend="off"` → planner (outcome 3). Fixtures ready at
`~/rudra-verify/open177-dep` and `~/rudra-verify/open177-off`.** Plan:
`docs/superpowers/plans/2026-11-19-laya-decision-integration/05-loop-recovery.md`
(+ `05a-…-design-amendment.md`, `05-LIVE-VERIFICATION.md`)."""
assert old_177 in t, "177-entry not found"
t = t.replace(old_177, new_177)

p.write_text(t)
print("TODO.md patched")
