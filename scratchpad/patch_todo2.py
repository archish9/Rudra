import pathlib

p = pathlib.Path("/Users/archish/Documents/ai-ml/Rudra/TODO.md")
t = p.read_text()

old_row = """| 177 | **OPEN-177** (laya S5, #1) — classify a BLOCKED gate failure with laya and take a deterministic recovery instead of re-consulting the planner (`loop/parallel.py:620`); abstain → re-consult; all bounds still fire. Live-verify. | Needs 175; independent of 178, 179. **Removes a Vaayu round-trip.** | `…/05-loop-recovery.md` |"""
new_row = """| 177 | **OPEN-177** (laya S5, #1) — DONE+unit-green+decision-path live-proven (run1b); `recover_or_consult` at both sites, failure_class gated 0.60, missing_dependency→ADD_DEP_TASK / flaky→RERUN_GATE / else→planner; all bounds still fire. **Live close PENDING a stable-Vaayu `acted:true` run + outcomes 2/3.** | Needs 175; independent of 178, 179. **Removes a Vaayu round-trip.** | `…/05-loop-recovery.md`, `…/05a-…md`, `…/05-LIVE-VERIFICATION.md` |"""
assert old_row in t, "table row 177 not found"
t = t.replace(old_row, new_row)
p.write_text(t)
print("row patched")
