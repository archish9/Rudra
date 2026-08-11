# Step 8 — Git Tools and the Test Runner: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Rudra a Python API and two model-facing tools for running the project's tests and reading its git state, so Step 9's fix loop has something to loop on.

**Architecture:** One gated subprocess primitive (`shell/runner.py`) sits under two independent consumers (`git/`, `testing/`). Every command reaches the existing Step 7 permission engine as `tool="execute"` with the real command string, so no new permission-rule vocabulary is introduced and the audit log keeps recording real commands. `testing/` reads its command from the existing `stacks/` detection; `git/` does not depend on `stacks/` at all.

**Tech Stack:** Python 3.12+, `subprocess`, `dataclasses`, `langchain_core.tools.tool`, pytest. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-08-11-step8-git-and-test-runner-design.md` — read §4 and §8 before starting. The seven owner decisions S8.1–S8.7 are binding.

## Global Constraints

- **Ledger discipline (CLAUDE.md §2, rule 2):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE`.
- **Evidence-based (rule 3):** every claim about the codebase cites `file.py:line`. If you cannot verify it, say so.
- **Verify before claiming done (rule 4):** run the command, show the output.
- **Quality gate, absolute since Step 3:** `.venv/bin/ruff check src/ tests/` must print `All checks passed!`, and `.venv/bin/ruff format --check src/ tests/` must be clean.
- **Test baseline measured 2026-08-11 on a clean tree at `d93541c`:** `.venv/bin/pytest -q` → `516 passed, 2 skipped`. It must never go down. (`TODO.md` records 515 and `CLAUDE.md` §8 records 489 — both stale. Re-measure; do not trust any of the three.)
- **No new runtime dependencies.** `pyproject.toml`'s `[project] dependencies` is not touched by this step.
- **Rudra's own toolchain is never conflated with the user's project toolchain** (D18). Test-command resolution reads the *target project's* layout, never Rudra's `.venv`.
- **Never invent test-runner output.** Fixtures in Task 8 are captured from real runs. Two are supplied verbatim in this plan; the jest one you capture yourself.
- **`from __future__ import annotations` at the top of every new module**, matching every existing module in `src/rudra/`.
- **Type hints on every public function.** Frozen dataclasses for every result type.
- Commit after every task. Branch is `step-8-git-and-test-runner`, already created, already holding the spec commit `d61bdd3`.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/rudra/shell/__init__.py` | Re-exports `CommandResult`, `run_gated` |
| `src/rudra/shell/runner.py` | The only subprocess call site in Rudra. Gate decision + capture + timeout |
| `src/rudra/git/__init__.py` | Re-exports the public git API |
| `src/rudra/git/core.py` | Repo predicates, status/diff/log, `auto_branch` |
| `src/rudra/git/tools.py` | `create_git_tools()` → `[git_diff]` |
| `src/rudra/testing/__init__.py` | Re-exports `TestResult`, `run_tests` |
| `src/rudra/testing/parse.py` | Per-stack summary-line count parsing. Pure, no I/O |
| `src/rudra/testing/runner.py` | `TestResult`, command resolution, `run_tests` |
| `src/rudra/testing/tools.py` | `create_testing_tools()` → `[run_tests]` |

**Modified:**

| Path | Change |
|---|---|
| `src/rudra/config/schema.py:96-133` | `ToolsConfig` gains `auto_branch`, `test_timeout`; `DEFAULTS["tools"]` likewise |
| `src/rudra/config/loader.py:47-48,187-191` | `_TOOLS_KEYS` split by type; per-key validation |
| `src/rudra/config/layers.py:171-176` | `RUDRA_AUTO_BRANCH`, `RUDRA_TEST_TIMEOUT` |
| `src/rudra/config/template.py:71-79` | Two new keys, plus the commented git deny lines |
| `src/rudra/permissions/rules.py:29-38,189-191` | `read_plan` → `READ_ONLY_TOOLS`; new `WRAPPED_EXECUTE_TOOLS` |
| `src/rudra/permissions/middleware.py:52-86` | `interrupt_tools`; unregistered `ask` → deny (`A1.51`) |
| `src/rudra/permissions/__init__.py:98-106` | Pass `interrupt_on` keys into the middleware |
| `src/rudra/stacks/registry.py:11-29` | `PYTHON.test_command = None` (`A1.33(b)`) |
| `src/rudra/stacks/detect.py:68-85` | Python branch in `resolve_test_command` |
| `src/rudra/agent/planner_agent.py:102-104` | Register the two new tools |
| `src/rudra/agent/main_agent.py:361-376` | `auto_branch` hook |

---

## Task 1: Config keys — `auto_branch` and `test_timeout`

**Files:**
- Modify: `src/rudra/config/schema.py:96-133`
- Modify: `src/rudra/config/loader.py:47-48,187-191`
- Modify: `src/rudra/config/layers.py:171-176`
- Modify: `src/rudra/config/template.py:71-79`
- Test: `tests/test_config_permissions_and_tools.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `cfg.tools.auto_branch: bool` (default `False`), `cfg.tools.test_timeout: int` (default `600`). Tasks 5, 9, and 11 read these.

**Why this is first:** `[tools]` validation currently rejects any non-bool value (`loader.py:190-191`), so an int key cannot be added without changing the validator. Every later task that reads config depends on this landing.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config_permissions_and_tools.py`:

```python
def test_tools_defaults_include_auto_branch_and_test_timeout(tmp_path: Path):
    cfg = build_config(project_root=tmp_path)
    assert cfg.tools.auto_branch is False
    assert cfg.tools.test_timeout == 600


def test_test_timeout_accepts_an_integer(tmp_path: Path):
    _write_project_toml(tmp_path, "[tools]\ntest_timeout = 90\n")
    cfg = build_config(project_root=tmp_path)
    assert cfg.tools.test_timeout == 90


def test_test_timeout_rejects_a_bool(tmp_path: Path):
    # isinstance(True, int) is True in Python, so bool must be excluded
    # explicitly or `test_timeout = true` silently becomes a 1-second timeout.
    _write_project_toml(tmp_path, "[tools]\ntest_timeout = true\n")
    with pytest.raises(ConfigError, match="whole number of seconds"):
        build_config(project_root=tmp_path)


def test_test_timeout_rejects_zero_and_negative(tmp_path: Path):
    _write_project_toml(tmp_path, "[tools]\ntest_timeout = 0\n")
    with pytest.raises(ConfigError, match="greater than 0"):
        build_config(project_root=tmp_path)


def test_auto_branch_rejects_a_string(tmp_path: Path):
    _write_project_toml(tmp_path, '[tools]\nauto_branch = "yes"\n')
    with pytest.raises(ConfigError, match="true or false"):
        build_config(project_root=tmp_path)


def test_unknown_tools_key_still_suggests_the_nearest_name(tmp_path: Path):
    _write_project_toml(tmp_path, "[tools]\nauto_brnch = true\n")
    with pytest.raises(ConfigError, match="auto_branch"):
        build_config(project_root=tmp_path)
```

Use the file's existing helper for writing a project TOML — read the top of `tests/test_config_permissions_and_tools.py` and reuse whatever it already defines rather than adding a second one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_permissions_and_tools.py -q -k "auto_branch or test_timeout"`
Expected: FAIL — `TypeError: ToolsConfig.__init__() got an unexpected keyword argument` or `AttributeError: 'ToolsConfig' object has no attribute 'auto_branch'`.

- [ ] **Step 3: Extend `ToolsConfig` and `DEFAULTS`**

In `src/rudra/config/schema.py`, add the two fields to `ToolsConfig` and extend its docstring:

```python
@dataclass(frozen=True)
class ToolsConfig:
    """The tool layer. Un-reserved in Step 7, which implements it.

    The oversized-tool-result threshold that would naturally live here is
    unreachable through `create_deep_agent` (TODO.md A1.47), and a key that
    silently does nothing is worse than no key.

    `shell_in_auto` is off by default. Under `--auto` nobody reads the
    command before it runs, and a shell command can write anywhere the user
    can — measured, not theorised (A1.49). Unattended runs therefore get
    the filesystem tools, which the backend genuinely confines, unless the
    user opts in once. `ask` mode is unaffected.

    `auto_branch` is off by default for the same reason in a different
    shape: branching mutates the user's repository before the model has
    done anything, and nobody asked for it (Step 8 spec S8.3).

    `test_timeout` is a real key rather than a constant because Angular's
    Karma builder hangs indefinitely without a browser (C11.3), and no one
    number fits both a three-second unit suite and an integration run.
    """

    shell: bool
    shell_in_auto: bool
    auto_branch: bool
    test_timeout: int
```

And in `DEFAULTS`:

```python
    "tools": {"shell": True, "shell_in_auto": False, "auto_branch": False, "test_timeout": 600},
```

- [ ] **Step 4: Split `[tools]` validation by type**

In `src/rudra/config/loader.py`, replace line 48 and the loop at 187-191:

```python
_TOOLS_BOOL_KEYS = frozenset({"shell", "shell_in_auto", "auto_branch"})
_TOOLS_INT_KEYS = frozenset({"test_timeout"})
_TOOLS_KEYS = _TOOLS_BOOL_KEYS | _TOOLS_INT_KEYS
```

```python
    for key, value in merged.get("tools", {}).items():
        if key not in _TOOLS_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [tools].{_suggest(key, _TOOLS_KEYS)}")
        if key in _TOOLS_BOOL_KEYS:
            if not isinstance(value, bool):
                raise ConfigError(f"[tools] {key} must be true or false, got {value!r}.")
            continue
        # bool is a subclass of int, so `test_timeout = true` would otherwise
        # pass isinstance and become a 1-second timeout.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"[tools] {key} must be a whole number of seconds, got {value!r}."
            )
        if value <= 0:
            raise ConfigError(f"[tools] {key} must be greater than 0, got {value!r}.")
```

- [ ] **Step 5: Add the environment-variable cases**

In `src/rudra/config/layers.py`, immediately after the `SHELL_IN_AUTO` branch (line 176), add:

```python
        if tail == "AUTO_BRANCH":
            put("tools", "auto_branch", value=_as_bool(raw))
            continue
        # Before _split_role_and_suffix: it would read TEST_TIMEOUT as role
        # "test" plus suffix "timeout", which IS a MODEL_KEY, and silently
        # create a phantom [model.test] section.
        if tail == "TEST_TIMEOUT":
            put("tools", "test_timeout", value=_as_number(raw))
            continue
```

Both must sit **before** the `_split_role_and_suffix` call at line 177, for the reason the comment states.

- [ ] **Step 6: Add an env-layer test**

```python
def test_test_timeout_env_var_does_not_become_a_model_role(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RUDRA_TEST_TIMEOUT", "45")
    cfg = build_config(project_root=tmp_path)
    assert cfg.tools.test_timeout == 45
    assert "test" not in cfg.models
```

- [ ] **Step 7: Update the config template**

In `src/rudra/config/template.py`, replace the `[tools]` block (lines 71-79) with:

```
[tools]
# false removes the execute tool entirely: no tests, no linters, no git.
shell = true

# Whether --auto may run commands. Off by default: in unattended mode nobody
# reads the command before it runs, and a shell command can write anywhere
# you can — Rudra's confinement covers the file-writing tools, not the shell.
# Turn it on if you want unattended test runs, knowing that.
shell_in_auto = false

# Create a `rudra/<task-slug>` branch before a run, so the agent's work is
# not on your branch. Only fires from a clean tree with a non-detached HEAD;
# otherwise it prints why and continues where you are. Never fails the run.
auto_branch = false

# How long a test command may run before Rudra kills it, in seconds.
# Angular's Karma builder hangs forever when no browser is installed, and
# inside a fix loop a hang stalls the loop instead of failing a round.
test_timeout = 600
```

And extend the `[permissions]` block's `deny` line (template line 59) with the commented git rules:

```
allow = []
deny  = []

# Rudra itself never commits. The agent can still run git through the shell,
# so uncomment these if you would rather it could not:
# deny = ["execute:git commit*", "execute:git push*", "execute:git reset --hard*"]
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: all new tests PASS, total at or above `516 passed, 2 skipped`, `All checks passed!`, formatting clean.

- [ ] **Step 9: Verify the template still parses**

Run: `cd "$(mktemp -d)" && /Users/archish/Documents/ai-ml/Rudra/.venv/bin/rudra init && /Users/archish/Documents/ai-ml/Rudra/.venv/bin/rudra config list | grep -E "auto_branch|test_timeout"`
Expected: both keys listed with their default values and the layer that set them. A template that does not round-trip through the loader is a broken `rudra init`.

- [ ] **Step 10: Commit**

```bash
git add src/rudra/config/ tests/test_config_permissions_and_tools.py
git commit -m "feat(config): auto_branch and test_timeout in [tools]

[tools] validation accepted only booleans, so an integer key could not be
added without splitting it by type. bool is a subclass of int, so
test_timeout = true is rejected explicitly or it becomes a 1-second timeout.

RUDRA_TEST_TIMEOUT is handled before _split_role_and_suffix, which would
otherwise read it as role 'test' plus suffix 'timeout' -- a real MODEL_KEY --
and create a phantom [model.test] section."
```

---

## Task 2: `A1.51` — the gate stops failing open on unknown tools

**Files:**
- Modify: `src/rudra/permissions/rules.py:29-38,189-191`
- Modify: `src/rudra/permissions/middleware.py:52-86`
- Modify: `src/rudra/permissions/__init__.py:98-106`
- Test: `tests/test_permissions_unknown_tool.py` (create)
- Test: `tests/test_permissions_middleware.py` (update construction sites)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `WRAPPED_EXECUTE_TOOLS: frozenset[str]` in `rules.py`, and `RudraPermissionMiddleware(engine, audit, mode, interrupt_tools)`. Task 10 relies on `git_diff` and `run_tests` being in `WRAPPED_EXECUTE_TOOLS`.

**Why before the tools exist:** Task 10 registers two tool names. On today's code an unregistered name decided `ask` executes silently and unaudited — the defect `A1.51` records. Fixing it after registering the tools would mean shipping the hole, however briefly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permissions_unknown_tool.py`:

```python
"""A1.51 — a tool decided `ask` with no interrupt entry must not run silently."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from rudra.permissions.audit import AuditLog
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import READ_ONLY_TOOLS, WRAPPED_EXECUTE_TOOLS, PermissionEngine


class _Request:
    def __init__(self, name: str, args: dict) -> None:
        self.tool_call = {"name": name, "args": args, "id": "call-1"}


def _engine(tmp_path: Path, mode: str = "ask") -> PermissionEngine:
    return PermissionEngine(
        mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )


def _middleware(tmp_path: Path, interrupt_tools: frozenset[str]) -> RudraPermissionMiddleware:
    return RudraPermissionMiddleware(
        _engine(tmp_path),
        AuditLog(tmp_path / "audit.jsonl"),
        "ask",
        interrupt_tools=interrupt_tools,
    )


def test_unregistered_tool_decided_ask_is_denied_not_run(tmp_path: Path):
    middleware = _middleware(tmp_path, frozenset({"write_file"}))
    called = False

    def handler(request):
        nonlocal called
        called = True
        return "ran"

    result = middleware.wrap_tool_call(_Request("mystery_tool", {}), handler)

    assert called is False, "the handler must never run for an unregistered ask"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "mystery_tool" in result.content


def test_unregistered_denial_is_audited(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    middleware = RudraPermissionMiddleware(
        _engine(tmp_path), AuditLog(audit_path), "ask", interrupt_tools=frozenset()
    )
    middleware.wrap_tool_call(_Request("mystery_tool", {}), lambda request: "ran")

    assert audit_path.exists(), "a silent denial is the defect, not the fix"
    assert "mystery_tool" in audit_path.read_text(encoding="utf-8")


def test_registered_ask_tool_still_falls_through_to_the_interrupt(tmp_path: Path):
    middleware = _middleware(tmp_path, frozenset({"write_file"}))
    result = middleware.wrap_tool_call(
        _Request("write_file", {"file_path": "a.py"}), lambda request: "ran"
    )
    assert result == "ran", "interrupt_on owns the ask path for registered tools"


def test_read_plan_is_a_read_and_never_prompts(tmp_path: Path):
    assert "read_plan" in READ_ONLY_TOOLS
    assert _engine(tmp_path).decide("read_plan", {}).effect == "allow"


@pytest.mark.parametrize("tool", sorted(WRAPPED_EXECUTE_TOOLS))
def test_wrapped_execute_tools_are_allowed_at_the_middleware(tmp_path: Path, tool: str):
    # Their inner run_gated makes the real execute decision; deciding them
    # again here would prompt twice for one command.
    decision = _engine(tmp_path).decide(tool, {})
    assert decision.effect == "allow"
    assert decision.source == "wrapped-execute"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_permissions_unknown_tool.py -q`
Expected: FAIL — `ImportError: cannot import name 'WRAPPED_EXECUTE_TOOLS'`.

- [ ] **Step 3: Extend `rules.py`**

Replace lines 29-38 of `src/rudra/permissions/rules.py`:

```python
# Rudra's own orchestration tools. They write only under .rudra/run/ and are
# how the loop functions; gating them would make mode="ask" prompt for
# Rudra's own bookkeeping (spec §4.6).
CONTROL_PLANE_TOOLS = frozenset({"update_plan", "write_task_assignment", "ask_user"})

# `read_plan` belongs here, not in the control plane: it only reads
# .rudra/run/PLAN.md. It was in neither set until Step 8, which made it the
# one reachable instance of A1.51 -- decided "ask", no interrupt registered,
# so it ran unprompted and unaudited.
READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep", "read_plan"})
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})

# Tools that are a shell command wearing a different name. Their own
# implementation calls PermissionEngine.decide("execute", ...) with the real
# command string, so deciding them a second time here would prompt twice for
# one command (Step 8 spec S8.2, §6.6).
WRAPPED_EXECUTE_TOOLS = frozenset({"git_diff", "run_tests"})

# `task` is allowed: it spawns a subagent whose own tool calls are gated by
# this same engine, so gating the spawn would double-prompt (spec §4.3).
_OTHER_TOOLS = frozenset({"task"})

ALL_GATED_TOOLS = (
    READ_ONLY_TOOLS | MUTATING_TOOLS | _OTHER_TOOLS | CONTROL_PLANE_TOOLS | WRAPPED_EXECUTE_TOOLS
)
```

Then in `decide`, immediately after the control-plane check (line 190-191):

```python
        if tool in WRAPPED_EXECUTE_TOOLS:
            return Decision("allow", None, "wrapped-execute")
```

Add `WRAPPED_EXECUTE_TOOLS` to `__all__`.

- [ ] **Step 4: Extend the middleware**

In `src/rudra/permissions/middleware.py`, add the message builder beside `_denial_text`:

```python
def _unregistered_text(tool: str) -> str:
    return (
        f"Permission denied: {tool!r} has no permission rule and no approval "
        f"prompt, so Rudra cannot ask the user about it and will not run it "
        f"unchecked. Do not retry this call. This is a Rudra configuration "
        f"gap, not something you did wrong — use a different tool."
    )
```

Change `__init__` and `_check`:

```python
    def __init__(
        self,
        engine: PermissionEngine,
        audit: AuditLog,
        mode: str,
        interrupt_tools: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.engine = engine
        self.audit = audit
        self.mode = mode
        self.interrupt_tools = interrupt_tools
```

```python
        if decision.effect != "deny":
            # "ask" belongs to interrupt_on -- but only where an entry exists.
            # Without one there is no prompt and no denial, so the call would
            # run unaudited: A1.51. Fail closed instead of enumerating every
            # tool deepagents might register.
            if tool in self.interrupt_tools:
                return None
            self.audit.record(tool, arg, decision, mode=self.mode, outcome="deny")
            return ToolMessage(
                content=_unregistered_text(tool),
                tool_call_id=call.get("id", ""),
                name=tool,
                status="error",
            )
```

Leave the `allow` branch above it untouched.

- [ ] **Step 5: Wire it in `build_gate`**

In `src/rudra/permissions/__init__.py`, build `interrupt_on` before the middleware so its keys can be passed in:

```python
    audit = AuditLog(rudra_paths(project_path).logs / "permissions.jsonl")
    interrupt_on = build_interrupt_on(engine)
    return Gate(
        engine=engine,
        middleware=RudraPermissionMiddleware(
            engine, audit, cfg.permissions.mode, interrupt_tools=frozenset(interrupt_on)
        ),
        interrupt_on=interrupt_on,
        grants=grants,
        audit=audit,
        mode=cfg.permissions.mode,
        project_root=Path(project_path),
    )
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_unknown_tool.py tests/test_permissions_middleware.py tests/test_permissions_gate.py -q`
Expected: PASS. If `test_permissions_middleware.py` constructs the middleware positionally with three arguments it still works — `interrupt_tools` defaults to empty. Any test there that asserts an unregistered `ask` falls through must be updated to assert the denial instead; that is the behaviour change, and the old assertion encoded the bug.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/`
Expected: at or above `516 passed, 2 skipped`, `All checks passed!`.

- [ ] **Step 8: Mark `A1.51` DONE in `TODO.md`**

Change its Status cell from `PENDING` to `**DONE** 2026-08-11` and append the verifying evidence to the Item cell: the two behaviours now asserted (`unregistered ask → deny + audited`, `read_plan → allow`) and the test file name.

- [ ] **Step 9: Commit**

```bash
git add src/rudra/permissions/ tests/test_permissions_unknown_tool.py tests/test_permissions_middleware.py TODO.md
git commit -m "fix(permissions): the gate no longer fails open on unknown tools

A1.51. decide() fell through to the mode default for any name outside its
four sets, returning 'ask'. build_interrupt_on registers only MUTATING_TOOLS,
and the middleware fell through to the handler for any effect that was not
'deny'. So 'ask' with no interrupt entry was neither a prompt nor a denial:
the call ran, and audit.record was never reached.

The middleware now receives the interrupt_on keys and denies an ask it
cannot prompt for. read_plan joins READ_ONLY_TOOLS, closing the one
instance reachable today."
```

---

## Task 3: `shell/runner.py` — the single gated subprocess call site

**Files:**
- Create: `src/rudra/shell/__init__.py`
- Create: `src/rudra/shell/runner.py`
- Test: `tests/test_shell_runner.py` (create)

**Interfaces:**
- Consumes: `Gate` from `rudra.permissions` (attributes `.engine`, `.audit`, `.mode`, and the method `.prompt(requests, console)`).
- Produces:
  - `CommandResult` — frozen dataclass with fields `argv: tuple[str, ...]`, `command: str`, `exit_code: int | None`, `stdout: str`, `stderr: str`, `denied: bool`, `denial_reason: str | None`, `timed_out: bool`, and the property `ok: bool`.
  - `run_gated(argv: Sequence[str], *, cwd: Path, gate: Any, console: Console, timeout: int, env: dict[str, str] | None = None, read_only: bool = False) -> CommandResult`.

Tasks 4, 5, 6, and 9 all call `run_gated` and nothing else calls `subprocess`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shell_runner.py`:

```python
"""The one subprocess call site: gate decision, capture, timeout, failure modes."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine
from rudra.shell.runner import CommandResult, run_gated


class _Gate:
    """A real engine and a real audit log; only the prompt is scripted."""

    def __init__(self, tmp_path: Path, mode: str, answer: str = "approve") -> None:
        self.engine = PermissionEngine(
            mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path
        )
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.mode = mode
        self.answer = answer
        self.prompted: list[dict] = []

    def prompt(self, requests, console):
        self.prompted.extend(requests)
        return [{"type": self.answer} for _ in requests]


def _echo(text: str) -> list[str]:
    return [sys.executable, "-c", f"print({text!r})"]


def test_allowed_command_runs_and_captures_stdout(tmp_path: Path):
    result = run_gated(
        _echo("hello"),
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert result.exit_code == 0
    assert result.ok is True
    assert "hello" in result.stdout
    assert result.denied is False


def test_allowed_command_is_audited_with_the_real_command_string(tmp_path: Path):
    gate = _Gate(tmp_path, "auto")
    run_gated(_echo("hi"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    logged = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"tool": "execute"' in logged
    assert "-c" in logged, "the audit line records the command, not a tool name"


def test_denied_command_never_runs(tmp_path: Path):
    marker = tmp_path / "should-not-exist.txt"
    gate = _Gate(tmp_path, "plan")  # plan mode denies every mutating tool
    result = run_gated(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"],
        cwd=tmp_path,
        gate=gate,
        console=Console(),
        timeout=30,
    )
    assert result.denied is True
    assert result.exit_code is None
    assert marker.exists() is False


def test_ask_mode_prompts_once_and_runs_on_approve(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="approve")
    result = run_gated(_echo("ok"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    assert len(gate.prompted) == 1
    assert gate.prompted[0]["name"] == "execute"
    assert result.exit_code == 0


def test_ask_mode_reject_returns_denied_and_does_not_run(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="reject")
    result = run_gated(_echo("nope"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    assert result.denied is True
    assert result.stdout == ""


def test_read_only_bypasses_the_prompt_entirely(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="reject")
    result = run_gated(
        _echo("read"), cwd=tmp_path, gate=gate, console=Console(), timeout=30, read_only=True
    )
    assert gate.prompted == []
    assert result.exit_code == 0


def test_missing_binary_is_a_value_not_an_exception(tmp_path: Path):
    result = run_gated(
        ["definitely-not-a-real-binary-xyz"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert isinstance(result, CommandResult)
    assert result.exit_code is None
    assert result.timed_out is False
    assert "definitely-not-a-real-binary-xyz" in result.stderr


def test_timeout_kills_the_command_and_reports_it(tmp_path: Path):
    result = run_gated(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=1,
    )
    assert result.timed_out is True
    assert result.exit_code is None


def test_non_zero_exit_is_not_an_error(tmp_path: Path):
    result = run_gated(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert result.exit_code == 3
    assert result.ok is False
    assert result.denied is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_shell_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.shell'`.

- [ ] **Step 3: Write `src/rudra/shell/runner.py`**

```python
"""The only place in Rudra that starts a subprocess.

Both Step 8 consumers -- git (C3.5) and the test runner (C3.6) -- need the
same primitive: run a command, through the permission gate, and capture what
it said. Two copies of a gate check is the thing that drifts, and the half
that drifts is the security-relevant one, so there is one copy.

Every command reaches the gate as `execute` with its real command string
(Step 8 spec S8.2). Three properties follow: users keep writing
`allow = ["execute:pytest*"]` with no new vocabulary, the audit log records
the actual command rather than an opaque tool name, and A1.49's unattended
shell opt-in covers these tools automatically -- correctly, because they are
shell.

Nothing here raises for a denial, a non-zero exit, a timeout, or a missing
binary. Every one of those is a value the caller acts on.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass(frozen=True)
class CommandResult:
    """What one command did.

    `exit_code` is None whenever the command never produced one: denied,
    timed out, or the binary was not found. Callers distinguish which via
    `denied` and `timed_out`.
    """

    argv: tuple[str, ...]
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    denied: bool = False
    denial_reason: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _denied(argv: tuple[str, ...], command: str, reason: str) -> CommandResult:
    return CommandResult(
        argv=argv,
        command=command,
        exit_code=None,
        stdout="",
        stderr="",
        denied=True,
        denial_reason=reason,
    )


def _permitted(
    argv: tuple[str, ...], command: str, gate: Any, console: Console
) -> CommandResult | None:
    """None when the command may run; a denied CommandResult when it may not."""
    decision = gate.engine.decide("execute", {"command": command})

    if decision.effect == "deny":
        gate.audit.record("execute", command, decision, mode=gate.mode, outcome="deny")
        return _denied(argv, command, decision.rule or "denied by the permission gate")

    if decision.effect == "ask":
        # Gate.prompt already audits whatever the user chose, so this branch
        # deliberately does not record anything itself.
        answers = gate.prompt([{"name": "execute", "args": {"command": command}}], console)
        if not answers or answers[0].get("type") != "approve":
            return _denied(argv, command, "the user rejected this command")
        return None

    gate.audit.record("execute", command, decision, mode=gate.mode, outcome="allow")
    return None


def run_gated(
    argv: Sequence[str],
    *,
    cwd: Path,
    gate: Any,
    console: Console,
    timeout: int,
    env: dict[str, str] | None = None,
    read_only: bool = False,
) -> CommandResult:
    """Run `argv` in `cwd`, subject to the permission gate.

    `read_only=True` skips the decision. It is for commands Rudra itself
    composes from a fixed set that only reads -- `git status`, `git log` --
    where prompting would fire several times before the planner even starts.
    It is consistent with policy already in force: reads are never gated
    (permissions/rules.py) and are never a floor violation
    (permissions/floor.py). Never pass it for anything a model composed or
    anything that writes.
    """
    argv = tuple(str(part) for part in argv)
    command = shlex.join(argv)

    if not read_only and gate is not None:
        refusal = _permitted(argv, command, gate, console)
        if refusal is not None:
            return refusal

    try:
        process = subprocess.Popen(  # noqa: S603 - argv is a list, never a shell string
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        return CommandResult(argv, command, None, "", f"{argv[0]}: {exc}")

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # start_new_session put the child in its own process group, so the
        # whole tree dies. Killing only the child would leave a test runner's
        # workers alive -- the Karma case C11.3 describes.
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            process.kill()
        stdout, stderr = process.communicate()
        return CommandResult(argv, command, None, stdout, stderr, timed_out=True)

    return CommandResult(argv, command, process.returncode, stdout, stderr)


__all__ = ["CommandResult", "run_gated"]
```

- [ ] **Step 4: Write `src/rudra/shell/__init__.py`**

```python
"""Gated command execution — the one subprocess call site (Step 8)."""

from __future__ import annotations

from rudra.shell.runner import CommandResult, run_gated

__all__ = ["CommandResult", "run_gated"]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_shell_runner.py -q`
Expected: PASS, 9 tests.

If `test_timeout_kills_the_command_and_reports_it` is slow, that is expected — it sleeps for its 1-second timeout.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/shell/ tests/test_shell_runner.py
git commit -m "feat(shell): one gated subprocess call site

Both C3.5 and C3.6 need the same primitive, and a duplicated gate check is
the half that must not drift. Commands reach the engine as execute with the
real command string (S8.2), so existing allow/deny rules cover them, the
audit log stays readable, and A1.49's unattended opt-in applies.

Timeout kills the process group, not just the child: start_new_session
puts the command in its own group so a hung test runner's workers die too."
```

---

## Task 4: `git/core.py` — reading repository state

**Files:**
- Create: `src/rudra/git/__init__.py`
- Create: `src/rudra/git/core.py`
- Test: `tests/test_git_core.py` (create)

**Interfaces:**
- Consumes: `run_gated`, `CommandResult` from Task 3.
- Produces, all taking `(project_path: Path, *, gate, console, cfg)` unless noted:
  - `is_repo(...) -> bool`
  - `current_branch(...) -> str | None` — `None` on detached HEAD
  - `is_clean(...) -> bool`
  - `status(...) -> list[FileStatus]` where `FileStatus` has `index: str`, `worktree: str`, `path: str`
  - `diff(..., path: str | None = None, staged: bool = False, max_lines: int = 400) -> str`
  - `log(..., count: int = 10) -> list[Commit]` where `Commit` has `sha: str`, `author: str`, `subject: str`
  - `branch_exists(..., name: str) -> bool`

Task 5 adds `auto_branch` to this module. Task 6 calls `diff`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_core.py`:

```python
"""git/core against real repositories. git's presence was verified in C3.1."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from rudra.config import build_config
from rudra.git import core


class _AutoGate:
    """auto mode with shell opted in: every command is allowed, nothing prompts."""

    def __init__(self, tmp_path: Path) -> None:
        from rudra.permissions.audit import AuditLog
        from rudra.permissions.rules import PermissionEngine

        self.engine = PermissionEngine(
            mode="auto",
            allow=(),
            deny=(),
            floor_disable=(),
            project_root=tmp_path,
            shell_in_auto=True,
        )
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.mode = "auto"

    def prompt(self, requests, console):  # pragma: no cover - auto never asks
        raise AssertionError("auto mode must not prompt")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


@pytest.fixture
def env(tmp_path: Path):
    return {"gate": _AutoGate(tmp_path), "console": Console(), "cfg": build_config(project_root=tmp_path)}


def test_is_repo_is_true_inside_a_repository(repo: Path, env):
    assert core.is_repo(repo, **env) is True


def test_is_repo_is_false_in_a_plain_directory(tmp_path: Path, env):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.is_repo(plain, **env) is False


def test_current_branch_reports_the_branch(repo: Path, env):
    assert core.current_branch(repo, **env) == "main"


def test_current_branch_is_none_when_head_is_detached(repo: Path, env):
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "checkout", "-q", sha)
    assert core.current_branch(repo, **env) is None


def test_is_clean_is_true_after_a_commit(repo: Path, env):
    assert core.is_clean(repo, **env) is True


def test_is_clean_is_false_with_an_untracked_file(repo: Path, env):
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert core.is_clean(repo, **env) is False


def test_is_clean_is_false_with_a_modified_tracked_file(repo: Path, env):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert core.is_clean(repo, **env) is False


def test_status_reports_paths_and_codes(repo: Path, env):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    entries = {entry.path: entry for entry in core.status(repo, **env)}
    assert entries["a.txt"].worktree == "M"
    assert entries["b.txt"].index == "?"


def test_status_handles_paths_containing_spaces(repo: Path, env):
    (repo / "two words.txt").write_text("x", encoding="utf-8")
    assert "two words.txt" in {entry.path for entry in core.status(repo, **env)}


def test_diff_shows_working_tree_changes(repo: Path, env):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = core.diff(repo, **env)
    assert "-one" in text
    assert "+changed" in text


def test_diff_is_capped_and_says_so(repo: Path, env):
    (repo / "a.txt").write_text("\n".join(str(n) for n in range(500)) + "\n", encoding="utf-8")
    text = core.diff(repo, max_lines=20, **env)
    assert len(text.splitlines()) <= 25
    assert "truncated" in text.lower()


def test_diff_of_a_clean_tree_is_empty(repo: Path, env):
    assert core.diff(repo, **env).strip() == ""


def test_log_returns_commits_newest_first(repo: Path, env):
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "second: with a colon")
    commits = core.log(repo, count=5, **env)
    assert [c.subject for c in commits] == ["second: with a colon", "first"]
    assert commits[0].author == "Test"
    assert len(commits[0].sha) == 40


def test_branch_exists_distinguishes_present_from_absent(repo: Path, env):
    assert core.branch_exists(repo, "main", **env) is True
    assert core.branch_exists(repo, "nope", **env) is False


def test_read_only_git_never_prompts_in_ask_mode(repo: Path, tmp_path: Path):
    from rudra.permissions.audit import AuditLog
    from rudra.permissions.rules import PermissionEngine

    class _StrictGate:
        def __init__(self) -> None:
            self.engine = PermissionEngine(
                mode="ask", allow=(), deny=(), floor_disable=(), project_root=repo
            )
            self.audit = AuditLog(tmp_path / "audit.jsonl")
            self.mode = "ask"

        def prompt(self, requests, console):
            raise AssertionError("read-only git must not prompt")

    cfg = build_config(project_root=repo)
    assert core.is_repo(repo, gate=_StrictGate(), console=Console(), cfg=cfg) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_git_core.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.git'`.

- [ ] **Step 3: Write `src/rudra/git/core.py`**

```python
"""Git, as a Python API.

The orchestrator calls these directly (no model in the loop) and Step 9's
reviewer will too. Exactly one of them is exposed to the model, as a tool --
see git/tools.py -- because the model already has gated `execute` and a new
tool only earns its place by parsing output or bounding it (Step 8 spec §5.1).

Read-only subcommands run with `read_only=True`. `auto_branch` alone fires
three of them before the planner starts, and prompting for
`git rev-parse --is-inside-work-tree` on every run would make the gate an
annoyance rather than a control. This is consistent with policy already in
force: reads are never gated (permissions/rules.py) and are never a floor
violation (permissions/floor.py). The set is fixed here, in Rudra's own code
-- no model-supplied argv ever enters it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import CommandResult, run_gated

# git subcommands that only read. Rudra composes every one of these itself.
READ_ONLY_SUBCOMMANDS = frozenset({"rev-parse", "status", "log", "diff", "branch"})

# Generous, but not unbounded: a `git log` on a large history is slow, and a
# hung git is still a hang. Not user-configurable -- [tools] test_timeout is
# for test suites, whose runtime genuinely varies by project.
GIT_TIMEOUT_SECONDS = 60

# NUL, so a commit subject containing a colon does not split into two fields.
_LOG_FORMAT = "%H%x00%an%x00%s"


@dataclass(frozen=True)
class FileStatus:
    """One line of `git status --porcelain`."""

    index: str
    worktree: str
    path: str


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    subject: str


def _run(
    project_path: Path,
    argv: list[str],
    *,
    gate: Any,
    console: Console,
    cfg: Any,
) -> CommandResult:
    return run_gated(
        ["git", *argv],
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=GIT_TIMEOUT_SECONDS,
        env=scrubbed_env(cfg),
        read_only=bool(argv) and argv[0] in READ_ONLY_SUBCOMMANDS,
    )


def is_repo(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> bool:
    result = _run(
        project_path, ["rev-parse", "--is-inside-work-tree"], gate=gate, console=console, cfg=cfg
    )
    return result.ok and result.stdout.strip() == "true"


def current_branch(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> str | None:
    """The branch name, or None when HEAD is detached.

    `rev-parse --abbrev-ref HEAD` prints the literal string "HEAD" for a
    detached head, which is the case auto_branch must refuse.
    """
    result = _run(
        project_path, ["rev-parse", "--abbrev-ref", "HEAD"], gate=gate, console=console, cfg=cfg
    )
    if not result.ok:
        return None
    name = result.stdout.strip()
    return None if name in ("", "HEAD") else name


def is_clean(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> bool:
    result = _run(project_path, ["status", "--porcelain"], gate=gate, console=console, cfg=cfg)
    return result.ok and result.stdout.strip() == ""


def status(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list[FileStatus]:
    """Parsed `git status --porcelain`.

    The format is two status characters, a space, then the path -- so the
    path is everything from index 3 onward and splitting on whitespace would
    lose any filename containing a space. A rename prints `old -> new`; the
    new name is what a caller acts on.
    """
    result = _run(project_path, ["status", "--porcelain"], gate=gate, console=console, cfg=cfg)
    if not result.ok:
        return []

    entries: list[FileStatus] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append(FileStatus(index=line[0], worktree=line[1], path=path.strip('"')))
    return entries


def diff(
    project_path: Path,
    *,
    gate: Any,
    console: Console,
    cfg: Any,
    path: str | None = None,
    staged: bool = False,
    max_lines: int = 400,
) -> str:
    """The working-tree diff, capped.

    Capping is the whole reason this exists rather than leaving `git diff` to
    raw `execute`: an uncapped diff is the easiest way to fill a 32B context
    (D6) with something the model cannot act on.
    """
    argv = ["diff"]
    if staged:
        argv.append("--staged")
    if path:
        argv.extend(["--", path])

    result = _run(project_path, argv, gate=gate, console=console, cfg=cfg)
    if result.denied:
        return f"git diff was not permitted: {result.denial_reason}"
    if not result.ok:
        return result.stderr.strip() or "git diff failed."

    lines = result.stdout.splitlines()
    if len(lines) <= max_lines:
        return result.stdout
    kept = "\n".join(lines[:max_lines])
    return f"{kept}\n\n[diff truncated: {len(lines) - max_lines} more lines]"


def log(
    project_path: Path, *, gate: Any, console: Console, cfg: Any, count: int = 10
) -> list[Commit]:
    result = _run(
        project_path,
        ["log", f"-n{count}", f"--format={_LOG_FORMAT}"],
        gate=gate,
        console=console,
        cfg=cfg,
    )
    if not result.ok:
        return []

    commits: list[Commit] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) == 3:
            commits.append(Commit(sha=parts[0], author=parts[1], subject=parts[2]))
    return commits


def branch_exists(
    project_path: Path, name: str, *, gate: Any, console: Console, cfg: Any
) -> bool:
    result = _run(
        project_path,
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        gate=gate,
        console=console,
        cfg=cfg,
    )
    return result.ok


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "READ_ONLY_SUBCOMMANDS",
    "Commit",
    "FileStatus",
    "branch_exists",
    "current_branch",
    "diff",
    "is_clean",
    "is_repo",
    "log",
    "status",
]
```

- [ ] **Step 4: Write `src/rudra/git/__init__.py`**

```python
"""Git tools and the Python git API (Step 8, C3.5)."""

from __future__ import annotations

from rudra.git.core import (
    Commit,
    FileStatus,
    branch_exists,
    current_branch,
    diff,
    is_clean,
    is_repo,
    log,
    status,
)

__all__ = [
    "Commit",
    "FileStatus",
    "branch_exists",
    "current_branch",
    "diff",
    "is_clean",
    "is_repo",
    "log",
    "status",
]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_git_core.py -q`
Expected: PASS, 15 tests.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/git/ tests/test_git_core.py
git commit -m "feat(git): Python API for repository state

Read-only subcommands bypass the gate decision, bounded to a fixed set
Rudra composes itself. auto_branch fires three of them before the planner
starts; prompting for git rev-parse on every run would make the gate an
annoyance rather than a control, and reads are already never gated.

status parses from index 3 rather than splitting on whitespace, so paths
containing spaces survive. log is NUL-separated so a subject containing a
colon does not split into two fields."
```

---

## Task 5: `auto_branch` — isolate the run, or explain why not

**Files:**
- Modify: `src/rudra/git/core.py` (append)
- Modify: `src/rudra/git/__init__.py` (re-export)
- Test: `tests/test_git_auto_branch.py` (create)

**Interfaces:**
- Consumes: `is_repo`, `current_branch`, `is_clean`, `branch_exists`, `_run` from Task 4.
- Produces:
  - `branch_slug(task: str, max_length: int = 40) -> str`
  - `BranchOutcome` — frozen dataclass with `branch: str | None`, `skipped_reason: str | None`
  - `auto_branch(project_path: Path, task: str, *, gate, console, cfg) -> BranchOutcome`

Task 11 calls `auto_branch`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_auto_branch.py`. Reuse the `_AutoGate`, `_git`, `repo`, and `env` fixtures — copy them from `tests/test_git_core.py` rather than importing across test modules.

```python
def test_slug_is_lowercased_and_hyphenated():
    assert core.branch_slug("Build a Flask App") == "build-a-flask-app"


def test_slug_collapses_punctuation_and_trims_edges():
    assert core.branch_slug("  fix: the __init__ bug!!  ") == "fix-the-init-bug"


def test_slug_is_capped():
    assert len(core.branch_slug("word " * 50)) <= 40


def test_slug_never_ends_with_a_hyphen_after_capping():
    # A cap landing mid-separator would produce "rudra/foo-", which git rejects.
    assert not core.branch_slug("a" * 39 + " bbbb").endswith("-")


def test_slug_falls_back_when_nothing_survives():
    assert core.branch_slug("!!!") == "task"


def test_auto_branch_creates_a_branch_from_a_clean_tree(repo: Path, env):
    outcome = core.auto_branch(repo, "build a flask app", **env)
    assert outcome.branch == "rudra/build-a-flask-app"
    assert outcome.skipped_reason is None
    assert core.current_branch(repo, **env) == "rudra/build-a-flask-app"


def test_auto_branch_suffixes_a_name_already_in_use(repo: Path, env):
    core.auto_branch(repo, "same task", **env)
    _git(repo, "checkout", "-q", "main")
    outcome = core.auto_branch(repo, "same task", **env)
    assert outcome.branch == "rudra/same-task-2"


def test_auto_branch_skips_a_dirty_tree_and_says_why(repo: Path, env):
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    outcome = core.auto_branch(repo, "task", **env)
    assert outcome.branch is None
    assert "uncommitted" in outcome.skipped_reason
    assert core.current_branch(repo, **env) == "main", "the branch must not change"


def test_auto_branch_skips_a_detached_head(repo: Path, env):
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "checkout", "-q", sha)
    outcome = core.auto_branch(repo, "task", **env)
    assert outcome.branch is None
    assert "detached" in outcome.skipped_reason


def test_auto_branch_skips_a_plain_directory(tmp_path: Path, env):
    plain = tmp_path / "plain"
    plain.mkdir()
    outcome = core.auto_branch(plain, "task", **env)
    assert outcome.branch is None
    assert "git repository" in outcome.skipped_reason


def test_auto_branch_reports_a_denial_rather_than_raising(repo: Path, tmp_path: Path):
    from rudra.config import build_config
    from rudra.permissions.audit import AuditLog
    from rudra.permissions.rules import PermissionEngine

    class _DenyGate:
        def __init__(self) -> None:
            self.engine = PermissionEngine(
                mode="auto",
                allow=(),
                deny=(),
                floor_disable=(),
                project_root=repo,
                shell_in_auto=False,  # A1.49: execute is denied under bare --auto
            )
            self.audit = AuditLog(tmp_path / "audit.jsonl")
            self.mode = "auto"

        def prompt(self, requests, console):  # pragma: no cover
            raise AssertionError("auto mode must not prompt")

    outcome = core.auto_branch(
        repo, "task", gate=_DenyGate(), console=Console(), cfg=build_config(project_root=repo)
    )
    assert outcome.branch is None
    assert outcome.skipped_reason is not None
    assert core.current_branch(
        repo, gate=_DenyGate(), console=Console(), cfg=build_config(project_root=repo)
    ) == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_git_auto_branch.py -q`
Expected: FAIL — `AttributeError: module 'rudra.git.core' has no attribute 'branch_slug'`.

- [ ] **Step 3: Append to `src/rudra/git/core.py`**

Add `import re` to the imports, then:

```python
_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")

BRANCH_PREFIX = "rudra/"

# Enough branches for any real run; a project with more is not a naming
# collision, it is a signal that auto_branch is being used wrongly.
_MAX_SUFFIX = 50


@dataclass(frozen=True)
class BranchOutcome:
    """What auto_branch did, or the reason it did nothing.

    Skipping is a normal outcome, not an error. Exactly one field is set.
    """

    branch: str | None
    skipped_reason: str | None


def branch_slug(task: str, max_length: int = 40) -> str:
    """A git-ref-safe slug for a task description.

    Trailing separators are stripped AFTER capping as well as before: a cap
    landing mid-separator would otherwise produce `rudra/foo-`, and git
    rejects a ref component ending in a hyphen-only segment.
    """
    slug = _SLUG_SEPARATORS.sub("-", task.lower()).strip("-")
    return slug[:max_length].strip("-") or "task"


def auto_branch(
    project_path: Path, task: str, *, gate: Any, console: Console, cfg: Any
) -> BranchOutcome:
    """Create `rudra/<slug>` before a run, when it is safe to.

    Opt-in via `[tools] auto_branch` and off by default (Step 8 spec S8.3):
    this mutates the user's repository before the model has done anything.

    Every precondition failure returns a reason and changes nothing. It never
    raises and never fails the run -- a user who cannot get a branch still
    wants their task done, on the branch they are already on.

    The clean-tree precondition is not fussiness: `git checkout -b` carries
    uncommitted changes onto the new branch, and fails outright where it
    would clobber. A dirty tree is the case most likely to surprise someone
    who ran Rudra in a repository they care about.
    """
    common = {"gate": gate, "console": console, "cfg": cfg}

    if not is_repo(project_path, **common):
        return BranchOutcome(None, "this is not a git repository")
    if current_branch(project_path, **common) is None:
        return BranchOutcome(None, "HEAD is detached")
    if not is_clean(project_path, **common):
        return BranchOutcome(None, "the working tree has uncommitted changes")

    base = f"{BRANCH_PREFIX}{branch_slug(task)}"
    name = base
    for suffix in range(2, _MAX_SUFFIX + 1):
        if not branch_exists(project_path, name, **common):
            break
        name = f"{base}-{suffix}"
    else:
        return BranchOutcome(None, f"every name from {base} to {base}-{_MAX_SUFFIX} is taken")

    result = _run(project_path, ["checkout", "-b", name], **common)
    if result.denied:
        return BranchOutcome(None, f"creating the branch was not permitted: {result.denial_reason}")
    if not result.ok:
        return BranchOutcome(None, (result.stderr.strip() or "git checkout -b failed"))
    return BranchOutcome(name, None)
```

Add `BRANCH_PREFIX`, `BranchOutcome`, `auto_branch`, and `branch_slug` to `__all__` in both `core.py` and `__init__.py`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_git_auto_branch.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/git/ tests/test_git_auto_branch.py
git commit -m "feat(git): opt-in auto-branch with honest preconditions

Off by default (S8.3): branching mutates the user's repository before the
model has done anything. Fires only inside a work tree, with a non-detached
HEAD, from a clean tree -- git checkout -b carries uncommitted changes onto
the new branch and fails outright where it would clobber.

Every failure returns a reason and changes nothing. A user who cannot get a
branch still wants their task done on the branch they are on."
```

---

## Task 6: `git/tools.py` — the one model-facing git tool

**Files:**
- Create: `src/rudra/git/tools.py`
- Test: `tests/test_git_tools.py` (create)

**Interfaces:**
- Consumes: `core.diff`, `core.is_repo` from Task 4.
- Produces: `create_git_tools(project_path: Path, *, gate, console, cfg) -> list` returning `[git_diff]`. Task 10 calls it from `planner_agent.py`.

**The containment rule this task exists to enforce:** `git_diff`'s `path` argument is the one model-supplied value that would otherwise enter a gate-bypassed command. Resolve it against the project root; anything outside drops out of the read-only bypass into a full gate decision. Without this, `git_diff("../../other-repo")` is a read of an arbitrary repository that never reaches the engine.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_tools.py`. Copy the `_AutoGate`, `_git`, and `repo` fixtures from `tests/test_git_core.py`.

```python
def _tool(repo: Path, gate) -> Any:
    from rudra.config import build_config
    from rudra.git.tools import create_git_tools

    tools = create_git_tools(
        repo, gate=gate, console=Console(), cfg=build_config(project_root=repo)
    )
    assert [t.name for t in tools] == ["git_diff"]
    return tools[0]


def test_git_diff_returns_the_working_tree_diff(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = _tool(repo, _AutoGate(tmp_path)).invoke({})
    assert "+changed" in text


def test_git_diff_in_a_plain_directory_explains_rather_than_failing(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    text = _tool(plain, _AutoGate(tmp_path)).invoke({})
    assert "not a git repository" in text.lower()


def test_git_diff_says_so_when_there_is_nothing_to_show(repo: Path, tmp_path: Path):
    text = _tool(repo, _AutoGate(tmp_path)).invoke({})
    assert "no changes" in text.lower()


def test_git_diff_path_outside_the_project_is_gated_not_bypassed(repo: Path, tmp_path: Path):
    """The model-supplied path is the one way into a bypassed command."""
    prompted: list[dict] = []

    class _RecordingGate(_AutoGate):
        def __init__(self) -> None:
            super().__init__(tmp_path)
            from rudra.permissions.rules import PermissionEngine

            self.engine = PermissionEngine(
                mode="ask", allow=(), deny=(), floor_disable=(), project_root=repo
            )
            self.mode = "ask"

        def prompt(self, requests, console):
            prompted.extend(requests)
            return [{"type": "reject"}]

    text = _tool(repo, _RecordingGate()).invoke({"path": "../../elsewhere"})
    assert prompted, "an out-of-root path must reach the gate"
    assert "not permitted" in text.lower() or "rejected" in text.lower()


def test_git_diff_inside_the_project_does_not_prompt(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")

    class _StrictGate(_AutoGate):
        def prompt(self, requests, console):
            raise AssertionError("an in-root read must not prompt")

    text = _tool(repo, _StrictGate(tmp_path)).invoke({"path": "a.txt"})
    assert "+changed" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_git_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.git.tools'`.

- [ ] **Step 3: Write `src/rudra/git/tools.py`**

```python
"""The one git tool the model sees.

The model already has gated `execute`, so it can run `git status` today. A
new tool only earns its place by parsing output or bounding it -- otherwise
it is a second spelling of an existing capability, paid for in schema tokens
inside a 32B window (D6). An uncapped diff is exactly what raw `execute`
does badly, so `git_diff` is the one that ships (Step 8 spec §5.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from rich.console import Console

from rudra.git import core

MAX_DIFF_LINES = 400


def _within_root(project_path: Path, candidate: str) -> bool:
    """Is this path inside the project?

    Resolved before comparison, so `../../elsewhere` and a symlink out both
    land on their real location -- the same reason PermissionEngine._resolve
    resolves before matching.
    """
    root = Path(project_path).resolve()
    target = (root / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def create_git_tools(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list:
    """The git tools for one run. Currently exactly one."""

    @tool
    def git_diff(path: str = "", staged: bool = False) -> str:
        """Show what has changed in the working tree, as a unified diff.

        Use this to review your own edits before deciding what to do next.
        Output is capped, so a very large diff is truncated with a note.

        Args:
            path: Limit the diff to one file, relative to the project root.
                  Leave empty for every change.
            staged: Show staged changes instead of unstaged ones.

        Returns:
            The diff, or a sentence explaining why there is none.
        """
        common = {"gate": gate, "console": console, "cfg": cfg}

        if not core.is_repo(project_path, **common):
            return "This project is not a git repository, so there is no diff to show."

        target = path.strip() or None
        if target is not None and not _within_root(project_path, target):
            # Out of root: this is the one model-supplied value that could
            # otherwise ride a read-only bypass into an arbitrary repository.
            # Send the whole call through the gate instead.
            from rudra.permissions.env import scrubbed_env
            from rudra.shell.runner import run_gated

            result = run_gated(
                ["git", "diff", "--", target],
                cwd=project_path,
                gate=gate,
                console=console,
                timeout=core.GIT_TIMEOUT_SECONDS,
                env=scrubbed_env(cfg),
                read_only=False,
            )
            if result.denied:
                return f"Reading a diff outside the project was not permitted: {result.denial_reason}"
            return result.stdout or "No changes."

        text = core.diff(
            project_path, path=target, staged=staged, max_lines=MAX_DIFF_LINES, **common
        )
        return text if text.strip() else "No changes in the working tree."

    return [git_diff]


__all__ = ["MAX_DIFF_LINES", "create_git_tools"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_git_tools.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/git/tools.py tests/test_git_tools.py
git commit -m "feat(git): capped git_diff tool

One tool, not five: the model already has gated execute, and an uncapped
diff is the one thing raw execute does badly in a 32B window.

The path argument is the single model-supplied value that could ride the
read-only bypass into an arbitrary repository, so it is resolved against
the project root and anything outside takes a full gate decision instead."
```

---

## Task 7: `A1.33(b)` — a Python test command that can actually launch

**Files:**
- Modify: `src/rudra/stacks/registry.py:11-29`
- Modify: `src/rudra/stacks/detect.py:68-85`
- Test: `tests/test_stacks.py` (extend)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `resolve_test_command(project_path, profile)` now returns a launchable argv for Python projects. Task 9 consumes it unchanged — its signature does not move.

**The defect (`A1.33(b)`, `PENDING` since Step 4):** `PYTHON.test_command` is `("pytest",)` (`registry.py:14`), a bare executable name. It is not on `PATH` in any project virtualenv Rudra did not activate, and it is wrong for Django or stdlib `unittest`. Shipping `C3.6` against it turns "the environment is broken" into "this project has no tests" — a false negative fed straight into Step 9's gate.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stacks.py`:

```python
def _python_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return tmp_path


def _venv_with(tmp_path: Path, *executables: str) -> None:
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True)
    for name in executables:
        path = binaries / name
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)


def test_python_prefers_the_project_venv_pytest(tmp_path: Path):
    _python_project(tmp_path)
    _venv_with(tmp_path, "pytest", "python")
    command = resolve_test_command(tmp_path, PYTHON)
    assert command == [str(tmp_path / ".venv" / "bin" / "pytest")]


def test_python_uses_the_venv_interpreter_when_pytest_is_not_installed(tmp_path: Path):
    _python_project(tmp_path)
    _venv_with(tmp_path, "python")
    command = resolve_test_command(tmp_path, PYTHON)
    assert command == [str(tmp_path / ".venv" / "bin" / "python"), "-m", "pytest"]


def test_python_recognises_a_django_project(tmp_path: Path):
    _python_project(tmp_path)
    (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8")
    assert resolve_test_command(tmp_path, PYTHON) == ["python", "manage.py", "test"]


def test_python_falls_back_to_unittest_with_no_venv_and_no_pytest(tmp_path: Path):
    _python_project(tmp_path)
    assert resolve_test_command(tmp_path, PYTHON) == ["python", "-m", "unittest", "discover"]


def test_python_uses_pytest_when_the_project_declares_it(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    assert resolve_test_command(tmp_path, PYTHON) == ["python", "-m", "pytest"]


def test_python_test_command_is_never_a_bare_executable_name(tmp_path: Path):
    # A1.33(b): "pytest" alone is not on PATH in a venv Rudra did not activate.
    command = resolve_test_command(_python_project(tmp_path), PYTHON)
    assert command is not None
    assert command[0] != "pytest"


def test_venv_resolution_never_reads_rudras_own_venv(tmp_path: Path):
    # D18: Rudra being a Python project and the target being one must never
    # be conflated. Resolution reads the target's layout only.
    command = resolve_test_command(_python_project(tmp_path), PYTHON)
    assert "Rudra" not in " ".join(command)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_stacks.py -q -k python`
Expected: FAIL — the existing behaviour returns `["pytest"]`.

- [ ] **Step 3: Change the profile**

In `src/rudra/stacks/registry.py`, change `PYTHON.test_command` to `None` and record why:

```python
PYTHON = StackProfile(
    name="python",
    markers=("pyproject.toml", "setup.py", "requirements.txt"),
    # None, not ("pytest",): a bare executable name is not on PATH in a
    # project virtualenv Rudra did not activate, and is wrong outright for a
    # Django or stdlib-unittest project. resolve_test_command works it out
    # from the project's own layout. TODO.md A1.33(b).
    test_command=None,
    skip_dirs=frozenset(
        {
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".venv",
            "venv",
            ".tox",
            "build",
            "dist",
        }
    ),
    specificity=10,
)
```

Check `tests/test_stacks.py::test_non_node_stacks_declare_their_test_command` — it asserts non-Node profiles declare a command, and Python no longer does. Update it to exclude Python and add a comment naming `A1.33(b)`, rather than deleting the assertion for Rust.

- [ ] **Step 4: Extend `resolve_test_command`**

In `src/rudra/stacks/detect.py`, add above `resolve_test_command`:

```python
_VENV_DIRS = (".venv", "venv")
# Windows puts executables in Scripts/, POSIX in bin/. Checking both costs
# two stat calls and avoids a platform branch.
_VENV_BIN_DIRS = ("bin", "Scripts")


def _venv_executable(project_path: Path, name: str) -> Path | None:
    for venv in _VENV_DIRS:
        for binaries in _VENV_BIN_DIRS:
            for suffix in ("", ".exe"):
                candidate = project_path / venv / binaries / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate
    return None


def _declares_pytest(project_path: Path) -> bool:
    """Does the project name pytest anywhere obvious?

    Text matching, not parsing: pytest can be declared in [project]
    dependencies, a dependency-group, [tool.poetry], or requirements.txt,
    and parsing all four to answer one yes/no question is not worth it.
    """
    for filename in ("pyproject.toml", "requirements.txt", "setup.py"):
        path = project_path / filename
        try:
            if "pytest" in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _python_test_command(project_path: Path) -> list[str]:
    """The launchable argv for a Python project's tests.

    Ordered by how specific the evidence is. A project virtualenv is the
    strongest signal available, because it is the interpreter the project's
    own tooling uses -- and it is exactly what a bare `pytest` misses
    (TODO.md A1.33(b)).

    Never falls back to Rudra's own interpreter or venv: D18 is explicit
    that Rudra being a Python project and the target being one must not be
    conflated.
    """
    pytest_bin = _venv_executable(project_path, "pytest")
    if pytest_bin is not None:
        return [str(pytest_bin)]

    python_bin = _venv_executable(project_path, "python")
    if python_bin is not None:
        return [str(python_bin), "-m", "pytest"]

    if (project_path / "manage.py").is_file():
        return ["python", "manage.py", "test"]
    if _declares_pytest(project_path):
        return ["python", "-m", "pytest"]
    return ["python", "-m", "unittest", "discover"]
```

Then in `resolve_test_command`, before the Node branch:

```python
    if profile.test_command is not None:
        return list(profile.test_command)

    if profile.name == "python":
        return _python_test_command(Path(project_path))

    scripts = _load_package_json(Path(project_path)).get("scripts")
```

Update the module docstring's claim that it "does not execute anything" — still true, this reads the filesystem only — and extend `resolve_test_command`'s docstring to note the Python branch.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_stacks.py -q`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 6: Verify against this repository**

Run: `.venv/bin/python -c "
from pathlib import Path
from rudra.stacks.detect import resolve_test_command
from rudra.stacks.registry import PYTHON
print(resolve_test_command(Path('.'), PYTHON))
"`
Expected: `['.venv/bin/pytest']` as an absolute path — Rudra's own venv, because Rudra *is* the target here. That is the correct answer and demonstrates the fix: the old code returned `['pytest']`, which does not resolve.

- [ ] **Step 7: Mark `A1.33(b)` in `TODO.md`**

`A1.33` groups three findings. Edit the Item cell to record (b) as fixed here with its evidence, leaving (a) and (c) `PENDING` and saying so explicitly. Do not change the row's Status cell to DONE — two thirds of it are still open.

- [ ] **Step 8: Commit**

```bash
git add src/rudra/stacks/ tests/test_stacks.py TODO.md
git commit -m "fix(stacks): a Python test command that can launch

A1.33(b). test_command was ('pytest',) -- a bare executable name, not on
PATH in any project virtualenv Rudra did not activate, and wrong outright
for Django or stdlib unittest. C3.6 consumes this field, so shipping the
runner against it would report a broken environment as 'no tests'.

Resolution is ordered by evidence strength: project venv pytest, venv
interpreter, manage.py, declared pytest, unittest. It reads the target
project's layout only -- never Rudra's own venv (D18)."
```

---

## Task 8: `testing/parse.py` — counts from real output

**Files:**
- Create: `src/rudra/testing/parse.py`
- Test: `tests/test_testing_parse.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Counts` — frozen dataclass with `total: int | None`, `failed: int | None`, `skipped: int | None`
  - `parse_counts(stack: str | None, stdout: str, stderr: str) -> Counts`

Task 9 calls `parse_counts`.

**Fixtures are captured, never invented.** The pytest and cargo strings below were produced on 2026-08-11 by running real failing suites; reproduce them verbatim. The node one likewise. Jest you capture yourself in Step 4.

- [ ] **Step 1: Write the failing tests with the captured fixtures**

Create `tests/test_testing_parse.py`:

```python
"""Count parsing against REAL runner output.

Every fixture here was captured from an actual failing run. A regex written
against invented output tests the regex against itself.
"""

from __future__ import annotations

from rudra.testing.parse import Counts, parse_counts

# Captured 2026-08-11: pytest 9.x, one pass / one fail / one skip.
PYTEST_MIXED = """\
=========================== short test summary info ============================
FAILED test_sample.py::test_bad - assert (2 + 2) == 5
1 failed, 1 passed, 1 skipped in 0.02s
"""

# Captured 2026-08-11 from this repository's own suite.
PYTEST_ALL_PASS = "516 passed, 2 skipped, 35 warnings in 2.97s\n"

# Captured 2026-08-11: cargo 1.x, one pass / one fail / one ignored.
CARGO_MIXED = """\
failures:
    tests::bad

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
"""

# Captured 2026-08-11: node --test, one pass / one fail / one skip.
NODE_MIXED = """\
ℹ tests 3
ℹ suites 0
ℹ pass 1
ℹ fail 1
ℹ cancelled 0
ℹ skipped 1
"""


def test_pytest_mixed_counts():
    counts = parse_counts("python", PYTEST_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_pytest_all_passing_counts():
    counts = parse_counts("python", PYTEST_ALL_PASS, "")
    assert counts.failed == 0
    assert counts.skipped == 2
    assert counts.total == 518


def test_cargo_mixed_counts():
    counts = parse_counts("rust", CARGO_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_cargo_sums_multiple_test_binaries():
    # cargo prints one `test result:` line per binary; a crate with a lib and
    # an integration test prints two, and only the sum is meaningful.
    doubled = CARGO_MIXED + "\ntest result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
    counts = parse_counts("rust", doubled, "")
    assert counts.total == 7
    assert counts.failed == 1


def test_node_mixed_counts():
    counts = parse_counts("node", NODE_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_unrecognised_output_degrades_to_none_rather_than_guessing():
    counts = parse_counts("python", "some tool printed something else entirely", "")
    assert counts == Counts(None, None, None)


def test_unknown_stack_degrades_to_none():
    assert parse_counts(None, PYTEST_MIXED, "") == Counts(None, None, None)


def test_output_on_stderr_is_parsed_too():
    # cargo writes its summary to stdout, but a wrapper script may redirect.
    counts = parse_counts("rust", "", CARGO_MIXED)
    assert counts.failed == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_testing_parse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.testing'`.

- [ ] **Step 3: Write `src/rudra/testing/parse.py`**

```python
"""Turning a test runner's own summary line into counts. Pure, no I/O.

Deliberately shallow. `pytest --json-report` needs a third-party plugin
Rudra cannot install into a user's project, and `cargo test --format json`
is nightly-only, so a precise path would exist for some stacks and not
others -- and Step 9 would have to handle the degraded shape regardless
(Step 8 spec §6.3). So the degraded shape is the contract.

Counts are None when nothing matched. That is not a failure: the exit code
is always authoritative, and a guessed count is worse than no count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# pytest: "1 failed, 1 passed, 1 skipped in 0.02s"
_PYTEST_PAIR = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed)")

# cargo: "test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; ..."
_CARGO_LINE = re.compile(
    r"test result:\s+\S+\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored"
)

# node --test: "ℹ pass 1" / "ℹ fail 1" / "ℹ skipped 1" / "ℹ tests 3"
_NODE_LINE = re.compile(r"^\W*\b(tests|pass|fail|skipped)\s+(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Counts:
    """How many tests ran, failed, and were skipped. None means unparsed."""

    total: int | None
    failed: int | None
    skipped: int | None


_UNKNOWN = Counts(None, None, None)


def _parse_pytest(text: str) -> Counts:
    """pytest's last summary line wins; earlier lines may be per-file noise."""
    for line in reversed(text.strip().splitlines()):
        if " in " not in line:
            continue
        pairs = _PYTEST_PAIR.findall(line)
        if not pairs:
            continue
        tally: dict[str, int] = {}
        for number, label in pairs:
            tally[label.rstrip("s") if label.startswith("error") else label] = int(number)
        passed = tally.get("passed", 0)
        failed = tally.get("failed", 0) + tally.get("error", 0)
        skipped = tally.get("skipped", 0)
        return Counts(total=passed + failed + skipped, failed=failed, skipped=skipped)
    return _UNKNOWN


def _parse_cargo(text: str) -> Counts:
    """Sum every binary's line: a crate prints one per test target."""
    matches = _CARGO_LINE.findall(text)
    if not matches:
        return _UNKNOWN
    passed = sum(int(m[0]) for m in matches)
    failed = sum(int(m[1]) for m in matches)
    ignored = sum(int(m[2]) for m in matches)
    return Counts(total=passed + failed + ignored, failed=failed, skipped=ignored)


def _parse_node(text: str) -> Counts:
    found = {label: int(number) for label, number in _NODE_LINE.findall(text)}
    if "pass" not in found and "fail" not in found:
        return _UNKNOWN
    total = found.get("tests")
    failed = found.get("fail", 0)
    skipped = found.get("skipped", 0)
    if total is None:
        total = found.get("pass", 0) + failed + skipped
    return Counts(total=total, failed=failed, skipped=skipped)


_PARSERS = {
    "python": _parse_pytest,
    "rust": _parse_cargo,
    "node": _parse_node,
    "react": _parse_node,
    "angular": _parse_node,
}


def parse_counts(stack: str | None, stdout: str, stderr: str) -> Counts:
    """Counts for `stack`, or all-None when nothing recognisable was found.

    Both streams are searched: a runner invoked through a wrapper script may
    have had its summary redirected.
    """
    parser = _PARSERS.get(stack or "")
    if parser is None:
        return _UNKNOWN
    for text in (stdout, stderr):
        if not text:
            continue
        counts = parser(text)
        if counts != _UNKNOWN:
            return counts
    return _UNKNOWN


__all__ = ["Counts", "parse_counts"]
```

- [ ] **Step 4: Capture a real jest fixture and extend the Node parser**

Node's `npm test` runs whatever `scripts.test` declares — jest, vitest, mocha, or karma — so `node --test` alone does not cover the common case.

Run, in a scratch directory outside this repository:

```bash
cd "$(mktemp -d)"
npm init -y >/dev/null
npm install --save-dev jest >/dev/null 2>&1
mkdir -p __tests__
cat > __tests__/x.test.js <<'EOF'
test('ok', () => expect(1).toBe(1));
test('bad', () => expect(2 + 2).toBe(5));
test.skip('skipped', () => {});
EOF
npx jest 2>&1 | tail -8
```

Copy the `Tests:` summary line **verbatim** into `tests/test_testing_parse.py` as a `JEST_MIXED` fixture, add a test asserting its counts, and add a `_JEST_LINE` regex to `_parse_node` that is tried before `_NODE_LINE`.

If the machine is offline and `npm install` fails, do **not** invent the fixture. Instead: leave `_parse_node` as it is, add `@pytest.mark.skip(reason="jest fixture not yet captured — needs network")` on a placeholder test naming what must be captured, and record the gap in the Task 12 ledger entry. An unparsed jest run degrades to `Counts(None, None, None)`, which is a correct answer — a wrong regex is not.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_testing_parse.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/testing/parse.py tests/test_testing_parse.py
git commit -m "feat(testing): count parsing from real runner output

Shallow on purpose: pytest --json-report needs a plugin Rudra cannot install
into a user's project and cargo's JSON output is nightly-only, so a precise
path would exist for some stacks and not others -- and the fix loop would
have to handle the degraded shape anyway. So the degraded shape is the
contract, and counts are None when nothing matched.

Every fixture is captured from a real failing run, not written from memory."
```

---

## Task 9: `testing/runner.py` — `TestResult` and `run_tests`

**Files:**
- Create: `src/rudra/testing/__init__.py`
- Create: `src/rudra/testing/runner.py`
- Test: `tests/test_testing_runner.py` (create)

**Interfaces:**
- Consumes: `run_gated`/`CommandResult` (Task 3), `parse_counts`/`Counts` (Task 8), `detect`/`resolve_test_command` (Task 7), `rudra_paths` from `rudra.state.paths`.
- Produces:
  - `TestResult` — frozen dataclass with `available: bool`, `command: tuple[str, ...] | None`, `stack: str | None`, `exit_code: int | None`, `passed: bool`, `total/failed/skipped: int | None`, `output_tail: str`, `launch_error: str | None`, `denied: bool`, `timed_out: bool`
  - `run_tests(project_path: Path, *, gate, console, cfg) -> TestResult`
  - `MAX_TAIL_CHARS: int = 8000`

Task 10 wraps `run_tests`. Step 9's fix loop consumes `TestResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_testing_runner.py`. Copy `_AutoGate` from `tests/test_git_core.py`.

```python
def _env(tmp_path: Path):
    from rudra.config import build_config

    return {
        "gate": _AutoGate(tmp_path),
        "console": Console(),
        "cfg": build_config(project_root=tmp_path),
    }


def test_a_project_with_no_test_command_is_unavailable_not_failed(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    result = run_tests(tmp_path, **_env(tmp_path))
    assert result.available is False
    assert result.passed is False
    assert result.launch_error is None, "no command declared is not a launch failure"


def test_a_greenfield_directory_is_unavailable(tmp_path: Path):
    result = run_tests(tmp_path, **_env(tmp_path))
    assert result.available is False
    assert result.stack is None


def test_a_passing_python_suite_is_reported_as_passed(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(tmp_path, **_env(tmp_path))
    assert result.available is True
    assert result.stack == "python"
    assert result.exit_code == 0
    assert result.passed is True
    assert result.failed == 0


def test_a_failing_python_suite_reports_counts_and_a_tail(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_bad.py").write_text(
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert 2 + 2 == 5\n",
        encoding="utf-8",
    )
    result = run_tests(tmp_path, **_env(tmp_path))
    assert result.passed is False
    assert result.exit_code != 0
    assert result.failed == 1
    assert "test_bad" in result.output_tail


def test_full_output_is_written_to_the_run_log(tmp_path: Path):
    from rudra.state.paths import rudra_paths

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    run_tests(tmp_path, **_env(tmp_path))
    log_file = rudra_paths(tmp_path).logs / "tests.log"
    assert log_file.exists()
    assert "test_ok" in log_file.read_text(encoding="utf-8")


def test_the_tail_is_capped(tmp_path: Path):
    from rudra.testing.runner import MAX_TAIL_CHARS

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    body = "\n".join(f"def test_n{n}():\n    assert True" for n in range(400))
    (tmp_path / "test_many.py").write_text(body, encoding="utf-8")
    result = run_tests(tmp_path, **_env(tmp_path))
    assert len(result.output_tail) <= MAX_TAIL_CHARS + 200


def test_a_missing_binary_is_a_launch_error_not_an_absent_suite(tmp_path: Path):
    """'no tests' and 'could not start' must never look alike to the gate."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    env = _env(tmp_path)
    result = run_tests(tmp_path, _command_override=["not-a-real-binary-xyz"], **env)
    assert result.available is True
    assert result.launch_error is not None
    assert result.passed is False


def test_a_denied_run_is_reported_as_denied(tmp_path: Path):
    from rudra.config import build_config
    from rudra.permissions.audit import AuditLog
    from rudra.permissions.rules import PermissionEngine

    class _DenyGate:
        def __init__(self) -> None:
            self.engine = PermissionEngine(
                mode="auto",
                allow=(),
                deny=(),
                floor_disable=(),
                project_root=tmp_path,
                shell_in_auto=False,  # A1.49
            )
            self.audit = AuditLog(tmp_path / "audit.jsonl")
            self.mode = "auto"

        def prompt(self, requests, console):  # pragma: no cover
            raise AssertionError("auto mode must not prompt")

    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(
        tmp_path,
        gate=_DenyGate(),
        console=Console(),
        cfg=build_config(project_root=tmp_path),
    )
    assert result.denied is True
    assert result.passed is False
```

The `_command_override` keyword exists only for that one test — it is how a missing binary is exercised without depending on the machine lacking cargo. Give it a leading underscore and document it as test-only.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_testing_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.testing.runner'`.

- [ ] **Step 3: Write `src/rudra/testing/runner.py`**

```python
"""Running the project's tests and reporting what happened.

This is C3.6's deliverable and Step 9's input. The fix loop (C6.5) and the
deterministic completion gate (C6.6) call `run_tests` directly, with no
model in the loop -- which is why the Python function is the primary
artefact and the tool wrapping it is thin (Step 8 spec S8.1).

Gated as `execute`, so under `--auto` without `--allow-shell` it is denied
and no tests run (A1.49). That is correct and stays: pytest executes the
test files the *model* wrote, so it is a genuine arbitrary-code path, not a
lesser one. The consequence -- unattended runs cannot self-verify unless
the user opts into shell -- is stated in the spec §2.5 rather than worked
around here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated
from rudra.stacks.detect import detect, resolve_test_command
from rudra.state.paths import rudra_paths
from rudra.testing.parse import parse_counts

# Roughly a page of failure output. Enough for a traceback, small enough
# that it does not dominate a 32B context (D6).
MAX_TAIL_CHARS = 8000


@dataclass(frozen=True)
class TestResult:
    """What one test run did.

    `available=False` means the project declares no test command -- a real
    answer, not a failure (stacks/detect.py). `launch_error` is separate on
    purpose: "no test command" and "the command would not start" must never
    look alike to a completion gate, because collapsing them reports a broken
    environment as a project that simply has no tests.
    """

    available: bool
    command: tuple[str, ...] | None = None
    stack: str | None = None
    exit_code: int | None = None
    passed: bool = False
    total: int | None = None
    failed: int | None = None
    skipped: int | None = None
    output_tail: str = ""
    launch_error: str | None = None
    denied: bool = False
    timed_out: bool = False


def _tail(text: str, limit: int = MAX_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n{text[-limit:]}"


def _write_log(project_path: Path, text: str) -> None:
    """Full output to .rudra/run/logs/tests.log.

    D17's intent -- big output to a log, short preview returned. C3.2 was
    right to decline building this for tool results, since deepagents evicts
    those; a Python-side call gets no such eviction (see also A1.47).
    """
    logs = rudra_paths(project_path).logs
    try:
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "tests.log").write_text(text, encoding="utf-8")
    except OSError:
        # A log is never worth failing a run over.
        pass


def run_tests(
    project_path: Path,
    *,
    gate: Any,
    console: Console,
    cfg: Any,
    _command_override: list[str] | None = None,
) -> TestResult:
    """Run this project's tests.

    `_command_override` is test-only: it exercises the missing-binary path
    without depending on the machine lacking a toolchain.
    """
    project_path = Path(project_path)
    profiles = detect(project_path)
    profile = profiles[0] if profiles else None

    command = _command_override or (
        resolve_test_command(project_path, profile) if profile is not None else None
    )
    stack = profile.name if profile is not None else None

    if command is None:
        return TestResult(available=False, stack=stack)

    result = run_gated(
        command,
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=cfg.tools.test_timeout,
        env=scrubbed_env(cfg),
    )

    combined = f"{result.stdout}\n{result.stderr}".strip()
    _write_log(project_path, combined)

    if result.denied:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            denied=True,
            output_tail=result.denial_reason or "denied by the permission gate",
        )

    if result.timed_out:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            timed_out=True,
            output_tail=_tail(combined),
        )

    if result.exit_code is None:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            launch_error=result.stderr.strip() or "the test command could not be started",
            output_tail=_tail(combined),
        )

    counts = parse_counts(stack, result.stdout, result.stderr)
    return TestResult(
        available=True,
        command=result.argv,
        stack=stack,
        exit_code=result.exit_code,
        passed=result.exit_code == 0,
        total=counts.total,
        failed=counts.failed,
        skipped=counts.skipped,
        output_tail=_tail(combined),
    )


__all__ = ["MAX_TAIL_CHARS", "TestResult", "run_tests"]
```

- [ ] **Step 4: Write `src/rudra/testing/__init__.py`**

```python
"""Running the project's tests (Step 8, C3.6)."""

from __future__ import annotations

from rudra.testing.parse import Counts, parse_counts
from rudra.testing.runner import MAX_TAIL_CHARS, TestResult, run_tests

__all__ = ["MAX_TAIL_CHARS", "Counts", "TestResult", "parse_counts", "run_tests"]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_testing_runner.py -q`
Expected: PASS, 8 tests.

Note the Python-suite tests resolve to `["python", "-m", "pytest"]` (no venv in `tmp_path`), which runs against whatever `python` is on `PATH`. If that interpreter has no pytest, those tests will report a launch error instead. Should that happen, change the fixture to create a `.venv/bin/python` symlink to `sys.executable` so resolution finds Rudra's own interpreter *as the target project's venv* — which is legitimate here, because in that fixture the temp directory genuinely is the project.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/testing/ tests/test_testing_runner.py
git commit -m "feat(testing): run the project's tests, report structured results

C3.6. The Python function is the primary artefact because Step 9's fix loop
and completion gate call it directly, with no model in the loop (S8.1).

launch_error is separate from available: 'no test command declared' and
'the command would not start' must never look alike to a completion gate,
or a broken environment gets reported as a project with no tests.

Full output goes to .rudra/run/logs/tests.log per D17; only a capped tail
travels in the result."
```

---

## Task 10: Register both tools on the planner

**Files:**
- Create: `src/rudra/testing/tools.py`
- Modify: `src/rudra/agent/planner_agent.py:102-104`
- Test: `tests/test_testing_tools.py` (create)
- Test: `tests/test_agent_wiring.py` (extend)

**Interfaces:**
- Consumes: `run_tests`/`TestResult` (Task 9), `create_git_tools` (Task 6), `WRAPPED_EXECUTE_TOOLS` (Task 2).
- Produces: `create_testing_tools(project_path, *, gate, console, cfg) -> list` returning `[run_tests]`. The planner's tool list gains `git_diff` and `run_tests`.

**Why the planner and not the coder:** the coder's system prompt says *"After write_file() returns successfully, STOP. Do not write more files"* (`coder_agent.py:56`). Handing it a test runner would contradict its own instructions inside the same context window. The planner is the only agent with a tool list and it persists across the run. Step 9 revisits this when subagents land (`C6.2`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_testing_tools.py`:

```python
def test_run_tests_tool_reports_a_passing_suite(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    tool = _run_tests_tool(tmp_path)
    text = tool.invoke({})
    assert "pass" in text.lower()


def test_run_tests_tool_reports_failures_with_the_tail(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    text = _run_tests_tool(tmp_path).invoke({})
    assert "fail" in text.lower()
    assert "test_bad" in text


def test_run_tests_tool_says_so_when_there_is_no_suite(tmp_path: Path):
    text = _run_tests_tool(tmp_path).invoke({})
    assert "no test" in text.lower()


def test_run_tests_tool_surfaces_a_denial_as_guidance(tmp_path: Path):
    # A denied run must tell the model why and not to retry, or it loops.
    text = _run_tests_tool(tmp_path, deny=True).invoke({})
    assert "not permitted" in text.lower() or "denied" in text.lower()
```

Add to `tests/test_agent_wiring.py`:

```python
def test_planner_registers_the_step_8_tools(tmp_path: Path):
    from rudra.permissions.rules import WRAPPED_EXECUTE_TOOLS

    names = _planner_tool_names(tmp_path)  # reuse this file's existing helper
    assert "git_diff" in names
    assert "run_tests" in names
    assert WRAPPED_EXECUTE_TOOLS <= set(names), (
        "every wrapped-execute name must actually be registered, or the "
        "engine allows a tool that does not exist"
    )


def test_coder_still_has_no_tools(tmp_path: Path):
    # coder_agent.py:56 tells it to stop after one write_file. A test runner
    # would contradict that in the same context window. Step 9 revisits this.
    assert _coder_tool_names(tmp_path) == []
```

If `tests/test_agent_wiring.py` has no such helpers, write them by constructing the agents the way that file already does and reading `tool.name` off the tool list.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_testing_tools.py tests/test_agent_wiring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.testing.tools'`.

- [ ] **Step 3: Write `src/rudra/testing/tools.py`**

```python
"""The test runner, as a tool the model can call.

Thin on purpose. The Python function is what Step 9's loop uses; this exists
so the agent is not blind to test state mid-task (Step 8 spec S8.1). It
returns a short summary rather than the raw dump -- the full output is
already on disk at .rudra/run/logs/tests.log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from rich.console import Console

from rudra.testing.runner import TestResult, run_tests as _run_tests


def _summarise(result: TestResult) -> str:
    if not result.available:
        return (
            "This project declares no test command, so there is nothing to run. "
            "Do not retry — add tests and a test configuration first if the task needs them."
        )
    if result.denied:
        return (
            f"Running tests was not permitted: {result.output_tail}. Do not retry this call. "
            "The user can allow it with --allow-shell or an explicit allow rule."
        )
    if result.timed_out:
        return (
            f"The test command timed out and was killed. Command: {' '.join(result.command or ())}. "
            "Last output:\n" + result.output_tail
        )
    if result.launch_error is not None:
        return (
            f"The test command could not be started: {result.launch_error}. "
            f"Command: {' '.join(result.command or ())}."
        )

    headline = "All tests passed." if result.passed else "Tests failed."
    counted = ""
    if result.total is not None:
        counted = f" {result.total} run, {result.failed} failed, {result.skipped} skipped."
    if result.passed:
        return f"{headline}{counted}"
    return f"{headline}{counted}\n\nOutput:\n{result.output_tail}"


def create_testing_tools(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list:
    """The test-running tools for one run. Currently exactly one."""

    @tool
    def run_tests() -> str:
        """Run this project's test suite and report what happened.

        Rudra works out the right command from the project's own layout —
        pytest, cargo test, or the package.json test script. Call this after
        writing code to check whether it works.

        Returns:
            A short summary, plus failure output when there is any.
        """
        return _summarise(_run_tests(project_path, gate=gate, console=console, cfg=cfg))

    return [run_tests]


__all__ = ["create_testing_tools"]
```

- [ ] **Step 4: Register both on the planner**

In `src/rudra/agent/planner_agent.py`, replace the `custom_tools` assignment (lines 102-104):

```python
    custom_tools = (
        create_planning_tools(project_path, task=task)
        + create_interaction_tools(console, project_path)
        + create_git_tools(project_path, gate=gate, console=console, cfg=cfg)
        + create_testing_tools(project_path, gate=gate, console=console, cfg=cfg)
    )
```

Add the two imports at the top of the file. `cfg` is already in scope (`planner_agent.py:100`).

Extend the planner prompt's `## RULES` block (`planner_agent.py:58-64`) with:

```
- Call run_tests() after the files are written to check whether they work
- Do NOT run `git commit` or `git push` unless the task explicitly asks for it
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_testing_tools.py tests/test_agent_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/testing/tools.py src/rudra/agent/planner_agent.py tests/test_testing_tools.py tests/test_agent_wiring.py
git commit -m "feat(agent): register git_diff and run_tests on the planner

The coder stays tools=[]: its prompt tells it to stop after one write_file,
and a test runner would contradict that in the same context window. Step 9
revisits this when subagents land (C6.2).

A denied run returns guidance naming the escape hatch rather than a bare
error, so the model adapts instead of retrying something that cannot succeed."
```

---

## Task 11: Wire `auto_branch` into the run

**Files:**
- Modify: `src/rudra/agent/main_agent.py:361-376`
- Test: `tests/test_main_agent_helpers.py` (extend)

**Interfaces:**
- Consumes: `auto_branch`, `BranchOutcome` (Task 5), `cfg.tools.auto_branch` (Task 1).
- Produces: nothing new. This is the last wiring step.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main_agent_helpers.py`:

```python
def test_maybe_auto_branch_is_a_no_op_when_disabled(tmp_path: Path, monkeypatch):
    from rudra.agent import main_agent

    called = False

    def _spy(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("auto_branch must not be called when the key is off")

    monkeypatch.setattr(main_agent, "auto_branch", _spy)
    cfg = _config_with_auto_branch(tmp_path, enabled=False)
    main_agent._maybe_auto_branch(tmp_path, "task", cfg=cfg, gate=None, console=Console())
    assert called is False


def test_maybe_auto_branch_reports_the_branch_it_made(tmp_path: Path, monkeypatch, capsys):
    from rudra.agent import main_agent
    from rudra.git.core import BranchOutcome

    monkeypatch.setattr(
        main_agent, "auto_branch", lambda *a, **k: BranchOutcome("rudra/task", None)
    )
    console = Console()
    main_agent._maybe_auto_branch(
        tmp_path, "task", cfg=_config_with_auto_branch(tmp_path, enabled=True),
        gate=None, console=console,
    )
    assert "rudra/task" in capsys.readouterr().out


def test_maybe_auto_branch_prints_the_skip_reason(tmp_path: Path, monkeypatch, capsys):
    from rudra.agent import main_agent
    from rudra.git.core import BranchOutcome

    monkeypatch.setattr(
        main_agent,
        "auto_branch",
        lambda *a, **k: BranchOutcome(None, "the working tree has uncommitted changes"),
    )
    main_agent._maybe_auto_branch(
        tmp_path, "task", cfg=_config_with_auto_branch(tmp_path, enabled=True),
        gate=None, console=Console(),
    )
    assert "uncommitted" in capsys.readouterr().out
```

Write `_config_with_auto_branch` as a local helper that writes a project TOML with `[tools] auto_branch = true|false` and calls `build_config`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main_agent_helpers.py -q -k auto_branch`
Expected: FAIL — `AttributeError: module 'rudra.agent.main_agent' has no attribute '_maybe_auto_branch'`.

- [ ] **Step 3: Add the helper to `main_agent.py`**

Import at the top of the file:

```python
from rudra.git.core import auto_branch
```

Add near the other module-level helpers (beside `_ensure_agents_md`, around line 463):

```python
def _maybe_auto_branch(project_path: Path, task: str, *, cfg, gate, console: Console) -> None:
    """Branch before the run, when the user asked for it and it is safe.

    Off by default (TODO.md Step 8 spec S8.3). Both outcomes are printed:
    silence would leave the user unable to tell whether it ran, which is the
    complaint A1.50 recorded against a setting that accepted a name and then
    did nothing.
    """
    if not cfg.tools.auto_branch:
        return

    outcome = auto_branch(project_path, task, gate=gate, console=console, cfg=cfg)
    if outcome.branch is not None:
        console.print(f"[dim]Working on branch {outcome.branch}[/dim]")
    else:
        console.print(f"[yellow]No branch created: {outcome.skipped_reason}.[/yellow]")
```

- [ ] **Step 4: Call it from `run()`**

In `RudraAgent.run`, immediately after the `dry_run` early return (`main_agent.py:364-371`) and before `self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")`:

```python
            _maybe_auto_branch(
                self.context.project_path,
                self.context.task,
                cfg=get_config(),
                gate=self.gate,
                console=self.context.console,
            )
```

Placement matters in both directions: after `dry_run`, so `--dry-run` creates nothing; before the planner, so the plan and every file it produces land on the new branch.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_main_agent_helpers.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: green.

`ruff` matters more than usual here: `create_main_agent` has no unit coverage, and Step 6 recorded a dangling reference in that same function being caught by ruff rather than pytest.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/agent/main_agent.py tests/test_main_agent_helpers.py
git commit -m "feat(agent): call auto_branch at the start of a run

After the dry_run return so --dry-run creates nothing, before the planner so
the plan and every file it produces land on the new branch.

Both outcomes print. Silence would leave the user unable to tell whether it
ran -- the complaint A1.50 recorded against a setting that accepted a name
and then quietly did nothing."
```

---

## Task 12: Documentation, ledger, and acceptance

**Files:**
- Modify: `CLAUDE.md` §3 (architecture tree), §6 (config example), §8 (commands)
- Modify: `TODO.md` — `C3.5`, `C3.6`, Step 8's execution-order row
- Modify: `Documentation/` — the shell/permissions page gains the new keys
- Test: none new; this task runs the acceptance suite

**Interfaces:**
- Consumes: everything.
- Produces: a closed step.

- [ ] **Step 1: Run the four acceptance runs**

All from a `mktemp -d` outside this repository, under `env -i` with no `RUDRA_*`/`OLLAMA_*` set, configured only by `.rudra/config.toml`, against a real model.

**Run 1 — `ask` through a real pty.** Drive it through a pty so `isatty()` is genuinely true (Step 7's acceptance did the same). Give it a task that writes a small Python module with a test, then let it call `run_tests`.
Capture: the approval panel text, that it reads `execute` with the real `pytest` command and not `run_tests`, and the resulting audit line.

**Run 2 — `--auto` alone.** Same task.
Capture: `run_tests` denied with `source: "auto-shell"`, and that the run still completes rather than crashing.

**Run 3 — `--auto --allow-shell`.** Same task.
Capture: tests actually execute, counts parse, `.rudra/run/logs/tests.log` holds the full output, and `output_tail` holds the tail.

**Run 4 — `auto_branch = true`.** Once in a clean git repo, once in a dirty one.
Capture: `rudra/<slug>` created in the first; a printed skip reason and an unchanged branch in the second.

- [ ] **Step 2: Record the evidence in `TODO.md`, not a scratch file**

Set `C3.5` and `C3.6` to `**DONE** 2026-08-11` with the measured evidence inline: exact commands, audit lines quoted, exit codes, the generated files. `U.12`'s `/tmp` smoke logs no longer resolve, which is why the evidence goes in the row.

Update Step 8's execution-order row the way Steps 5–7's rows read: what closed, what stayed open and why, the measured diff (`git diff --stat main..HEAD -- src/ tests/`), the final verification output, and the sentence naming what Step 9 is now unblocked to do.

If the jest fixture from Task 8 Step 4 could not be captured, say so explicitly in the row and note that Node/React/Angular count parsing degrades to `None` until it is.

- [ ] **Step 3: Update `CLAUDE.md`**

- §3's architecture tree: add `shell/`, `git/`, `testing/` with one-line descriptions.
- §6's config example: add `auto_branch` and `test_timeout` to the `[tools]` block.
- §8's command list: note that `--auto` alone runs no tests, and that `--auto --allow-shell` is what a loop-engineering run wants.
- §8's test count: replace the stale `489 passed, 2 skipped` with the number this step actually ends on.

- [ ] **Step 4: Update `Documentation/`**

Find the page covering shell execution and permissions (`git log --oneline -- Documentation/` will name it — `d93541c` touched it). Add the two new `[tools]` keys and the commented git deny rules, matching that page's existing voice.

- [ ] **Step 5: Final verification**

Run:
```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
.venv/bin/rudra doctor --offline
```
Expected: `All checks passed!`, formatting clean, tests at or above `516 passed, 2 skipped`, `Rudra v0.2.0`, and `doctor` exiting cleanly.

Paste the real output into the `TODO.md` row. Do not paraphrase it.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md TODO.md Documentation/
git commit -m "docs: close out Step 8 — git tools and the test runner"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §4.1 module layout | 3, 4, 6, 8, 9, 10 |
| §4.2 gated as `execute` | 3 |
| §4.3 `run_gated` | 3 |
| §4.4 read-only bypass + `git_diff` path containment | 3 (flag), 4 (frozenset), 6 (containment) |
| §5.1 git surface | 4, 6 |
| §5.3 auto-branch | 5, 11 |
| §5.4 never commit unless asked | 1 (template), 10 (prompt rule) |
| §6.1 resolution + `A1.33(b)` | 7 |
| §6.2 `TestResult` | 9 |
| §6.3 parse depth | 8 |
| §6.4 output offload | 9 |
| §6.5 planner-only registration | 10 |
| §6.6 `A1.51` | 2 |
| §7 error handling | 3, 4, 5, 9 |
| §7 `test_timeout` key | 1 |
| §9 testing | every task |
| §10 acceptance | 12 |
| §11 ledger deltas | 2, 7, 12 |

No gaps.

**Type consistency checked:** `CommandResult` (Task 3) is consumed as `.ok`/`.stdout`/`.stderr`/`.denied`/`.denial_reason`/`.timed_out`/`.exit_code`/`.argv` in Tasks 4, 5, 6, 9 — all defined. `Counts` (Task 8) is consumed as `.total`/`.failed`/`.skipped` in Task 9 — defined. `BranchOutcome` (Task 5) is consumed as `.branch`/`.skipped_reason` in Task 11 — defined. `core.GIT_TIMEOUT_SECONDS` is referenced in Task 6 and defined in Task 4. `WRAPPED_EXECUTE_TOOLS` is defined in Task 2 and asserted in Task 10.

**One ordering constraint that must not be reordered:** Task 2 before Task 10. Task 10 registers `git_diff` and `run_tests`; on pre-Task-2 code those names are decided `ask`, have no `interrupt_on` entry, and would therefore execute silently and unaudited — the `A1.51` hole, shipped.
