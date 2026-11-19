# LABEL-SOURCES — S2 Task 2 archive label audit

> **Verdict per decision, backed by real archived runs** (not assumption). This
> file DRIVES S3's scope and the order S4–S7 ship: `derivable now` decisions can
> fit from history; the others ship **zero-shot + abstain→Vaayu** and fit later
> once instrumentation rows accrue (S2 Task 3).
>
> Force-added on branch `laya-decision-integration` (the plan dir is gitignored).

**Audited:** 2026-11-19. Tool: `scripts/decide_audit_archive.py` (read-only).
**Archive root:** `$XDG_STATE_HOME/rudra/runs` → `~/.local/state/rudra/runs`.

## Corpus (what exists locally)

| metric | count |
|---|---|
| runs | 63 |
| tasks | 288 |
| done / blocked / (pending / dropped) | 101 / 34 / (138 / 15) |
| runs where clarify asked (`ask_user`, role=planner) | 9 |
| blocked tasks with a `note` cause signal | 34 / 34 |
| tasks carrying `unblocks` | 8 |
| **`unblocks` tasks whose target resolved to `done`** | **0** |
| runs where a `tester` subagent ran (`subagent_done` role=tester) | 17 |
| tasks with both `attempts` and `seconds` | 288 / 288 |
| runs with a stack/language/domain fact | 27 / 63 |
| MCP meta-tool calls (`list/describe/call_mcp_tool`) | **0** |
| skill→outcome attribution records | **0** |

Per-run files present: `ledger.json`, `facts.json`, `meta.json`,
`permissions.jsonl`, `verify.log`, `tests.log`, `usage.json`,
`transcript.jsonl`, `debug-<id>.jsonl`.

---

## #2 `needs_clarification` (noul) — **needs instrumentation**

- **Input signal:** present. `greenfield` is recomputable; whether clarify
  *asked* is in the debug log as `ask_user` tool_calls with `role=planner`
  (9 runs).
- **Label (did asking MATTER?):** **not in the archive.** Nothing records the
  counterfactual — whether the plan/outcome differed from not asking. Using "a
  question was asked" as the label trains laya to imitate today's clarify
  trigger, not to judge ambiguity (label leakage — plan Review Focus).
- **Verdict:** ship zero-shot + abstain→Vaayu. **Instrument (Task 3):** on each
  clarify decision, log `decision_label` with `(greenfield, asked, questions_n,
  run_converged)` so a real label accrues.

## #1 `failure_class` (choice) — **needs instrumentation**

- **Input signal:** rich. 34/34 blocked tasks carry a `note` (the gate blocker
  text); many also carry `halts`, `convergence`, `dependency_gates`,
  `run_errors`. The cause is readable by eye.
- **Label (recovery OUTCOME — did the chosen recovery converge?):** **absent.**
  The plan's Review Focus is explicit: the label is the recovery outcome, not
  the planner's re-consult text and not the raw cause. The archive has **0 of 8**
  `unblocks` tasks resolving to a `done` target — i.e. zero positive
  recovery-converged examples. There is also no clean categorical cause-class
  field; `convergence` is mostly `None`/`""` (2 `converging` among 34 blocked).
- **Verdict:** ship zero-shot + abstain→Vaayu. **Instrument (Task 3):** when S5
  classifies a blocked task and takes a recovery, log `decision_label` with
  `(cause features, chosen_recovery, converged)` — that is the only path to a
  fittable label.

## #3 triage — **mixed**

- **`complexity` (score): derivable now.** Every task (288/288) has `attempts`
  and `seconds`; a high-attempt / high-seconds / BLOCKED task is a hard-complexity
  proxy. Fittable from the archive (noisy but real). *Caveat (S6 Review Focus):*
  `complexity` may only ever **raise** the fix budget, never starve a task below
  the floor — a wrong label must not block a hard task early.
- **`domain` (choice): derivable when present.** 27/63 runs carry a stack fact;
  stack is also recomputable from the project tree. Fittable with partial
  coverage.
- **`needs_tests` (noul): needs instrumentation.** `subagent_done` records
  `role=tester` (17 runs) but carries **no task id**, so "did the tester run for
  THIS task, and did its tests reach the gate" is not cleanly per-task derivable.
  **Instrument (Task 3):** log `decision_label` at coder dispatch with
  `(task, tester_ran, tests_in_gate)`.

## #4 `mcp_tool` / `skills_topk` (choice) — **zero-shot only / needs instrumentation**

- **Input signal:** thin. **0** MCP meta-tool calls in the whole archive (MCP was
  not configured in these runs), so there is no record of which tool was picked,
  let alone whether it helped. Skills are inlined (OPEN-168) with **no**
  skill→outcome attribution anywhere.
- **Verdict:** **zero-shot only** today; hints are advisory and never remove a
  capability (keeps the OPEN-168 "Rudra inlines, model does not pull" invariant),
  so acting zero-shot is safe. **Instrument (later, S7-adjacent):** attribute an
  outcome to a selected tool/skill before any fit. No Task 3 stub lands now — the
  call site (S7) does not exist yet, and a label with no decision point is noise.

---

## What this drives

- **S3 fit pipeline:** only `complexity` and `domain` have enough signal to fit
  from the current archive (and both below laya's 35-row floor for most
  questions → they stay zero-shot until rows accrue). Everything else ships
  zero-shot + abstain→Vaayu. S3's 35-row gate is what protects all of them.
- **S4–S7 order:** no decision is "fit-ready" today; every one ships with the
  abstain→Vaayu safety path, which S1 already guarantees. The ordering in
  `TODO.md` (176 clarify, 177 recovery, 178 triage, 179 mcp/skill) stands —
  fit-readiness is not the gate, the abstain path is.
- **Task 3 stub (landed):** `decide/log.py::record_label` — one swallow-failure
  `kind="decision_label"` emitter (metadata only) for #2, #1, and #3
  `needs_tests`. It is the shared primitive those decisions' sessions call **at
  the point the label is knowable**, because every one of those labels is a
  recovery/clarify/dispatch OUTCOME that does not exist until S4–S6 build the
  decision site (0 outcome rows exist today). Wiring a speculative emission into
  `planner_agent.py` / `engine.py` / `parallel.py` now would add input-only rows
  that cannot be fit and churn files S4–S6 own; the tested primitive is the
  correct stub. No stub for #4 (no call site yet) or #3 `complexity`/`domain`
  (already derivable from the archive).
