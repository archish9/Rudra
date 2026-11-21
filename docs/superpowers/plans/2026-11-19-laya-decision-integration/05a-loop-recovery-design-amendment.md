# S5 / OPEN-177 — Design amendment (loop recovery routing)

> **Status:** approved by owner (archish) 2026-11-21. Amends `05-loop-recovery.md`
> Task 1 + Task 2. Read `00-master.md` §3 §4, `05-loop-recovery.md`, and
> `NOTES-output-schema.md` first. Force-added to the branch like `NOTES-*`
> (plan dir is gitignored in the worktree).

## Why this amendment

`05-loop-recovery.md` Task 1 assumes a "closed set of **existing** deterministic
recoveries (`ADD_DEPENDENCY_TASK`, `DECOMPOSE`, `RERUN_FLAKY`)" to route to.
**They do not exist in the tree** (verified 2026-11-21):

- `loop/dependencies.py::missing_packages` is an attempt **refund** fired in
  `run_task` *before* a task goes BLOCKED (`engine.py:843`, bounded
  `MAX_DEPENDENCY_GATES = 3`). Not a recovery; by BLOCKED it already ran and
  failed.
- `loop/decomposition.py` is a guard that **refuses** an over-split task list at
  `add_tasks`. It does not split a blocked task.
- No flaky/rerun mechanism exists anywhere (`grep flaky|rerun` → only prose).

The planner re-consult exists **because recovery is generative** (it writes new
task text). So S5 cannot "route to existing recoveries"; it must build the few
recoveries that are genuinely deterministic, and re-consult for the rest. This
matches `CLAUDE.md` lesson 3 ("check each plan against the tree anyway").

## Decision (owner): Option 2 — flaky-rerun + dep-task

`recovery_route(engine, blocker_text, *, packages) -> RecoveryAction | None`
maps **only** two classes to a real deterministic recovery; every other class,
abstain, review, or unavailable → `None` (re-consult `breakdown` exactly as
today — the safe direction).

| `failure_class` (laya `choice`) | Confident → action | Rationale |
|---|---|---|
| `flaky_or_environment` | `RERUN_GATE` — re-run `_verify` once, no coder, no planner | Only class with a recovery that is *purely* deterministic. Same failure → same signature → existing **C6.5a** blocks it; laya cannot loop past the bounds. |
| `missing_dependency` | `ADD_DEP_TASK` — synthesize one task "declare & install `<pkg>`" from the parsed package name | Coder holds no shell (OPEN-36); the refund gave it 3 shots to *declare* and it stayed BLOCKED. Package name is **data** (parsed), not generated. Only when a name is parsed. |
| `wrong_approach` | `None` → re-consult | Recovery is generative (new design). Python can't; that's the planner. |
| `needs_decomposition` | `None` → re-consult | Same — splitting needs new task text. |

**`ADD_DEP_TASK` guard — never act blind.** The package name comes from
`loop/dependencies.py::missing_packages(report, is_local)` (the same parser the
refund uses), **not** from laya. If it returns empty (truncated tail / mixed
failures), `recovery_route` returns `None` → re-consult. So `ADD_DEP_TASK` fires
only when (a) laya is confident `missing_dependency` AND (b) a concrete package
name exists. laya classifies; the name is Rudra's own data.

## Signal fed to laya (determinism)

The `gate_output` state for `FAILURE_CLASS` is the **same blocker text the loop
already computes** (`loop/engine.py::_blocker_text`), masked exactly as
`loop/bounds.py::_exception_lines` masks it (abs paths / memory addresses). No
new text source. `fits_budget` (catalog) refuses the laya call if it overflows
512 → `None` → re-consult.

## Both call sites (plan named one)

The blocked re-consult is **two identical sites**, both wired the same way:
- `loop/parallel.py:620` — parallel batch path (plan named this).
- `loop/engine.py:2254` — sequential `work()` path.

A shared helper does recovery-or-reconsult; both sites call it. Wiring one and
not the other half-ships the feature.

## Mechanisms (all exist; no new plumbing)

- Engine: `LoopContext.decide` already carries the run's `DecisionEngine`
  (`engine.py:145`, wired by S4). No new context field.
- Rerun: `_verify(task, context)` (`engine.py:614`) re-runs the gate off-thread,
  returns a `VerifyReport`; `.passed` → task DONE via the normal verdict path.
- Dep-task: `ledger.add_task(desc)` inside the **existing** `ledger.unblocking(
  task.id)` window (`ledger.py:162/181`) → the task carries `unblocks` and is
  worked NEXT, preserving the OPEN-158 invariant the planner path already has.

## Bounds that MUST still fire (unchanged, tested)

- **C6.5a** two-identical-signatures → BLOCKED (a rerun/dep-task that doesn't
  converge is caught here; laya cannot bypass it).
- `MAX_BLOCKED_CONSULTS` (the re-consult cap) — a recovery that re-blocks still
  counts toward it.
- `MAX_DEPENDENCY_GATES` refund in `run_task` — untouched; `ADD_DEP_TASK` is a
  separate task, not a refund.
- `loop/regressions.py` time-comparison DONE rule — untouched.

## Non-goals / anti-goals

- No recovery for `wrong_approach` / `needs_decomposition` (generative → planner).
- Laya never *causes* a recovery that wouldn't have been safe; it only *shortcuts*
  the planner for a class with a real deterministic recovery (master §3).
- Every decision writes a `rudra.decide` log line (`decide/log.py::record`),
  acted or fell-back (§8a). A decision held only in memory does not exist.

## Scope delta vs `05-loop-recovery.md`

- **Task 1** `recovery_route` lives in `decide/gates.py` (beside `skip_clarify`),
  returns a `RecoveryAction` enum ∈ {`RERUN_GATE`, `ADD_DEP_TASK`} or `None`.
  Takes `packages: tuple[str, ...]` (from `missing_packages`) so the decide layer
  never imports the loop. New tests `tests/decide/test_gate_recovery.py`.
- **Task 2** shared helper in `loop/` wired at **both** sites; `RERUN_GATE` and
  `ADD_DEP_TASK` applied in the loop (which owns `_verify` and the ledger). New
  tests `tests/loop/` (or `tests/decide/test_recovery_wiring.py`), plus a bounds
  test that C6.5a still forces BLOCKED with laya active.
- **Task 3** live Vaayu run unchanged: dep-block skips planner + recovers; a
  block laya can't classify → planner still runs; `backend = "off"` → identical
  to today.

## Done criteria (unchanged from 05 §Done)

Planner re-consult skipped only for `flaky_or_environment`/`missing_dependency`
when confident and (dep) a package is parsed; all bounds still fire; abstain →
re-consult; both sites wired; three live outcomes recorded; `ruff`/`pytest`
green; `TODO.md` updated; committed, not merged.
