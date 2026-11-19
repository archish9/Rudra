# OPEN-180 — laya run-entry requirement gate (Implementation Plan)

> Tracked on branch `laya-decision-integration` (force-added; plan dir is gitignored).
> Owner decision 2026-11-19: **laya is a HARD run requirement**, not optional.

## Intent

Rudra must not start a RUN without laya installed. When `[decide] enabled` is true
(default) and laya is not importable for this OS, `RudraAgent.run()` stops BEFORE planning
and tells the user to read `Documentation/16-laya.md` (why required + per-OS install). The
gate is **additive** — the engine's own graceful degradation (`DecisionEngine.build` never
raises; abstain→Vaayu) is unchanged; this is a new precondition at run entry only.

## Scope (owner-chosen)

- **Blocks a run only.** `--version`, `doctor`, `init`, `config`, `mcp`, `memory` keep
  working (they build no `RudraAgent`), so a user can bootstrap and reach the guidance.
- **`[decide] enabled = false`** is the one opt-out: Rudra runs without laya, always Vaayu.
- **`backend = "off"` does NOT lift the requirement** — laya must still be installed;
  `off` only means installed-but-unused.
- Ruling (mine, owner may override): `enabled=false` lifts the gate because requiring an
  install for a fully-disabled layer is user-hostile.

## Global constraints

Python 3.12+; capability not `sys.platform`; the gate must NOT import laya at module scope
(probe inside a function, like `select_backend`); bookkeeping/logging never crashes a run;
verify gate absolute (ruff clean, `uv run pytest -q` not down).

## Tasks (TDD)

### Task 1 — `laya_installed` importability probe
- `src/rudra/decide/backends.py`: add `laya_installed(cfg) -> bool`. True iff the laya
  package for this machine is IMPORTABLE (not: usable/downloaded). Mirror `select_backend`'s
  guarded imports: try `import laya_mlx` (Apple-Silicon path) and/or `import laya` (onnx
  path); respect a forced `backend` ("mlx"→only laya_mlx, "onnx"→only laya, "auto"→either,
  "off"→treat as the capability default for *installed* purposes, i.e. still probe). It only
  imports; it never calls `load`. Catch all import errors → False.
- Tests `tests/decide/test_backends.py`: monkeypatch `sys.modules` / the import points so
  the three cases (mlx present, onnx present, neither) return the right bool WITHOUT laya
  installed. Do not require laya in CI.

### Task 2 — the run-entry gate
- New small function, e.g. `src/rudra/decide/gate.py::require_laya(cfg) -> str | None`
  returning a user-facing error message (naming `Documentation/16-laya.md`) when the run
  must be blocked, else `None`. Blocked iff `getattr(cfg.decide, "enabled", True)` is true
  AND `not laya_installed(cfg)`.
- Call it at the TOP of `RudraAgent.run()` (`src/rudra/agent/main_agent.py:339`), before any
  planning/model call. On block: surface the message and end the run with a non-zero exit,
  through whatever error channel `run()` already uses (inspect how run() reports a fatal
  setup error / how the CLI at cli.py:1615-1643 maps it to an exit code — reuse that path,
  do not invent a new one). No model call may happen on the blocked path.
- Emit a `rudra.*` NOTICE for the block (diagnostic; §8a) — but the user-facing message is
  the primary output.
- Tests: with a cfg (`enabled=true`) and `laya_installed` monkeypatched False → `run()`
  blocks before planning (assert no planner/model call; assert the message names the doc).
  With `laya_installed` True → run proceeds (gate returns None). With `enabled=false` +
  not installed → run proceeds (no gate). Use the existing run-path test seams
  (`context._model` / fakes) so no real model or laya is needed.

### Task 3 — `doctor` reports laya
- `src/rudra/cli.py` doctor (≈:651): add a row — laya importable (yes/no) for this OS, the
  build it would use, and when missing the per-OS install command + a pointer to Doc 16.
  Share `laya_installed`. Doctor must still work (it's a bootstrap command) whether or not
  laya is present.
- Test: doctor output includes the laya row both present and absent (monkeypatched).

### Task 4 — spec/doc reconciliation (mostly done)
- `00-master.md` §3/§4: the "fallback always safe" bullet now governs a decision WITHIN a
  run; add a run-entry-requirement clause (laya required unless `enabled=false`).
- Already written on branch (verify consistent with the shipped code): `Documentation/16-laya.md`,
  README laya section + install step, `01-getting-started.md` step, CLAUDE.md `decide/` entry.

## Done

`ruff check` clean; `uv run pytest -q` not down; the gate blocks/permits per scope with
tests; `doctor` reports laya; master-spec + CLAUDE.md + README agree with the code; commits
on `laya-decision-integration`, not merged. Update `TODO.md` (OPEN-180 removed, Next→OPEN-174).
