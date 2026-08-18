# Step 7 — Shell Execution and the Permission Layer: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Rudra's agents shell execution, gated by a permission layer that ships in the same step.

**Architecture:** One pure `PermissionEngine.decide()` is consumed by two mechanisms — a Rudra `wrap_tool_call` middleware that short-circuits denials, and deepagents' `interrupt_on` whose `when` predicate calls the same engine for anything needing a human. The backend becomes `CompositeBackend(default=LocalShellBackend(...))` with an `/artifacts/` route so deepagents' built-in oversized-result eviction lands in `.rudra/run/artifacts/` instead of the user's project.

**Tech Stack:** Python 3.12+, `deepagents==0.7.4`, `langchain`/`langgraph`, `wcmatch` (already a dependency), Typer + Rich, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-10-step7-shell-and-permissions-design.md` — read §2 before starting; three probe findings there contradict what `TODO.md` assumes.

## Global Constraints

- `.venv/bin/ruff check src/ tests/` must print `All checks passed!` — absolute gate since Step 3.
- `.venv/bin/ruff format --check src/ tests/` must be clean.
- `.venv/bin/pytest -q` must never drop below the current **286 passed, 2 skipped**.
- Every ledger claim cites `file.py:line` or pasted command output. No claim without evidence (`CLAUDE.md` §2 rule 3).
- **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with evidence first, then fix, then mark `DONE` (`CLAUDE.md` §2 rule 2). Task 1 exists for exactly this.
- `permissions=` is **never** passed to `create_deep_agent`. It raises `NotImplementedError` on any execute-capable backend — spec §2.2.
- No test may require a live model, a network call, or a reachable Ollama. Use the `ScriptedToolModel` pattern from `tests/test_deepagents_contract.py`.
- Never build a `.rudra/...` path by hand. `src/rudra/state/paths.py` is the single source of truth.
- Agent-facing prompt strings naming `.rudra/` paths must move in the same commit as the path — `tests/test_rudra_dir_migration.py` guards this.
- API key values are never logged, never put in an error message, never placed in the shell environment.

---

## File Structure

**New package** `src/rudra/permissions/` — nine modules, each one responsibility:

| File | Responsibility |
|---|---|
| `floor.py` | The three named floor rules. Pure predicates, no config. |
| `rules.py` | `Rule`, `parse_rule`, `Decision`, `PermissionEngine.decide()`. Pure. |
| `grants.py` | In-memory session grants. |
| `audit.py` | JSONL append to `.rudra/run/logs/permissions.jsonl`. |
| `diff.py` | Unified diff rendering for the approval prompt. |
| `env.py` | `scrubbed_env(cfg)` — inherited environment minus secrets. |
| `middleware.py` | `RudraPermissionMiddleware.wrap_tool_call` — deny short-circuit. |
| `interrupts.py` | `build_interrupt_on(engine)` — the `when` predicates. |
| `approval.py` | Terminal prompt + `run_with_approvals` interrupt/resume loop. |
| `__init__.py` | `Gate`, `build_gate(cfg, project_path)`. |

**Modified:**

| File | Change |
|---|---|
| `src/rudra/config/schema.py` | `PermissionsConfig.floor_disable`, new `ToolsConfig`, un-reserve `[tools]` |
| `src/rudra/config/loader.py` | Validate `floor_disable` and `[tools]`; build both |
| `src/rudra/config/layers.py` | `RUDRA_SHELL`, `RUDRA_TOOL_OUTPUT_LIMIT_TOKENS` |
| `src/rudra/config/template.py` | `floor_disable` + a real `[tools]` block |
| `src/rudra/state/paths.py` | `RudraPaths.artifacts` |
| `src/rudra/agent/main_agent.py` | Composite backend; gate wired; two `astream` calls wrapped |
| `src/rudra/agent/coder_agent.py` | Accept and pass `middleware` extras + `interrupt_on` |
| `src/rudra/agent/planner_agent.py` | Same |
| `src/rudra/cli.py` | TTY check; replace the "NOT ENFORCED" notice; `doctor` section |

**Task order and dependencies:**

```
1  ledger  ──> 2 floor ──> 3 rules ──> 4 config ──> 5 audit ──> 6 middleware
                                          │                          │
                                          ├──> 7 env                 │
                                          │                          │
                             8 diff ──> 9 grants+prompt ──> 10 interrupts+resume
                                                                     │
                                                     11 build_gate ──┤
                                                                     │
                                     12 backend swap + paths ────────┤
                                                                     │
                                              13 cli TTY ────────────┤
                                                                     │
                                     14 contract guard + acceptance ─┘
```

---

## Task 1: Log the ledger findings before any code

Session rule 2 is non-negotiable and the owner asked for it explicitly. Nothing in Tasks 2–14 may be written until this is committed.

**Files:**
- Modify: `TODO.md` (Section A1, Phase U rows `U.7` and `U.17`, Section E Step 7 row)

**Interfaces:**
- Consumes: nothing.
- Produces: ledger IDs `A1.44`, `A1.45`, `A1.46` referenced by later tasks' commit messages.

- [ ] **Step 1: Re-run the three probes and capture output verbatim**

Do not copy the spec's quotes. Re-run them so the evidence is this session's:

```bash
.venv/bin/python - <<'EOF'
import tempfile
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.composite import CompositeBackend
from deepagents.middleware.filesystem import FilesystemPermission, FilesystemOperation
import typing

# 1. U.17 — execute registered but non-functional
print("has execute attr:", hasattr(FilesystemBackend, "execute"))

# 2. permissions= vs shell
with tempfile.TemporaryDirectory() as d:
    try:
        create_deep_agent(
            model=None,
            backend=CompositeBackend(
                default=LocalShellBackend(root_dir=d, virtual_mode=True), routes={}
            ),
            permissions=[FilesystemPermission(operations=["write"], paths=["/x/**"], mode="deny")],
        )
    except NotImplementedError as exc:
        print("NotImplementedError:", exc)

# 3. FilesystemOperation never covered execute
print("FilesystemOperation:", typing.get_args(FilesystemOperation))

# 4. empty shell environment
with tempfile.TemporaryDirectory() as d:
    be = LocalShellBackend(root_dir=d, virtual_mode=True)
    print("env probe:", be.execute("echo $PATH; which pytest git ruff"))
EOF
```

- [ ] **Step 2: Add three new `A1` rows**

Append to `TODO.md`'s `### A1 — Correctness bugs` table, keeping the existing column shape (`# | Status | Item | Evidence`):

```markdown
| A1.44 | PENDING | **`LocalShellBackend` defaults to an empty environment, so shipping the default breaks every real toolchain.** `inherit_env: bool = False` (`.venv/.../deepagents/backends/local_shell.py:115`), and when false the environment is `env if env is not None else {}` (`:200-201`) — empty, not minimal. Measured: `LocalShellBackend(root_dir=<tmp>).execute("echo $PATH; which pytest git ruff")` returns `output='/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.\n\nExit code: 1'` — no `pytest`, no `git`, no `ruff`. Every venv, `nvm`, `rustup`, and `pyenv` toolchain is invisible, so Step 8's `C3.5` git tools and `C3.6` test-runner would fail on their first call. Found while designing Step 7. Fixed in Step 7 by passing an explicit environment: `os.environ` minus every role's `api_key_env` and anything matching `(_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$|^AWS_`, per spec §5.3 | `.venv/.../deepagents/backends/local_shell.py:115,200-201`; probe output quoted above |
| A1.45 | PENDING | **`artifacts_root` defaults to the backend root, so a shell-enabled agent writes into the user's project.** `CompositeBackend.__init__`'s `artifacts_root: str = "/"`, and `FilesystemMiddleware` derives `self._large_tool_results_prefix = f"{_root}/large_tool_results"` and `self._conversation_history_prefix = f"{_root}/conversation_history"` from it (`.venv/.../deepagents/middleware/filesystem.py:1674-1678`). With the backend rooted at the project, both resolve inside the repo Rudra is working on — `large_tool_results/` from oversized tool-result eviction, `conversation_history/` from the summarization middleware Rudra inherits automatically (`CLAUDE.md` §4). Not observed in Step 6's acceptance run only because it was short enough that neither fired. Verified the fix: constructing `CompositeBackend(default=LocalShellBackend(...), routes={"/artifacts/": FilesystemBackend(...)}, artifacts_root="/artifacts")` yields `/artifacts/large_tool_results` and `/artifacts/conversation_history`. Found while designing Step 7; fixed there by routing both into `.rudra/run/artifacts/`, which D15 places in the volatile subtree | `.venv/.../deepagents/backends/composite.py` (`artifacts_root` default); `.venv/.../deepagents/middleware/filesystem.py:1674-1678` |
| A1.47 | PENDING | **deepagents' oversized-tool-result threshold is unreachable, so a 32B context keeps every result under 20 000 tokens.** `FilesystemMiddleware.__init__` takes `tool_token_limit_before_evict: int | None = 20000` (`.venv/.../deepagents/middleware/filesystem.py:1602`) and evicts anything larger to `<artifacts_root>/large_tool_results/`. But `create_deep_agent` exposes no parameter for it — `[p for p in inspect.signature(create_deep_agent).parameters if "token" in p or "evict" in p]` → `[]` — and it appends its **own** `FilesystemMiddleware` unconditionally (`.venv/.../deepagents/graph.py:820-826`), so passing a configured instance in `middleware=` produces two filesystem middlewares rather than replacing the default. The only remaining route is assigning the private `_tool_token_limit_before_evict` after construction, which is the U.13 private-API hazard class. 20 000 tokens is roughly a third of a 32B window (D6) for one failing-`pytest` transcript. Found while writing the Step 7 plan; Step 7 deliberately ships **no** config key for it rather than shipping one that silently does nothing — the defect class of `A1.40` and of the inert `[permissions]` Step 6 worked to avoid. Belongs with **C7.x** context budgeting, which owns this number; the honest fixes are an upstream parameter, or a Rudra `wrap_tool_call` that truncates oversized results into `.rudra/run/artifacts/` itself | `.venv/.../deepagents/middleware/filesystem.py:1602`; `.venv/.../deepagents/graph.py:820-826`; `inspect.signature(create_deep_agent)` probe. Cross-ref **A1.45**, **C7.5** |
| A1.46 | PENDING | **`permissions=` never covered `execute`, which §0.8's open MCP question assumed it might.** `FilesystemOperation` is `('read', 'write')` — measured via `typing.get_args`. So even where `permissions=` is accepted, it governs filesystem tools only. §0.8 asks "does an MCP tool call pass through Rudra's permission gate at all, or does it bypass it entirely?" and notes `permissions=` "governs filesystem tools only" — this row supplies the measured basis for that and extends it: `execute` is in the same uncovered category as MCP tools, not a separate one. Consequence for Step 13: whatever `C4.x` builds for MCP gating is the same mechanism Step 7 builds for `execute`, not a second one. No fix — this is a fact about the upstream API, recorded so Step 13 inherits it | `typing.get_args(deepagents.middleware.filesystem.FilesystemOperation)` → `('read', 'write')`; cross-ref **§0.8**, **U.7** |
```

- [ ] **Step 3: Rewrite `U.17` as DONE**

Replace the whole `U.17` row's Status and Item with:

```markdown
| U.17 | **DONE** 2026-08-10 | **`execute` is registered on a plain `FilesystemBackend` but non-functional — `CLAUDE.md` §4 was right and `C3.1`'s premise stands.** The row asked whether registration implied function, since Step 1's probe found `execute` in the default tool stack. It does not. Driven through a real compiled graph with a scripted model emitting one `execute` call: on `FilesystemBackend` the tool returns `status='error'`, content `"Error: Execution not available. This agent's backend does not support command execution (SandboxBackendProtocol). To use the execute tool, provide a backend that implements SandboxBackendProtocol."`; on `LocalShellBackend` the identical call returns `status='success'`, `'hello-from-execute\n\n[Command succeeded with exit code 0]'`. Corroborating: `hasattr(FilesystemBackend, "execute")` → `False`, `isinstance(FilesystemBackend(root_dir=...), SandboxBackendProtocol)` → `False`, `isinstance(LocalShellBackend(root_dir=...), SandboxBackendProtocol)` → `True`. `CLAUDE.md` §4's parenthetical opening this question is retracted in the same commit | probe output quoted above; `CLAUDE.md` §4 |
```

- [ ] **Step 4: Rewrite `U.7` as WONTFIX**

`U.7` currently reads "Adopt `permissions: list[FilesystemPermission]` … This replaces most of hand-rolled C3.3". Replace with:

```markdown
| U.7 | **WONTFIX** 2026-08-10 | **`permissions=` cannot be adopted while shell exists — the two are mutually exclusive in 0.7.4.** `create_deep_agent(permissions=[...], backend=<execute-capable>)` raises at construction: `NotImplementedError: FilesystemMiddleware does not yet support permissions with backends that provide command execution (SandboxBackendProtocol). Tool-level permissions for the execute tool are not implemented. Either remove permissions or use a backend without execution support.` Guard at `.venv/.../deepagents/middleware/filesystem.py:1667-1672`: `_permissions and supports_execution(self.backend) and not _all_paths_scoped_to_routes(_permissions, self.backend)`. The `_all_paths_scoped_to_routes` escape hatch permits the combination only when **every** permission path sits under a `CompositeBackend` route prefix — route prefixes are mounted subtrees like `/skills/`, while project files live on the composite's `default`, which is the execute-capable backend the guard exists to protect against. Independently, `FilesystemOperation` is `('read','write')` (**A1.46**), so `permissions=` never covered `execute` even where accepted. **This row's previous text — "This replaces most of hand-rolled C3.3" — was false on 0.7.4 and is retracted.** `C3.3` is built by Rudra in full: one pure `PermissionEngine` consumed by a `wrap_tool_call` middleware for deny and by `interrupt_on`'s `when` predicate for ask. Recorded here rather than deleted so a future session does not rediscover `permissions=` and re-litigate, the same reason **U.8** keeps `RubricMiddleware`'s rejection on the record. **Revisit trigger:** `tests/test_deepagents_contract.py::test_permissions_still_rejected_with_execute_backend` asserts the `NotImplementedError` still fires; when it starts failing, upstream has lifted the restriction and this row can reopen | `.venv/.../deepagents/middleware/filesystem.py:1667-1672`; error text quoted above; cross-ref **A1.46**, **§0.8** |
```

- [ ] **Step 5: Mark Step 7 IN PROGRESS in Section E**

In the Stage III table, append to the Step 7 row's "Why here" cell:

```markdown
 — **STEP 7 IN PROGRESS 2026-08-10.** Spec: `docs/superpowers/specs/2026-08-10-step7-shell-and-permissions-design.md`. Plan: `docs/superpowers/plans/2026-08-10-step7-shell-and-permissions.md`. Findings `A1.44`–`A1.47` logged before work began per session rule 2; `U.17` closed DONE and `U.7` closed WONTFIX by the same design pass.
```

- [ ] **Step 6: Verify nothing but `TODO.md` changed**

Run: `git status --short`
Expected: exactly one modified path, `TODO.md`.

- [ ] **Step 7: Commit**

```bash
git add TODO.md
git commit -m "docs(ledger): log Step 7 findings before implementation

A1.44 LocalShellBackend ships an empty environment
A1.45 artifacts_root defaults into the user's project
A1.46 FilesystemOperation is read/write only, never execute
A1.47 the oversized-tool-result threshold is unreachable

U.17 closed DONE: execute is registered but non-functional on a plain
FilesystemBackend, so CLAUDE.md section 4 was right.

U.7 closed WONTFIX: permissions= raises NotImplementedError on any
execute-capable backend, so it cannot replace C3.3.

Session rule 2 — findings are logged before they are fixed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: The deny floor

**Files:**
- Create: `src/rudra/permissions/__init__.py` (empty placeholder for now — Task 11 fills it)
- Create: `src/rudra/permissions/floor.py`
- Test: `tests/test_permissions_floor.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `FLOOR_RULE_NAMES: tuple[str, ...]` = `("outside-root", "git-dir", "catastrophic-command")`
  - `floor_hit(tool: str, path: Path | None, command: str | None, project_root: Path) -> str | None` — returns the violated rule's name, or `None`. Ignores `floor_disable`; the caller decides what to do with a hit.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_floor.py`:

```python
"""The three named deny-floor rules (Step 7 spec §4.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.permissions.floor import FLOOR_RULE_NAMES, floor_hit


def test_floor_rule_names_are_the_three_documented_ones():
    assert FLOOR_RULE_NAMES == ("outside-root", "git-dir", "catastrophic-command")


@pytest.mark.parametrize("relative", ["src/app.py", "README.md", "a/b/c/d.txt"])
def test_writes_inside_the_project_are_not_a_violation(tmp_path, relative):
    assert floor_hit("write_file", tmp_path / relative, None, tmp_path) is None


@pytest.mark.parametrize(
    "outside",
    ["/etc/hosts", "/tmp/elsewhere/x.py"],
)
def test_writes_outside_the_project_root_violate_outside_root(tmp_path, outside):
    assert floor_hit("write_file", Path(outside), None, tmp_path) == "outside-root"


def test_traversal_out_of_the_project_violates_outside_root(tmp_path):
    escaped = (tmp_path / ".." / "sibling.txt").resolve()
    assert floor_hit("write_file", escaped, None, tmp_path) == "outside-root"


def test_writes_under_git_dir_violate_git_dir(tmp_path):
    assert floor_hit("write_file", tmp_path / ".git" / "HEAD", None, tmp_path) == "git-dir"


def test_nested_git_dir_also_violates(tmp_path):
    target = tmp_path / "vendor" / "dep" / ".git" / "config"
    assert floor_hit("write_file", target, None, tmp_path) == "git-dir"


def test_a_file_merely_named_git_is_not_a_violation(tmp_path):
    assert floor_hit("write_file", tmp_path / "git" / "notes.md", None, tmp_path) is None


def test_delete_is_gated_by_the_same_path_rules(tmp_path):
    assert floor_hit("delete", Path("/etc/hosts"), None, tmp_path) == "outside-root"


def test_read_file_is_never_a_floor_violation(tmp_path):
    """The floor governs destruction, not reading. Reads are ungated (§4.3)."""
    assert floor_hit("read_file", Path("/etc/hosts"), None, tmp_path) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "  rm   -rf   /  ",
        "sudo rm -rf /",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
    ],
)
def test_catastrophic_commands_are_denied(tmp_path, command):
    assert floor_hit("execute", None, command, tmp_path) == "catastrophic-command"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "rm -rf ./node_modules",
        "pytest -q",
        "git status",
        "dd if=in.bin of=out.bin",
    ],
)
def test_ordinary_commands_are_not_denied(tmp_path, command):
    assert floor_hit("execute", None, command, tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_floor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions'`

- [ ] **Step 3: Create the package placeholder**

Create `src/rudra/permissions/__init__.py` with exactly:

```python
"""Rudra's permission layer (Step 7 — C3.1-C3.4).

deepagents' own `permissions=` cannot be used: it raises NotImplementedError
on any backend that supports command execution, and its FilesystemOperation
is ('read', 'write') only, so it never covered `execute` regardless. See
TODO.md U.7 and A1.46.
"""

from __future__ import annotations
```

- [ ] **Step 4: Write the implementation**

Create `src/rudra/permissions/floor.py`:

```python
"""The deny floor: three named rules that apply in every permission mode.

On by default, including under `--yolo`, and disabled per rule via
`[permissions] floor_disable`. This module does not know about that setting
— `floor_hit` reports a violation and `rules.PermissionEngine` decides what
to do with it, so a disabled rule can still be audited as
`source: "floor-disabled"` rather than vanishing.

Not a sandbox, and does not pretend to be one: a model can still write a
destructive script and run it. What the floor buys is that the obvious
catastrophes cannot happen by accident, which is the realistic failure mode
for a 32B model (D6).
"""

from __future__ import annotations

import re
from pathlib import Path

FLOOR_RULE_NAMES = ("outside-root", "git-dir", "catastrophic-command")

# Tools whose effect is destructive. Reads are never a floor violation —
# the floor governs destruction, and gating reads would fire on the dozens
# of read_file calls a normal run makes (spec §4.3).
_DESTRUCTIVE_PATH_TOOLS = frozenset({"write_file", "edit_file", "delete"})

# Anchored at the start of the command, tolerating a leading `sudo` and
# arbitrary whitespace. Deliberately narrow: these are the unrecoverable
# ones, not everything dangerous.
_CATASTROPHIC = (
    re.compile(r"^\s*(?:sudo\s+)?rm\s+(?:-[a-zA-Z]+\s+)*-?[a-zA-Z]*[rR][a-zA-Z]*f?\s+/\s*\*?\s*$"),
    re.compile(r"^\s*(?:sudo\s+)?rm\s+(?:-[a-zA-Z]+\s+)*/\s*\*?\s*$"),
    re.compile(r"^\s*(?:sudo\s+)?mkfs(\.\w+)?\b"),
    re.compile(r"^\s*(?:sudo\s+)?dd\b.*\bof=/dev/"),
)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def floor_hit(
    tool: str,
    path: Path | None,
    command: str | None,
    project_root: Path,
) -> str | None:
    """Name the floor rule this call violates, or None.

    `path` must already be resolved by the caller. Resolution before
    matching is what makes `../../etc/hosts` and a symlink into /etc both
    land on their real location instead of being matched as strings the
    model chose (spec §4.4).
    """
    if tool in _DESTRUCTIVE_PATH_TOOLS and path is not None:
        root = Path(project_root).resolve()
        if not _is_inside(path, root):
            return "outside-root"
        if ".git" in path.parts:
            return "git-dir"

    if tool == "execute" and command is not None:
        if any(pattern.search(command) for pattern in _CATASTROPHIC):
            return "catastrophic-command"

    return None


__all__ = ["FLOOR_RULE_NAMES", "floor_hit"]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_floor.py -q`
Expected: PASS, 20 tests.

If `test_traversal_out_of_the_project_violates_outside_root` fails on macOS, the cause is `/tmp` being a symlink to `/private/tmp`: `_is_inside` compares a resolved path against a resolved root, so both sides must be resolved. Confirm `project_root` is resolved inside `floor_hit` (it is, via `Path(project_root).resolve()`) and that the test resolves its own expectation (it does, via `.resolve()`).

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/ tests/test_permissions_floor.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/rudra/permissions/__init__.py src/rudra/permissions/floor.py tests/test_permissions_floor.py
git commit -m "feat(permissions): the three named deny-floor rules

outside-root, git-dir, catastrophic-command. Pure predicates over an
already-resolved path or a raw command string.

floor_hit reports a violation and does not consult floor_disable, so the
engine can distinguish a denial from a rule the user turned off and still
audit the latter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: The rule engine

**Files:**
- Create: `src/rudra/permissions/rules.py`
- Test: `tests/test_permissions_rules.py`

**Interfaces:**
- Consumes: `floor.FLOOR_RULE_NAMES`, `floor.floor_hit` (Task 2).
- Produces:
  - `CONTROL_PLANE_TOOLS`, `READ_ONLY_TOOLS`, `MUTATING_TOOLS`: `frozenset[str]`
  - `ALL_GATED_TOOLS: frozenset[str]` — everything a rule may legally name
  - `Rule(tool: str, pattern: str | None)`, frozen dataclass
  - `parse_rule(text: str) -> Rule` — raises `ValueError` on a malformed rule
  - `gated_arg(tool: str, args: dict) -> str | None` — the command for `execute`, the path for file tools
  - `Decision(effect: str, rule: str | None, source: str)`, frozen dataclass
  - `PermissionEngine(mode, allow, deny, floor_disable, project_root, grants=None)` with `.decide(tool: str, args: dict) -> Decision`
- `effect` ∈ `{"allow", "deny", "ask"}`. `source` ∈ `{"floor", "floor-disabled", "deny", "session-grant", "allow", "mode-default", "control-plane"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_rules.py`:

```python
"""Precedence and matching for the permission engine (Step 7 spec §4.3, §4.4)."""

from __future__ import annotations

import pytest

from rudra.permissions.rules import (
    ALL_GATED_TOOLS,
    PermissionEngine,
    Rule,
    gated_arg,
    parse_rule,
)


def engine(tmp_path, **kwargs):
    defaults = dict(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=None
    )
    defaults.update(kwargs)
    return PermissionEngine(**defaults)


# ── parse_rule ────────────────────────────────────────────────────────────


def test_a_bare_tool_name_parses_to_a_patternless_rule():
    assert parse_rule("write_file") == Rule(tool="write_file", pattern=None)


def test_tool_colon_pattern_parses_into_both_halves():
    assert parse_rule("execute:pytest*") == Rule(tool="execute", pattern="pytest*")


def test_a_pattern_may_itself_contain_a_colon():
    """`execute:git log --format=%H:%s` must not split on the second colon."""
    assert parse_rule("execute:git log --format=%H:%s") == Rule(
        tool="execute", pattern="git log --format=%H:%s"
    )


def test_an_unknown_tool_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown tool 'writefile'"):
        parse_rule("writefile:x")


def test_an_empty_pattern_is_rejected():
    with pytest.raises(ValueError, match="empty pattern"):
        parse_rule("execute:")


def test_every_gated_tool_name_parses():
    for tool in ALL_GATED_TOOLS:
        assert parse_rule(tool).tool == tool


# ── gated_arg ─────────────────────────────────────────────────────────────


def test_gated_arg_is_the_command_for_execute():
    assert gated_arg("execute", {"command": "pytest -q"}) == "pytest -q"


def test_gated_arg_is_the_path_for_file_tools():
    assert gated_arg("write_file", {"file_path": "src/app.py"}) == "src/app.py"


def test_gated_arg_is_none_when_the_expected_key_is_absent():
    assert gated_arg("write_file", {}) is None


# ── precedence ────────────────────────────────────────────────────────────


def test_reads_are_allowed_silently_by_the_mode_default(tmp_path):
    decision = engine(tmp_path).decide("read_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("allow", "mode-default")


def test_mutations_ask_by_the_mode_default(tmp_path):
    decision = engine(tmp_path).decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("ask", "mode-default")


def test_auto_mode_allows_mutations(tmp_path):
    decision = engine(tmp_path, mode="auto").decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("allow", "mode-default")


def test_plan_mode_denies_mutations(tmp_path):
    decision = engine(tmp_path, mode="plan").decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("deny", "mode-default")


def test_plan_mode_still_allows_reads(tmp_path):
    assert engine(tmp_path, mode="plan").decide("read_file", {"file_path": "a"}).effect == "allow"


def test_control_plane_tools_are_never_gated(tmp_path):
    """update_plan and friends write only under .rudra/run/ (spec §4.6)."""
    for tool in ("update_plan", "write_task_assignment", "ask_user"):
        decision = engine(tmp_path, mode="plan").decide(tool, {})
        assert (decision.effect, decision.source) == ("allow", "control-plane")


def test_an_allow_rule_beats_the_mode_default(tmp_path):
    decision = engine(tmp_path, allow=("execute:pytest*",)).decide(
        "execute", {"command": "pytest -q"}
    )
    assert (decision.effect, decision.source) == ("allow", "allow")


def test_a_deny_rule_beats_an_allow_rule(tmp_path):
    decision = engine(
        tmp_path, allow=("execute:git*",), deny=("execute:git push*",)
    ).decide("execute", {"command": "git push origin main"})
    assert (decision.effect, decision.source) == ("deny", "deny")
    assert decision.rule == "execute:git push*"


def test_the_floor_beats_a_user_allow_rule(tmp_path):
    decision = engine(tmp_path, mode="auto", allow=("execute",)).decide(
        "execute", {"command": "rm -rf /"}
    )
    assert (decision.effect, decision.source) == ("deny", "floor")
    assert decision.rule == "<floor:catastrophic-command>"


def test_the_floor_holds_under_auto_mode(tmp_path):
    decision = engine(tmp_path, mode="auto").decide("write_file", {"file_path": "/etc/hosts"})
    assert (decision.effect, decision.source) == ("deny", "floor")


# ── floor_disable ─────────────────────────────────────────────────────────


def test_a_disabled_floor_rule_stops_denying(tmp_path):
    decision = engine(tmp_path, mode="auto", floor_disable=("outside-root",)).decide(
        "write_file", {"file_path": "/tmp/sibling/out.txt"}
    )
    assert decision.effect == "allow"


def test_a_disabled_floor_rule_is_still_reported_in_the_source(tmp_path):
    """Turning a rule off changes what Rudra blocks, not what it tells you."""
    decision = engine(tmp_path, mode="auto", floor_disable=("outside-root",)).decide(
        "write_file", {"file_path": "/tmp/sibling/out.txt"}
    )
    assert decision.source == "floor-disabled"
    assert decision.rule == "<floor:outside-root>"


def test_disabling_one_floor_rule_leaves_the_others_armed(tmp_path):
    eng = engine(tmp_path, mode="auto", floor_disable=("outside-root",))
    assert eng.decide("execute", {"command": "rm -rf /"}).source == "floor"
    assert eng.decide("write_file", {"file_path": str(tmp_path / ".git" / "HEAD")}).source == "floor"


# ── matching ──────────────────────────────────────────────────────────────


def test_a_relative_pattern_matches_the_project_relative_path(tmp_path):
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": ".env"}
    )
    assert decision.effect == "deny"


def test_a_relative_pattern_does_not_match_the_same_name_elsewhere(tmp_path):
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": "config/.env"}
    )
    assert decision.effect != "deny"


def test_a_globstar_pattern_matches_at_any_depth(tmp_path):
    decision = engine(tmp_path, deny=("write_file:**/secrets/**",)).decide(
        "write_file", {"file_path": "a/b/secrets/key.txt"}
    )
    assert decision.effect == "deny"


def test_an_anchored_pattern_matches_the_absolute_path(tmp_path):
    decision = engine(
        tmp_path, mode="auto", floor_disable=("outside-root",), deny=("write_file:/etc/**",)
    ).decide("write_file", {"file_path": "/etc/hosts"})
    assert decision.effect == "deny"


def test_a_bare_tool_rule_matches_every_call_to_that_tool(tmp_path):
    decision = engine(tmp_path, deny=("execute",)).decide("execute", {"command": "ls"})
    assert decision.effect == "deny"


def test_traversal_is_resolved_before_matching(tmp_path):
    """`src/../.env` is `.env`; matching the model's string would miss it."""
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": "src/../.env"}
    )
    assert decision.effect == "deny"


# ── session grants ────────────────────────────────────────────────────────


def test_a_session_grant_beats_the_mode_default(tmp_path):
    from rudra.permissions.grants import SessionGrants

    grants = SessionGrants()
    grants.add(Rule(tool="execute", pattern="pytest*"))
    decision = engine(tmp_path, grants=grants).decide("execute", {"command": "pytest -q"})
    assert (decision.effect, decision.source) == ("allow", "session-grant")


def test_a_deny_rule_beats_a_session_grant(tmp_path):
    from rudra.permissions.grants import SessionGrants

    grants = SessionGrants()
    grants.add(Rule(tool="execute", pattern="git*"))
    decision = engine(tmp_path, deny=("execute:git push*",), grants=grants).decide(
        "execute", {"command": "git push"}
    )
    assert (decision.effect, decision.source) == ("deny", "deny")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.rules'`

- [ ] **Step 3: Write `grants.py` first — two tests import it**

Create `src/rudra/permissions/grants.py`:

```python
"""Session grants: `always` at an approval prompt, in memory only.

Never written to config. A run can widen what it is allowed to do for its
own lifetime, and can never widen what the user has persisted — which is
also why Step 6 declined to build a TOML writer (S6.1).
"""

from __future__ import annotations

from rudra.permissions.rules import Rule, rule_matches


class SessionGrants:
    """Rules granted by the user during this process."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def add(self, rule: Rule) -> None:
        if rule not in self._rules:
            self._rules.append(rule)

    def matches(self, tool: str, arg: str | None, relative: str | None) -> Rule | None:
        """The first granted rule matching this call, or None."""
        for rule in self._rules:
            if rule_matches(rule, tool, arg, relative):
                return rule
        return None

    def __len__(self) -> int:
        return len(self._rules)


__all__ = ["SessionGrants"]
```

- [ ] **Step 4: Write the engine**

Create `src/rudra/permissions/rules.py`:

```python
"""The permission engine: one pure decision function.

Everything that interprets policy calls `PermissionEngine.decide`. The
middleware acts on a `deny`; `interrupts.build_interrupt_on`'s `when`
predicate acts on an `ask`. Nothing else reads the rule lists.

Purity is the point — no filesystem writes, no console, no graph — so
precedence and matching are testable directly, without a model or a
compiled agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wcmatch import glob as wcglob

from rudra.permissions.floor import floor_hit

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checking only
    from rudra.permissions.grants import SessionGrants

# Rudra's own orchestration tools. They write only under .rudra/run/ and are
# how the loop functions; gating them would make mode="ask" prompt for
# Rudra's own bookkeeping (spec §4.6).
CONTROL_PLANE_TOOLS = frozenset({"update_plan", "write_task_assignment", "ask_user"})

READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})

# `task` is allowed: it spawns a subagent whose own tool calls are gated by
# this same engine, so gating the spawn would double-prompt (spec §4.3).
_OTHER_TOOLS = frozenset({"task"})

ALL_GATED_TOOLS = READ_ONLY_TOOLS | MUTATING_TOOLS | _OTHER_TOOLS | CONTROL_PLANE_TOOLS

# Which argument carries the thing a pattern matches against.
_ARG_KEYS = {
    "execute": "command",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "read_file": "file_path",
    "ls": "path",
    "glob": "pattern",
    "grep": "pattern",
}

_WCMATCH_FLAGS = wcglob.GLOBSTAR | wcglob.DOTGLOB


@dataclass(frozen=True)
class Rule:
    """One parsed `allow`/`deny` entry."""

    tool: str
    pattern: str | None

    def __str__(self) -> str:
        return self.tool if self.pattern is None else f"{self.tool}:{self.pattern}"


@dataclass(frozen=True)
class Decision:
    """What to do about one tool call, and why.

    `rule` is the rule text that fired — a user rule verbatim, or
    `<floor:name>` — so an audit line and an error message can both name it
    without reconstructing anything.
    """

    effect: str  # "allow" | "deny" | "ask"
    rule: str | None
    source: str


def parse_rule(text: str) -> Rule:
    """Parse one `tool` or `tool:pattern` entry.

    Split on the FIRST colon only: `execute:git log --format=%H:%s` is one
    tool and one pattern, not a parse error.
    """
    tool, separator, pattern = text.partition(":")
    tool = tool.strip()
    if tool not in ALL_GATED_TOOLS:
        raise ValueError(
            f"Unknown tool '{tool}' in permission rule {text!r}. "
            f"Valid tools: {', '.join(sorted(ALL_GATED_TOOLS))}."
        )
    if separator and not pattern.strip():
        raise ValueError(f"Permission rule {text!r} has an empty pattern after ':'.")
    return Rule(tool=tool, pattern=pattern if separator else None)


def gated_arg(tool: str, args: dict[str, Any]) -> str | None:
    """The argument a pattern matches against, or None if absent."""
    key = _ARG_KEYS.get(tool)
    if key is None:
        return None
    value = args.get(key)
    return value if isinstance(value, str) else None


def rule_matches(rule: Rule, tool: str, arg: str | None, relative: str | None) -> bool:
    """Does this rule cover this call?

    A patternless rule covers every call to its tool. A pattern starting
    with `/` is matched against the resolved absolute path; anything else
    against the project-relative path. `execute` has no path, so both forms
    match the raw command string.
    """
    if rule.tool != tool:
        return False
    if rule.pattern is None:
        return True
    if arg is None:
        return False
    subject = arg if rule.pattern.startswith("/") or relative is None else relative
    return wcglob.globmatch(subject, rule.pattern, flags=_WCMATCH_FLAGS)


class PermissionEngine:
    """Decides allow / deny / ask for one tool call. Pure."""

    def __init__(
        self,
        mode: str,
        allow: tuple[str, ...],
        deny: tuple[str, ...],
        floor_disable: tuple[str, ...],
        project_root: Path,
        grants: SessionGrants | None = None,
    ) -> None:
        self.mode = mode
        self.allow = tuple(parse_rule(entry) for entry in allow)
        self.deny = tuple(parse_rule(entry) for entry in deny)
        self.floor_disable = frozenset(floor_disable)
        self.project_root = Path(project_root).resolve()
        self.grants = grants

    def _resolve(self, tool: str, arg: str | None) -> tuple[Path | None, str | None]:
        """Absolute path and project-relative path for a path-carrying call.

        Resolution happens before any matching, so `../../etc/hosts` and a
        symlink into /etc both land on their real location. Matching the
        string the model supplied would be trivially bypassable (spec §4.4).
        """
        if tool == "execute" or arg is None:
            return None, None
        candidate = Path(arg)
        absolute = (
            candidate if candidate.is_absolute() else self.project_root / candidate
        ).resolve()
        try:
            relative = str(absolute.relative_to(self.project_root))
        except ValueError:
            relative = None
        return absolute, relative

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        if tool in CONTROL_PLANE_TOOLS:
            return Decision("allow", None, "control-plane")

        arg = gated_arg(tool, args)
        absolute, relative = self._resolve(tool, arg)
        subject = str(absolute) if absolute is not None else arg

        # 1. floor
        suppressed: str | None = None
        hit = floor_hit(tool, absolute, arg if tool == "execute" else None, self.project_root)
        if hit is not None:
            if hit not in self.floor_disable:
                return Decision("deny", f"<floor:{hit}>", "floor")
            suppressed = hit

        def _final(effect: str, rule: str | None, source: str) -> Decision:
            if suppressed is not None and effect == "allow":
                return Decision(effect, f"<floor:{suppressed}>", "floor-disabled")
            return Decision(effect, rule, source)

        # 2. user deny
        for rule in self.deny:
            if rule_matches(rule, tool, subject, relative):
                return Decision("deny", str(rule), "deny")

        # 3. session grants
        if self.grants is not None:
            granted = self.grants.matches(tool, subject, relative)
            if granted is not None:
                return _final("allow", str(granted), "session-grant")

        # 4. user allow
        for rule in self.allow:
            if rule_matches(rule, tool, subject, relative):
                return _final("allow", str(rule), "allow")

        # 5. mode default
        if tool in READ_ONLY_TOOLS or tool in _OTHER_TOOLS:
            return _final("allow", None, "mode-default")
        if self.mode == "auto":
            return _final("allow", None, "mode-default")
        if self.mode == "plan":
            return Decision("deny", None, "mode-default")
        return Decision("ask", None, "mode-default")


__all__ = [
    "ALL_GATED_TOOLS",
    "CONTROL_PLANE_TOOLS",
    "MUTATING_TOOLS",
    "READ_ONLY_TOOLS",
    "Decision",
    "PermissionEngine",
    "Rule",
    "gated_arg",
    "parse_rule",
    "rule_matches",
]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_rules.py tests/test_permissions_floor.py -q`
Expected: PASS.

If `test_a_relative_pattern_matches_the_project_relative_path` fails, check that `_resolve` returns `relative` for a path inside the root and that `rule_matches` prefers `relative` for an unanchored pattern.

- [ ] **Step 6: Verify no import cycle**

Run: `.venv/bin/python -c "from rudra.permissions.rules import PermissionEngine; from rudra.permissions.grants import SessionGrants; print('ok')"`
Expected: `ok`

`grants.py` imports `rules.py` at runtime; `rules.py` imports `grants.py` only under `TYPE_CHECKING`. Reversing either direction deadlocks — the same class of failure Step 6 hit when `schema.py` imported `llm.providers`.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/ tests/
git add src/rudra/permissions/rules.py src/rudra/permissions/grants.py tests/test_permissions_rules.py
git commit -m "feat(permissions): rule engine with fixed precedence

decide() is pure: floor, then user deny, then session grants, then user
allow, then the mode default. Deny beats allow because allow and deny are
two separate lists and declaration order cannot arbitrate between them.

Paths resolve before matching, so traversal and symlinks are matched at
their real location rather than as the string the model chose.

A disabled floor rule yields source 'floor-disabled' rather than
disappearing, so the audit log still records what the floor would have
caught.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Config surface — `floor_disable`, `[tools]`, un-reserving

**Files:**
- Modify: `src/rudra/config/schema.py`
- Modify: `src/rudra/config/loader.py:41-44` (`_TOP_LEVEL`, `_PERMISSION_KEYS`), `:139-155` (permissions validation), `:246-253` (construction)
- Modify: `src/rudra/config/layers.py:160-172` (`env_layer` special cases)
- Modify: `src/rudra/config/template.py`
- Test: `tests/test_config_permissions_and_tools.py`

**Interfaces:**
- Consumes: `floor.FLOOR_RULE_NAMES` (Task 2), `rules.ALL_GATED_TOOLS` (Task 3) — both only in tests, never imported by `schema.py`.
- Produces:
  - `PermissionsConfig.floor_disable: tuple[str, ...]`
  - `ToolsConfig(shell: bool)`
  - `Config.tools: ToolsConfig`
  - `FLOOR_RULE_NAMES` declared literally in `schema.py`

**`[tools]` carries `shell` and nothing else.** The oversized-output threshold spec §5.2 first planned is unreachable (`A1.47`), and a key that silently does nothing is worse than no key — the `A1.40` defect class, and the one Step 6 worked to avoid when it refused to let `[permissions]` read as enforced.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_permissions_and_tools.py`:

```python
"""Step 7's config surface: floor_disable and the un-reserved [tools]."""

from __future__ import annotations

import pytest

from rudra.config.loader import ConfigError, build_config, reset_config
from rudra.config.schema import FLOOR_RULE_NAMES


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body: str):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_floor_disable_defaults_to_empty(tmp_path):
    assert build_config(tmp_path).permissions.floor_disable == ()


def test_floor_disable_is_read_from_toml(tmp_path):
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside-root"]\n')
    assert build_config(root).permissions.floor_disable == ("outside-root",)


def test_an_unknown_floor_rule_name_is_a_config_error(tmp_path):
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside_root"]\n')
    with pytest.raises(ConfigError, match="Did you mean 'outside-root'"):
        build_config(root)


def test_floor_disable_must_be_a_list_of_strings(tmp_path):
    root = write_config(tmp_path, "[permissions]\nfloor_disable = true\n")
    with pytest.raises(ConfigError, match="must be a list of strings"):
        build_config(root)


def test_the_schema_names_the_same_floor_rules_the_floor_module_does():
    """schema.py must not import rudra.permissions — this test keeps them honest.

    Same pattern as VALID_PROVIDERS, which is declared literally for the
    same reason and guarded by an equivalent test.
    """
    from rudra.permissions.floor import FLOOR_RULE_NAMES as REAL

    assert FLOOR_RULE_NAMES == REAL


def test_tools_section_is_no_longer_reserved(tmp_path):
    root = write_config(tmp_path, "[tools]\nshell = false\n")
    assert build_config(root).tools.shell is False


def test_tools_defaults_to_shell_enabled(tmp_path):
    assert build_config(tmp_path).tools.shell is True


def test_an_unknown_tools_key_is_a_config_error(tmp_path):
    root = write_config(tmp_path, "[tools]\nshel = false\n")
    with pytest.raises(ConfigError, match="Did you mean 'shell'"):
        build_config(root)


def test_no_inert_output_limit_key_ships(tmp_path):
    """A1.47: the threshold is unreachable, so it must not read as settable."""
    root = write_config(tmp_path, "[tools]\ntool_output_limit_tokens = 4000\n")
    with pytest.raises(ConfigError):
        build_config(root)


def test_shell_must_be_a_bool(tmp_path):
    root = write_config(tmp_path, '[tools]\nshell = "yes"\n')
    with pytest.raises(ConfigError, match="must be true or false"):
        build_config(root)


def test_skills_and_memory_stay_reserved(tmp_path):
    for section, step in (("skills", "Step 11"), ("memory", "Step 14")):
        root = write_config(tmp_path, f"[{section}]\nx = 1\n")
        with pytest.raises(ConfigError, match=step):
            build_config(root)
        reset_config()


def test_env_layer_sets_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("RUDRA_SHELL", "false")
    assert build_config(tmp_path).tools.shell is False


def test_the_init_template_round_trips_through_the_loader(tmp_path):
    """rudra init must scaffold a file the loader accepts (Step 6's rule)."""
    from rudra.config.template import CONFIG_TEMPLATE

    root = write_config(tmp_path, CONFIG_TEMPLATE)
    cfg = build_config(root)
    assert cfg.permissions.floor_disable == ()
    assert cfg.tools.shell is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config_permissions_and_tools.py -q`
Expected: FAIL — `ImportError: cannot import name 'FLOOR_RULE_NAMES' from 'rudra.config.schema'`

- [ ] **Step 3: Edit `schema.py`**

Delete the `"tools"` entry from `RESERVED_SECTIONS`, leaving:

```python
RESERVED_SECTIONS = {
    "skills": "not supported yet — arrives in Step 11 (C5.1)",
    "memory": "not supported yet — arrives in Step 14 (C8.1)",
    "mcp": "MCP is configured in a separate .mcp.json, not here — arrives in Step 13 (C4.2)",
}
```

Add below `VALID_MODES`:

```python
# Declared literally rather than imported from `rudra.permissions.floor`,
# for the same reason VALID_PROVIDERS is: this module imports nothing from
# the rest of Rudra, so it stays readable and testable with no agent
# machinery. Kept in agreement by
# tests/test_config_permissions_and_tools.py::test_the_schema_names_the_same_floor_rules_the_floor_module_does.
FLOOR_RULE_NAMES = ("outside-root", "git-dir", "catastrophic-command")
```

Replace `PermissionsConfig` entirely:

```python
@dataclass(frozen=True)
class PermissionsConfig:
    """Enforced from Step 7 (C3.3). See the Step 7 design spec §4.

    `allow`/`deny` are `tool` or `tool:pattern` strings using real tool
    names. `floor_disable` names built-in floor rules to switch off; it is
    per-rule rather than a boolean so one legitimate exception costs one
    name instead of surrendering the whole floor (spec §4.5).
    """

    mode: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]
    floor_disable: tuple[str, ...]
```

Add after `CompatConfig`:

```python
@dataclass(frozen=True)
class ToolsConfig:
    """The tool layer. Un-reserved in Step 7, which implements it.

    One key, deliberately. The oversized-tool-result threshold that would
    naturally live here is unreachable through `create_deep_agent` (TODO.md
    A1.47), and a key that silently does nothing is worse than no key.
    """

    shell: bool
```

Update `DEFAULTS`:

```python
    "permissions": {"mode": "ask", "allow": [], "deny": [], "floor_disable": []},
    "compat": {"task_anchor": False, "sandbox_paths": False},
    "tools": {"shell": True},
```

Add `"FLOOR_RULE_NAMES"` and `"ToolsConfig"` to `__all__`, keeping it sorted.

- [ ] **Step 4: Edit `loader.py`**

Line 41–44 becomes:

```python
_TOP_LEVEL = ("model", "agent", "permissions", "compat", "tools")
_AGENT_KEYS = frozenset({"verbose"})
_PERMISSION_KEYS = frozenset({"mode", "allow", "deny", "floor_disable"})
_COMPAT_KEYS = frozenset({"task_anchor", "sandbox_paths"})
_TOOLS_KEYS = frozenset({"shell"})
_POSITIVE_INT_KEYS = ("context_tokens", "max_output_tokens", "timeout")
```

Add `FLOOR_RULE_NAMES` and `ToolsConfig` to the `rudra.config.schema` import block.

In `validate`, replace the `for key in ("allow", "deny"):` loop with:

```python
    for key in ("allow", "deny", "floor_disable"):
        value = permissions.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"[permissions] {key} must be a list of strings, got {value!r}.")
    for name in permissions.get("floor_disable", []):
        if name not in FLOOR_RULE_NAMES:
            raise ConfigError(
                f"Unknown floor rule '{name}' in [permissions] floor_disable "
                f"({_where(provenance, sources, 'permissions.floor_disable')})."
                f"{_suggest(name, FLOOR_RULE_NAMES)}"
            )
```

A typo must be fatal here specifically: silently ignoring one leaves a rule armed that the user believes they turned off, and they find out by being blocked mid-run.

After the `compat` loop, add:

```python
    for key, value in merged.get("tools", {}).items():
        if key not in _TOOLS_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [tools].{_suggest(key, _TOOLS_KEYS)}")
        if key == "shell" and not isinstance(value, bool):
            raise ConfigError(f"[tools] shell must be true or false, got {value!r}.")
```

Add `tools: ToolsConfig` to the `Config` dataclass, immediately after `compat`. Then in `build_config`'s return, replace the `permissions=` argument and add `tools=`:

```python
        permissions=PermissionsConfig(
            mode=permissions.get("mode", "ask"),
            allow=tuple(permissions.get("allow", [])),
            deny=tuple(permissions.get("deny", [])),
            floor_disable=tuple(permissions.get("floor_disable", [])),
        ),
        compat=CompatConfig(**merged.get("compat", {})),
        tools=ToolsConfig(**merged.get("tools", {})),
```

- [ ] **Step 5: Edit `layers.py`**

In `env_layer`, immediately after the `PERMISSIONS_MODE` branch (`layers.py:167-169`), add:

```python
        if tail == "SHELL":
            put("tools", "shell", value=_as_bool(raw))
            continue
```

Order matters: these must sit **before** `_split_role_and_suffix`, which would otherwise try to read `SHELL` as a role-and-suffix pair.

- [ ] **Step 6: Edit `template.py`**

Replace the `[permissions]` block and the trailing "not supported yet" comment:

```toml
[permissions]
# ask | auto | plan
#
#   ask   prompt before each write, edit, delete, or command
#   auto  approve everything (same as --auto / --yolo)
#   plan  write no project files and run no commands
mode  = "ask"

# Rules are "tool" or "tool:pattern", using real tool names:
#   read_file  ls  glob  grep  write_file  edit_file  delete  execute  task
# A pattern starting with / matches the absolute path; otherwise it matches
# the project-relative path. For execute, it matches the command string.
# deny beats allow.
allow = []
deny  = []

# Built-in rules denied in every mode, including --auto. Name one here to
# switch it off; the run prints which are disabled, and calls they would
# have blocked are still written to the audit log.
#   outside-root          write or delete outside this project
#   git-dir               write or delete under .git/
#   catastrophic-command  rm -rf /, mkfs, dd of=/dev/*
floor_disable = []

[tools]
# false removes the execute tool: no tests, no linters, no git.
shell = true

[compat]
# Workarounds kept for small models. Both off by default — see TODO.md D4.
task_anchor   = false
sandbox_paths = false

# Not supported yet, listed so you know where they will go:
#   [skills]  Step 11    [memory]  Step 14
# MCP servers are configured in a separate .mcp.json (Step 13).
"""
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_config_permissions_and_tools.py -q`
Expected: PASS, 14 tests.

- [ ] **Step 8: Run the full suite — this task edits shared config code**

Run: `.venv/bin/pytest -q`
Expected: at least `286 passed`. Any Step 6 config test that fails here is asserting on the old shape; fix the test to assert the property, not the syntax — the same correction Step 6 made to `test_agent_wiring` and `test_cli_smoke`.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/ tests/
git add src/rudra/config/ tests/test_config_permissions_and_tools.py
git commit -m "feat(config): floor_disable, and [tools] un-reserved

CLAUDE.md section 6 reserved [tools] naming Step 7 as its implementing
step. This is that step, so the reserved error is removed and the section
becomes real, carrying shell.

It carries only shell: the oversized-output threshold that would belong
beside it is unreachable through create_deep_agent (A1.47), and an inert
config key is worse than no key.

permissions.floor_disable names built-in floor rules to switch off. An
unknown name is fatal with a difflib suggestion — silently ignoring one
would leave a rule armed that the user believes they disabled.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Audit log

**Files:**
- Create: `src/rudra/permissions/audit.py`
- Test: `tests/test_permissions_audit.py`

**Interfaces:**
- Consumes: `rules.Decision` (Task 3).
- Produces: `AuditLog(path: Path)` with `.record(tool: str, arg: str | None, decision: Decision, mode: str, outcome: str) -> None`, and `SILENT_SOURCES: frozenset[str]`.
  - `outcome` ∈ `{"allow", "deny", "approve", "reject"}` — what actually happened, distinct from `Decision.effect`, which is what the engine asked for.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_audit.py`:

```python
"""The permission audit log (Step 7 spec §6.7)."""

from __future__ import annotations

import json

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import Decision


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_denial_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "write_file",
        "/etc/hosts",
        Decision("deny", "<floor:outside-root>", "floor"),
        mode="auto",
        outcome="deny",
    )
    (entry,) = read_lines(log_path)
    assert entry["tool"] == "write_file"
    assert entry["arg"] == "/etc/hosts"
    assert entry["rule"] == "<floor:outside-root>"
    assert entry["mode"] == "auto"
    assert entry["decision"] == "deny"
    assert entry["source"] == "floor"
    assert entry["ts"].endswith("Z") or "+00:00" in entry["ts"]


def test_an_approval_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute", "pytest -q", Decision("ask", None, "mode-default"), mode="ask", outcome="approve"
    )
    (entry,) = read_lines(log_path)
    assert entry["decision"] == "approve"
    assert entry["source"] == "prompt"


def test_a_rejection_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute", "rm -rf build", Decision("ask", None, "mode-default"), mode="ask", outcome="reject"
    )
    assert read_lines(log_path)[0]["decision"] == "reject"


def test_a_session_grant_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute",
        "pytest -q tests/x",
        Decision("allow", "execute:pytest*", "session-grant"),
        mode="ask",
        outcome="allow",
    )
    entry = read_lines(log_path)[0]
    assert entry["source"] == "session-grant"
    assert entry["rule"] == "execute:pytest*"


def test_a_disabled_floor_rule_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "write_file",
        "/tmp/out.txt",
        Decision("allow", "<floor:outside-root>", "floor-disabled"),
        mode="auto",
        outcome="allow",
    )
    assert read_lines(log_path)[0]["source"] == "floor-disabled"


def test_silent_allows_are_not_recorded(tmp_path):
    """Hundreds of read_file lines would bury the signal (spec §6.7)."""
    log_path = tmp_path / "permissions.jsonl"
    log = AuditLog(log_path)
    for source in ("mode-default", "allow", "control-plane"):
        log.record("read_file", "src/app.py", Decision("allow", None, source), mode="ask", outcome="allow")
    assert read_lines(log_path) == []


def test_entries_append_rather_than_overwrite(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    log = AuditLog(log_path)
    for command in ("a", "b", "c"):
        log.record("execute", command, Decision("deny", "execute", "deny"), mode="ask", outcome="deny")
    assert [entry["arg"] for entry in read_lines(log_path)] == ["a", "b", "c"]


def test_the_parent_directory_is_created_on_demand(tmp_path):
    log_path = tmp_path / "run" / "logs" / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny"
    )
    assert log_path.exists()


def test_an_unwritable_log_warns_and_does_not_raise(tmp_path, capsys):
    """An unwritable log must never abort a run, nor be swallowed silently."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    log = AuditLog(blocked / "permissions.jsonl")
    log.record("execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny")
    assert "audit" in capsys.readouterr().err.lower()


def test_one_write_failure_does_not_silence_later_ones(tmp_path, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    log = AuditLog(blocked / "permissions.jsonl")
    for _ in range(2):
        log.record("execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny")
    assert capsys.readouterr().err.lower().count("audit") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.audit'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/permissions/audit.py`:

```python
"""Append-only JSONL record of every gated permission decision.

Written in every mode, including `auto`: an unattended run that keeps no
record of what it was allowed to do is exactly the run whose record matters
most.

Silent default-allow reads are deliberately NOT recorded. A run makes
hundreds, and including them stops the log being something a human skims
after a surprising run (spec §6.7).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rudra.permissions.rules import Decision

# An allow from one of these is routine and unremarkable.
SILENT_SOURCES = frozenset({"mode-default", "allow", "control-plane"})


class AuditLog:
    """One JSONL file per project run tree."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._warned = False

    def _should_record(self, decision: Decision, outcome: str) -> bool:
        return not (outcome == "allow" and decision.source in SILENT_SOURCES)

    def record(
        self,
        tool: str,
        arg: str | None,
        decision: Decision,
        *,
        mode: str,
        outcome: str,
    ) -> None:
        """Append one decision.

        `outcome` is what happened; `decision.effect` is what the engine
        asked for. They differ precisely where a human intervened, which is
        the interesting case: effect "ask" with outcome "reject".
        """
        if not self._should_record(decision, outcome):
            return

        source = "prompt" if decision.source == "mode-default" and outcome in {
            "approve",
            "reject",
        } else decision.source

        entry = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": tool,
            "arg": arg,
            "rule": decision.rule,
            "mode": mode,
            "decision": outcome,
            "source": source,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError as exc:
            # Never abort a run over a log, and never swallow the failure.
            # Warned once so a broken path does not print on every call.
            if not self._warned:
                self._warned = True
                print(f"warning: could not write the permission audit log: {exc}", file=sys.stderr)


__all__ = ["SILENT_SOURCES", "AuditLog"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_audit.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/audit.py tests/test_permissions_audit.py
git add src/rudra/permissions/audit.py tests/test_permissions_audit.py
git commit -m "feat(permissions): JSONL audit log for gated decisions

Records deny, approve, reject, session grants, floor denials, and calls a
disabled floor rule would have blocked. Silent default-allow reads are
excluded so the log stays skimmable.

An unwritable log warns once on stderr and never aborts the run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: The deny middleware

**Files:**
- Create: `src/rudra/permissions/middleware.py`
- Test: `tests/test_permissions_middleware.py`

**Interfaces:**
- Consumes: `rules.PermissionEngine`, `rules.gated_arg` (Task 3); `audit.AuditLog` (Task 5).
- Produces: `RudraPermissionMiddleware(engine, audit, mode)`, an `AgentMiddleware` subclass implementing `wrap_tool_call(request, handler)` and `awrap_tool_call(request, handler)`.

The middleware handles `deny` only. `ask` is `interrupt_on`'s job (Task 10) and `allow` falls through to `handler(request)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_middleware.py`:

```python
"""The deny short-circuit (Step 7 spec §4.1).

Driven through a real compiled graph so the assertion is that the file was
not written, not merely that a function returned something.
"""

from __future__ import annotations

import tempfile

import pytest
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from rudra.permissions.audit import AuditLog
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import PermissionEngine


class OneCallModel(BaseChatModel):
    """Emits one scripted tool call, then stops. No network, no provider."""

    tool_name: str = "write_file"
    tool_args: dict = {}
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "one-call"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": self.tool_name, "args": self.tool_args, "id": "call-1"}],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def build(tmp_path, *, mode="auto", allow=(), deny=(), tool, args):
    engine = PermissionEngine(
        mode=mode, allow=allow, deny=deny, floor_disable=(), project_root=tmp_path
    )
    audit = AuditLog(tmp_path / "audit.jsonl")
    agent = create_deep_agent(
        model=OneCallModel(tool_name=tool, tool_args=args),
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        middleware=[RudraPermissionMiddleware(engine, audit, mode)],
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    return tool_messages


def test_a_denied_write_never_reaches_the_disk(tmp_path):
    (message,) = build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert message.status == "error"
    assert not (tmp_path / "secret.txt").exists()


def test_the_denial_message_names_the_rule_that_fired(tmp_path):
    (message,) = build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert "write_file:secret.txt" in message.content


def test_an_allowed_write_reaches_the_disk(tmp_path):
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "ok.txt", "content": "hello"}
    )
    assert message.status != "error"
    assert (tmp_path / "ok.txt").read_text() == "hello"


def test_the_floor_denies_even_in_auto_mode(tmp_path):
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "/etc/hosts", "content": "x"}
    )
    assert message.status == "error"
    assert "outside-root" in message.content


def test_an_ask_decision_is_not_handled_here(tmp_path):
    """ask is interrupt_on's job. Without it wired, the call proceeds."""
    (message,) = build(
        tmp_path, mode="ask", tool="write_file", args={"file_path": "ok.txt", "content": "y"}
    )
    assert message.status != "error"


def test_a_denial_is_written_to_the_audit_log(tmp_path):
    build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert (tmp_path / "audit.jsonl").exists()
    assert "secret.txt" in (tmp_path / "audit.jsonl").read_text()


def test_control_plane_tools_pass_through_untouched(tmp_path):
    """update_plan is not a backend tool; it must not be gated (spec §4.6)."""
    engine = PermissionEngine(
        mode="plan", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    middleware = RudraPermissionMiddleware(engine, AuditLog(tmp_path / "a.jsonl"), "plan")
    calls = []

    class Request:
        tool_call = {"name": "update_plan", "args": {"plan_markdown": "- [ ] a.py"}, "id": "c"}

    middleware.wrap_tool_call(Request(), lambda request: calls.append(request) or "handled")
    assert calls, "control-plane tool was gated when it must pass through"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_middleware.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.middleware'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/permissions/middleware.py`:

```python
"""The deny half of the gate.

Only `deny` is handled here. `ask` belongs to `interrupt_on`, whose
pause/resume plumbing deepagents already owns, and routing a denial through
an interrupt would pause a graph purely to un-pause it. `allow` falls
through to the handler untouched.

A denial returns an error ToolMessage naming the rule, so the model can
adapt rather than retry blindly against something that will never succeed.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine, gated_arg


def _denial_text(tool: str, arg: str | None, rule: str | None) -> str:
    target = f" on {arg!r}" if arg else ""
    if rule and rule.startswith("<floor:"):
        name = rule[len("<floor:") : -1]
        return (
            f"Permission denied: {tool}{target} is blocked by Rudra's built-in "
            f"'{name}' rule, which applies in every permission mode. "
            f"Do not retry this call."
        )
    if rule:
        return (
            f"Permission denied: {tool}{target} matches the deny rule {rule!r} "
            f"in this project's configuration. Do not retry this call."
        )
    return (
        f"Permission denied: {tool}{target} is not permitted in "
        f"'plan' mode, which makes no project changes. Do not retry this call."
    )


class RudraPermissionMiddleware(AgentMiddleware):
    """Short-circuits denied tool calls before they reach the backend."""

    def __init__(self, engine: PermissionEngine, audit: AuditLog, mode: str) -> None:
        super().__init__()
        self.engine = engine
        self.audit = audit
        self.mode = mode

    def _check(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        tool = call["name"]
        args = call.get("args") or {}
        decision = self.engine.decide(tool, args)
        if decision.effect != "deny":
            return None
        arg = gated_arg(tool, args)
        self.audit.record(tool, arg, decision, mode=self.mode, outcome="deny")
        return ToolMessage(
            content=_denial_text(tool, arg, decision.rule),
            tool_call_id=call.get("id", ""),
            name=tool,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        denial = self._check(request)
        return denial if denial is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        denial = self._check(request)
        return denial if denial is not None else await handler(request)


__all__ = ["RudraPermissionMiddleware"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_middleware.py -q`
Expected: PASS, 7 tests.

If `test_an_allowed_write_reaches_the_disk` fails with a `ValidationError` about `runtime`, the handler is being called with the wrong object — pass `request` straight through, never a reconstructed one. This is U.19's lesson: deepagents' filesystem tools carry an injected `ToolRuntime` that only a real graph supplies.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/middleware.py tests/test_permissions_middleware.py
git add src/rudra/permissions/middleware.py tests/test_permissions_middleware.py
git commit -m "feat(permissions): deny short-circuit middleware

wrap_tool_call returns an error ToolMessage naming the rule that fired,
without invoking the handler, so a denied write never reaches the backend.

ask is deliberately not handled here — that is interrupt_on's job, and
routing a denial through an interrupt would pause the graph only to
un-pause it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: The shell environment

**Files:**
- Create: `src/rudra/permissions/env.py`
- Test: `tests/test_permissions_env.py`

**Interfaces:**
- Consumes: `Config` (Task 4) — reads `cfg.models` for every role's `api_key_env`.
- Produces: `scrubbed_env(cfg) -> dict[str, str]`, `SECRET_NAME_RE: re.Pattern[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_env.py`:

```python
"""The shell environment handed to LocalShellBackend (Step 7 spec §5.3).

Closes A1.44: the backend's own default is an EMPTY environment, so every
venv, nvm, rustup, and pyenv toolchain is invisible.
"""

from __future__ import annotations

import pytest

from rudra.config.loader import build_config, reset_config
from rudra.permissions.env import scrubbed_env


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def test_path_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    assert scrubbed_env(build_config(tmp_path))["PATH"] == "/usr/local/bin:/usr/bin"


@pytest.mark.parametrize(
    "name",
    ["VIRTUAL_ENV", "NVM_DIR", "CARGO_HOME", "HOME", "LANG", "PYENV_ROOT"],
)
def test_toolchain_variables_survive(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "/somewhere")
    assert name in scrubbed_env(build_config(tmp_path))


@pytest.mark.parametrize(
    "name",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "MY_SECRET",
        "DB_PASSWORD",
        "SERVICE_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
    ],
)
def test_secret_shaped_names_are_removed(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "s3cret")
    assert name not in scrubbed_env(build_config(tmp_path))


def test_the_configured_api_key_env_is_removed_by_name(tmp_path, monkeypatch):
    """A key var named something innocuous must still be dropped."""
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "openai_compatible"\napi_key_env = "MY_UNUSUAL_NAME"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_UNUSUAL_NAME", "s3cret")
    assert "MY_UNUSUAL_NAME" not in scrubbed_env(build_config(tmp_path))


def test_no_secret_value_appears_anywhere_in_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unique-canary-value")
    assert "sk-unique-canary-value" not in repr(scrubbed_env(build_config(tmp_path)))


def test_an_ordinary_variable_mentioning_a_key_is_kept(tmp_path, monkeypatch):
    """KEYBOARD_LAYOUT is not a secret. The regex is anchored for this reason."""
    monkeypatch.setenv("KEYBOARD_LAYOUT", "us")
    assert "KEYBOARD_LAYOUT" in scrubbed_env(build_config(tmp_path))


def test_the_result_is_a_copy_not_the_live_environment(tmp_path):
    env = scrubbed_env(build_config(tmp_path))
    env["ADDED_BY_TEST"] = "x"
    assert "ADDED_BY_TEST" not in __import__("os").environ
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.env'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/permissions/env.py`:

```python
"""The environment agent shell commands run with.

LocalShellBackend defaults to `inherit_env=False`, which means an EMPTY
environment — not a minimal one. `which pytest git ruff` finds nothing, so
every venv, nvm, rustup, and pyenv toolchain is invisible and Step 8's
git and test-runner tools would fail on their first call (TODO.md A1.44).

So Rudra passes an explicit environment: inherited, minus secrets. C1.5
keeps API keys out of TOML specifically so they live in the environment;
handing that environment to a shell the model drives would undo it, since
anything the agent prints lands in the transcript and goes to the provider.

This is a real-toolchains-work default, not a sandbox. A command that reads
~/.aws/credentials off disk is unaffected — that is the filesystem gate's
job, not the environment's.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from rudra.config.loader import Config

# Anchored at the end so KEYBOARD_LAYOUT and TOKENIZERS_PARALLELISM survive;
# AWS_ is a prefix because its credential vars do not share a suffix.
SECRET_NAME_RE = re.compile(r"(_KEY|_KEY_ID|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$|^AWS_")


def scrubbed_env(cfg: Config) -> dict[str, str]:
    """`os.environ` minus every secret-shaped or configured key variable."""
    configured = {
        model.api_key_env for model in cfg.models.values() if model.api_key_env
    }
    return {
        name: value
        for name, value in os.environ.items()
        if name not in configured and not SECRET_NAME_RE.search(name)
    }


__all__ = ["SECRET_NAME_RE", "scrubbed_env"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_env.py -q`
Expected: PASS, 18 tests.

`AWS_ACCESS_KEY_ID` is why the regex carries `_KEY_ID` as well as `_KEY`; it is also matched by the `^AWS_` prefix, and both are deliberate — a non-AWS `FOO_KEY_ID` should still be dropped.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/env.py tests/test_permissions_env.py
git add src/rudra/permissions/env.py tests/test_permissions_env.py
git commit -m "feat(permissions): inherited shell environment minus secrets

LocalShellBackend's default is an empty environment, so no venv, nvm,
rustup, or pyenv toolchain is reachable (A1.44). Rudra passes os.environ
minus every role's configured api_key_env and anything matching
(_KEY|_KEY_ID|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)\$ or ^AWS_.

C1.5 keeps API keys out of TOML so they live in the environment. Handing
that environment to a model-driven shell would undo it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Diff rendering

**Files:**
- Create: `src/rudra/permissions/diff.py`
- Test: `tests/test_permissions_diff.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `render(tool: str, args: dict, project_root: Path, *, full: bool = False, max_lines: int = 20) -> DiffPreview` where `DiffPreview` is a frozen dataclass with `header: str`, `body: str`, `truncated: bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_diff.py`:

```python
"""What the user sees before approving a write (Step 7 spec §6.4).

This is what closes A1.16 — files silently overwritten, no diff, no
confirm. A prompt that shows nothing does not close it.
"""

from __future__ import annotations

from rudra.permissions.diff import render


def test_a_new_file_reports_size_rather_than_a_diff(tmp_path):
    preview = render(
        "write_file", {"file_path": "new.py", "content": "a\nb\nc\n"}, tmp_path
    )
    assert "new file" in preview.header
    assert "3 lines" in preview.header


def test_a_new_file_shows_its_first_lines(tmp_path):
    preview = render(
        "write_file", {"file_path": "new.py", "content": "import os\nprint(1)\n"}, tmp_path
    )
    assert "import os" in preview.body


def test_a_new_file_shows_at_most_ten_lines(tmp_path):
    content = "".join(f"line{n}\n" for n in range(50))
    preview = render("write_file", {"file_path": "new.py", "content": content}, tmp_path)
    assert preview.body.count("\n") <= 11
    assert preview.truncated


def test_an_overwrite_produces_a_unified_diff(tmp_path):
    (tmp_path / "app.py").write_text("old line\n", encoding="utf-8")
    preview = render(
        "write_file", {"file_path": "app.py", "content": "new line\n"}, tmp_path
    )
    assert "-old line" in preview.body
    assert "+new line" in preview.body


def test_an_overwrite_header_carries_the_change_counts(tmp_path):
    (tmp_path / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    preview = render(
        "write_file", {"file_path": "app.py", "content": "a\nB\nc\nd\n"}, tmp_path
    )
    assert "+2" in preview.header and "-1" in preview.header
    assert "overwrite" in preview.header


def test_an_unchanged_overwrite_says_so(tmp_path):
    (tmp_path / "app.py").write_text("same\n", encoding="utf-8")
    preview = render("write_file", {"file_path": "app.py", "content": "same\n"}, tmp_path)
    assert "no change" in preview.header


def test_a_long_diff_is_capped(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"old{n}\n" for n in range(100)), encoding="utf-8")
    preview = render(
        "write_file",
        {"file_path": "app.py", "content": "".join(f"new{n}\n" for n in range(100))},
        tmp_path,
    )
    assert preview.truncated
    assert "more changed lines" in preview.body


def test_full_defeats_the_cap(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"old{n}\n" for n in range(100)), encoding="utf-8")
    args = {"file_path": "app.py", "content": "".join(f"new{n}\n" for n in range(100))}
    capped = render("write_file", args, tmp_path)
    full = render("write_file", args, tmp_path, full=True)
    assert len(full.body) > len(capped.body)
    assert not full.truncated


def test_edit_file_diffs_the_two_strings(tmp_path):
    preview = render(
        "edit_file",
        {"file_path": "app.py", "old_string": "def run():", "new_string": "def run(argv):"},
        tmp_path,
    )
    assert "-def run():" in preview.body
    assert "+def run(argv):" in preview.body


def test_delete_reports_the_current_size(tmp_path):
    (tmp_path / "gone.py").write_text("a\nb\n", encoding="utf-8")
    preview = render("delete", {"file_path": "gone.py"}, tmp_path)
    assert "delete" in preview.header
    assert "2 lines" in preview.header


def test_execute_shows_the_command_and_cwd(tmp_path):
    preview = render("execute", {"command": "pytest -q"}, tmp_path)
    assert "pytest -q" in preview.body
    assert str(tmp_path) in preview.body


def test_binary_content_reports_size_and_does_not_render(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    preview = render("write_file", {"file_path": "blob.bin", "content": "\x00\x01"}, tmp_path)
    assert "binary" in preview.header.lower()
    assert preview.body == ""


def test_oversized_content_reports_size_and_does_not_render(tmp_path):
    huge = "x" * (2 * 1024 * 1024)
    preview = render("write_file", {"file_path": "big.txt", "content": huge}, tmp_path)
    assert "too large" in preview.header.lower()
    assert preview.body == ""


def test_an_unreadable_existing_file_degrades_to_a_size_header(tmp_path):
    """A diff we cannot compute must not abort the approval prompt."""
    target = tmp_path / "sub"
    target.mkdir()
    preview = render("write_file", {"file_path": "sub", "content": "x"}, tmp_path)
    assert preview.body == ""
    assert preview.header
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_diff.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.diff'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/permissions/diff.py`:

```python
"""Rendering what a write is about to do, before it does it.

A1.16 is "files silently overwritten — no diff, no backup, no confirm". A
prompt that asks for approval without showing the change does not close
that; it just moves the silence one step later.

Capped by default because Rudra rewrites whole files: an uncapped 400-line
rewrite scrolls the decision off screen and trains users to approve blind.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RENDER_BYTES = 1_000_000
NEW_FILE_PREVIEW_LINES = 10


@dataclass(frozen=True)
class DiffPreview:
    """One rendered approval body."""

    header: str
    body: str
    truncated: bool


def _resolve(project_root: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else Path(project_root) / candidate


def _read(path: Path) -> str | None:
    """Existing text content, or None if absent, binary, or unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _counts(diff_lines: list[str]) -> tuple[int, int]:
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def _cap(lines: list[str], max_lines: int, full: bool) -> tuple[str, bool]:
    if full or len(lines) <= max_lines:
        return "\n".join(lines), False
    remainder = len(lines) - max_lines
    shown = [*lines[:max_lines], f"… {remainder} more changed lines"]
    return "\n".join(shown), True


def _write_preview(
    args: dict[str, Any], project_root: Path, full: bool, max_lines: int
) -> DiffPreview:
    raw_path = str(args.get("file_path", ""))
    content = args.get("content")
    if not isinstance(content, str):
        content = ""
    path = _resolve(project_root, raw_path)

    if len(content.encode("utf-8", errors="replace")) > MAX_RENDER_BYTES:
        size = len(content.encode("utf-8", errors="replace"))
        return DiffPreview(f"write_file  {raw_path}  (too large to preview, {size} bytes)", "", False)
    if "\x00" in content:
        return DiffPreview(f"write_file  {raw_path}  (binary content, not rendered)", "", False)

    if not path.exists():
        lines = content.splitlines()
        body, truncated = _cap(lines[:NEW_FILE_PREVIEW_LINES], NEW_FILE_PREVIEW_LINES, full=full)
        truncated = truncated or len(lines) > NEW_FILE_PREVIEW_LINES
        size = len(content.encode("utf-8", errors="replace"))
        return DiffPreview(
            f"write_file  {raw_path}  (new file, {len(lines)} lines, {size} bytes)",
            body,
            truncated,
        )

    before = _read(path)
    if before is None:
        return DiffPreview(f"write_file  {raw_path}  (existing content unreadable)", "", False)

    diff = list(
        difflib.unified_diff(
            before.splitlines(), content.splitlines(), lineterm="", n=2, fromfile="", tofile=""
        )
    )
    added, removed = _counts(diff)
    if not diff:
        return DiffPreview(f"write_file  {raw_path}  (overwrite, no change)", "", False)
    body, truncated = _cap(diff[2:], max_lines, full)
    return DiffPreview(f"write_file  {raw_path}  +{added} -{removed}  (overwrite)", body, truncated)


def _edit_preview(args: dict[str, Any], full: bool, max_lines: int) -> DiffPreview:
    raw_path = str(args.get("file_path", ""))
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    diff = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), lineterm="", n=2, fromfile="", tofile=""
        )
    )
    added, removed = _counts(diff)
    body, truncated = _cap(diff[2:], max_lines, full)
    return DiffPreview(f"edit_file  {raw_path}  +{added} -{removed}", body, truncated)


def render(
    tool: str,
    args: dict[str, Any],
    project_root: Path,
    *,
    full: bool = False,
    max_lines: int = 20,
) -> DiffPreview:
    """Render one pending tool call for an approval prompt."""
    if tool == "write_file":
        return _write_preview(args, project_root, full, max_lines)
    if tool == "edit_file":
        return _edit_preview(args, full, max_lines)
    if tool == "delete":
        raw_path = str(args.get("file_path", ""))
        existing = _read(_resolve(project_root, raw_path))
        size = f", {len(existing.splitlines())} lines" if existing is not None else ""
        return DiffPreview(f"delete  {raw_path}{size}", "", False)
    if tool == "execute":
        command = str(args.get("command", ""))
        return DiffPreview("execute", f"  {command}\n  cwd: {project_root}", False)
    return DiffPreview(tool, "", False)


__all__ = ["MAX_RENDER_BYTES", "DiffPreview", "render"]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_diff.py -q`
Expected: PASS, 14 tests.

If `test_an_overwrite_header_carries_the_change_counts` fails on the exact numbers, check `n=2` context and that `_counts` runs over the full diff while `_cap` slices `diff[2:]` to drop the `---`/`+++` header lines.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/diff.py tests/test_permissions_diff.py
git add src/rudra/permissions/diff.py tests/test_permissions_diff.py
git commit -m "feat(permissions): capped diff preview for approval prompts

Unified diff for an overwrite or an edit, capped at 20 changed lines with
a +N/-M header; a new file shows its size and first 10 lines rather than a
diff against nothing. Binary or oversized content reports size and renders
nothing.

Closes the visible half of A1.16: a prompt that shows nothing does not fix
a silent overwrite, it delays it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: The approval prompt

**Files:**
- Create: `src/rudra/permissions/approval.py`
- Test: `tests/test_permissions_approval.py`

**Interfaces:**
- Consumes: `rules.PermissionEngine`, `rules.Rule`, `rules.gated_arg` (Task 3); `grants.SessionGrants` (Task 3); `diff.render` (Task 8); `audit.AuditLog` (Task 5).
- Produces:
  - `ApprovalLoopExceeded(RuntimeError)`
  - `MAX_APPROVAL_ROUNDS: int = 50`
  - `suggest_grant(tool: str, arg: str | None) -> Rule` — the rule `always` would add
  - `decide_action_requests(requests, *, engine, grants, audit, console, project_root, mode, reader) -> list[dict]` — the batched interrupt's `action_requests` in, a `decisions` list out

`reader` is a zero-argument callable returning the user's keystroke. Injecting it is what makes the prompt testable without a terminal; production passes a Rich-backed reader.

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_approval.py`:

```python
"""The terminal approval prompt (Step 7 spec §6.3).

The reader is injected, so every branch is exercised without a TTY.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from rudra.permissions.approval import (
    MAX_APPROVAL_ROUNDS,
    decide_action_requests,
    suggest_grant,
)
from rudra.permissions.audit import AuditLog
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import PermissionEngine, Rule


def scripted(*keys):
    """A reader returning each key in turn, then raising if over-consumed."""
    remaining = list(keys)

    def read() -> str:
        if not remaining:
            raise AssertionError("prompt asked for more input than the test scripted")
        return remaining.pop(0)

    return read


def run(tmp_path, requests, keys, *, mode="ask", grants=None, engine=None):
    console = Console(file=open(tmp_path / "out.txt", "w", encoding="utf-8"), width=100)
    audit = AuditLog(tmp_path / "audit.jsonl")
    decisions = decide_action_requests(
        requests,
        engine=engine
        or PermissionEngine(
            mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path
        ),
        grants=grants if grants is not None else SessionGrants(),
        audit=audit,
        console=console,
        project_root=tmp_path,
        mode=mode,
        reader=scripted(*keys),
    )
    console.file.close()
    output = (tmp_path / "out.txt").read_text(encoding="utf-8")
    entries = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ] if (tmp_path / "audit.jsonl").exists() else []
    return decisions, output, entries


def request(name="execute", **args):
    return {"name": name, "args": args or {"command": "pytest -q"}, "description": "d"}


# ── suggest_grant ─────────────────────────────────────────────────────────


def test_a_grant_for_execute_covers_the_first_word(tmp_path):
    assert suggest_grant("execute", "pytest -q tests/") == Rule("execute", "pytest*")


def test_a_grant_for_a_file_tool_covers_that_exact_path(tmp_path):
    assert suggest_grant("write_file", "src/app.py") == Rule("write_file", "src/app.py")


def test_a_grant_with_no_argument_covers_the_whole_tool(tmp_path):
    assert suggest_grant("execute", None) == Rule("execute", None)


# ── decisions ─────────────────────────────────────────────────────────────


def test_approve_returns_an_approve_decision(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["a"])
    assert decisions == [{"type": "approve"}]


def test_reject_returns_a_reject_decision_with_a_message(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["r"])
    assert decisions[0]["type"] == "reject"
    assert decisions[0]["message"]


def test_always_approves_this_call(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["A"])
    assert decisions == [{"type": "approve"}]


def test_always_records_a_session_grant(tmp_path):
    grants = SessionGrants()
    run(tmp_path, [request()], ["A"], grants=grants)
    assert len(grants) == 1


def test_a_granted_call_does_not_prompt_again(tmp_path):
    """The whole point of `always`: a fix loop prompts once, not twelve times."""
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    run(tmp_path, [request()], ["A"], grants=grants, engine=engine)
    # Only one key is scripted; a second prompt would raise from `scripted`.
    decisions, _, _ = run(
        tmp_path,
        [request(command="pytest -q tests/x")],
        [],
        grants=grants,
        engine=engine,
    )
    assert decisions == [{"type": "approve"}]


def test_the_diff_is_shown_for_a_write(tmp_path):
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    _, output, _ = run(
        tmp_path,
        [request("write_file", file_path="app.py", content="new\n")],
        ["a"],
    )
    assert "-old" in output and "+new" in output


def test_d_shows_the_full_diff_then_prompts_again(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"o{n}\n" for n in range(60)), encoding="utf-8")
    _, output, _ = run(
        tmp_path,
        [request("write_file", file_path="app.py", content="".join(f"n{n}\n" for n in range(60)))],
        ["d", "a"],
    )
    assert "more changed lines" not in output.split("o59")[-1] or output.count("+n0") >= 1


def test_an_unrecognised_key_reprompts(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["z", "a"])
    assert decisions == [{"type": "approve"}]


def test_each_request_in_a_batch_gets_its_own_decision(tmp_path):
    decisions, _, _ = run(
        tmp_path,
        [request(command="pytest -q"), request(command="ruff check")],
        ["a", "r"],
    )
    assert [d["type"] for d in decisions] == ["approve", "reject"]


def test_an_approval_is_audited(tmp_path):
    _, _, entries = run(tmp_path, [request()], ["a"])
    assert entries[0]["decision"] == "approve"
    assert entries[0]["source"] == "prompt"


def test_a_rejection_is_audited(tmp_path):
    _, _, entries = run(tmp_path, [request()], ["r"])
    assert entries[0]["decision"] == "reject"


def test_a_grant_is_audited_as_a_session_grant(tmp_path):
    _, _, entries = run(tmp_path, [request()], ["A"])
    assert any(entry["source"] == "session-grant" for entry in entries)


def test_a_request_the_engine_already_allows_is_auto_approved(tmp_path):
    """interrupt_on's `when` can over-fire in batch mode; re-check per call."""
    engine = PermissionEngine(
        mode="ask", allow=("execute:pytest*",), deny=(), floor_disable=(), project_root=tmp_path
    )
    decisions, _, _ = run(tmp_path, [request()], [], engine=engine)
    assert decisions == [{"type": "approve"}]


def test_a_request_the_engine_denies_is_auto_rejected(tmp_path):
    engine = PermissionEngine(
        mode="ask", allow=(), deny=("execute:pytest*",), floor_disable=(), project_root=tmp_path
    )
    decisions, _, _ = run(tmp_path, [request()], [], engine=engine)
    assert decisions[0]["type"] == "reject"


def test_max_approval_rounds_is_a_backstop_not_a_working_limit(tmp_path):
    assert MAX_APPROVAL_ROUNDS >= 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_approval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.approval'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/permissions/approval.py`:

```python
"""The terminal approval prompt and the interrupt/resume loop.

The prompt's reader is injected so every branch is testable without a TTY.
`run_with_approvals` (Task 10 adds it below) wraps `astream` rather than
changing its stream mode, because `__interrupt__` never appears in "values"
chunks and switching modes would force a rewrite of both parse loops in
main_agent.py — the same two functions carrying A1.20's unfixed counter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from rich.console import Console
from rich.panel import Panel

from rudra.permissions.audit import AuditLog
from rudra.permissions.diff import render
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import PermissionEngine, Rule, gated_arg

MAX_APPROVAL_ROUNDS = 50

_REJECT_MESSAGE = (
    "The user rejected this call. Do not retry it. Choose a different "
    "approach, or stop and explain what you need."
)


class ApprovalLoopExceeded(RuntimeError):
    """More approval rounds than any real run needs — something is wrong."""


def suggest_grant(tool: str, arg: str | None) -> Rule:
    """The rule `always` would add for this call.

    For a command, the first word plus `*`: approving `pytest -q` once
    should cover `pytest -q tests/x`, which is the case that makes `always`
    worth having. For a path, the exact path — widening a write grant by
    guessing a directory would grant more than the user saw.
    """
    if arg is None:
        return Rule(tool, None)
    if tool == "execute":
        first = arg.strip().split()
        return Rule(tool, f"{first[0]}*") if first else Rule(tool, None)
    return Rule(tool, arg)


def _render_request(
    console: Console, tool: str, args: dict[str, Any], project_root: Path, *, full: bool
) -> None:
    preview = render(tool, args, project_root, full=full)
    console.print(Panel(preview.header, title="approval required", border_style="yellow"))
    if preview.body:
        console.print(preview.body)


def decide_action_requests(
    action_requests: Iterable[dict[str, Any]],
    *,
    engine: PermissionEngine,
    grants: SessionGrants,
    audit: AuditLog,
    console: Console,
    project_root: Path,
    mode: str,
    reader: Callable[[], str],
) -> list[dict[str, Any]]:
    """Turn one batched interrupt's requests into a `decisions` list.

    Each request is re-checked against the engine first. `interrupt_on`'s
    `when` predicate fires per AI message, and a grant added while deciding
    an earlier request in the same batch must take effect for a later one —
    otherwise `always` would still prompt for the rest of the batch.
    """
    decisions: list[dict[str, Any]] = []

    for request in action_requests:
        tool = request.get("name", "")
        args = request.get("args") or {}
        arg = gated_arg(tool, args)
        decision = engine.decide(tool, args)

        if decision.effect == "allow":
            audit.record(tool, arg, decision, mode=mode, outcome="allow")
            decisions.append({"type": "approve"})
            continue
        if decision.effect == "deny":
            audit.record(tool, arg, decision, mode=mode, outcome="deny")
            decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
            continue

        grant = suggest_grant(tool, arg)
        full = False
        while True:
            _render_request(console, tool, args, project_root, full=full)
            console.print(
                f"[a]pprove  [r]eject  [A]lways ({grant})  [d]iff (full)", style="bold"
            )
            key = (reader() or "").strip()
            if key == "a":
                audit.record(tool, arg, decision, mode=mode, outcome="approve")
                decisions.append({"type": "approve"})
                break
            if key == "r":
                audit.record(tool, arg, decision, mode=mode, outcome="reject")
                decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
                break
            if key == "A":
                grants.add(grant)
                granted = engine.decide(tool, args)
                audit.record(tool, arg, granted, mode=mode, outcome="allow")
                decisions.append({"type": "approve"})
                break
            if key == "d":
                full = True
                continue
            console.print(f"[red]Unrecognised key {key!r}. Choose a, r, A, or d.[/red]")

    return decisions


__all__ = [
    "MAX_APPROVAL_ROUNDS",
    "ApprovalLoopExceeded",
    "decide_action_requests",
    "suggest_grant",
]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_approval.py -q`
Expected: PASS, 17 tests.

`test_a_granted_call_does_not_prompt_again` is the one that matters: it passes an empty key script, so any prompt at all raises from `scripted`. If it fails, the engine passed to `decide_action_requests` is not the one holding the `SessionGrants` instance the prompt mutates — they must be the same object, which is why `build_gate` (Task 11) constructs both together.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/approval.py tests/test_permissions_approval.py
git add src/rudra/permissions/approval.py tests/test_permissions_approval.py
git commit -m "feat(permissions): terminal approval prompt

approve / reject / always / diff. A grant from 'always' is held in memory
for the process and never written to config, so a run can widen what it
may do for its own lifetime and never what the user has persisted.

Each request in a batch is re-checked against the engine before prompting,
so a grant taken on one request applies to the rest of the same batch.

The reader is injected, which is what makes every branch testable with no
TTY.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Interrupts and the resume loop

**Files:**
- Create: `src/rudra/permissions/interrupts.py`
- Modify: `src/rudra/permissions/approval.py` (append `run_with_approvals`)
- Test: `tests/test_permissions_interrupts.py`

**Interfaces:**
- Consumes: `rules.PermissionEngine`, `rules.MUTATING_TOOLS` (Task 3); `approval.decide_action_requests`, `approval.MAX_APPROVAL_ROUNDS`, `approval.ApprovalLoopExceeded` (Task 9).
- Produces:
  - `build_interrupt_on(engine) -> dict[str, dict]`
  - `approval.run_with_approvals(agent, inputs, config, gate, console) -> AsyncIterator` — an async generator yielding exactly the chunks `agent.astream(...)` yields

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_interrupts.py`:

```python
"""interrupt_on wiring and the resume loop (Step 7 spec §6.1, §6.2)."""

from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rudra.permissions.interrupts import build_interrupt_on
from rudra.permissions.rules import MUTATING_TOOLS, PermissionEngine


class OneExecuteModel(BaseChatModel):
    command: str = "echo gated"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "one-execute"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "execute", "args": {"command": self.command}, "id": "c1"}],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def agent_for(tmp_path, mode="ask", **engine_kwargs):
    engine = PermissionEngine(
        mode=mode,
        allow=engine_kwargs.get("allow", ()),
        deny=(),
        floor_disable=(),
        project_root=tmp_path,
    )
    agent = create_deep_agent(
        model=OneExecuteModel(),
        backend=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True),
        interrupt_on=build_interrupt_on(engine),
        checkpointer=InMemorySaver(),
    )
    return agent, engine


def test_every_mutating_tool_gets_an_interrupt_config(tmp_path):
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    assert set(build_interrupt_on(engine)) == set(MUTATING_TOOLS)


def test_read_only_tools_get_no_interrupt_config(tmp_path):
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    assert "read_file" not in build_interrupt_on(engine)


def test_each_config_allows_approve_and_reject(tmp_path):
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    for config in build_interrupt_on(engine).values():
        assert config["allowed_decisions"] == ["approve", "reject"]


def test_an_ask_decision_actually_interrupts(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t1"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    state = agent.get_state(config)
    assert state.interrupts, "mode=ask did not pause the graph"


def test_an_allowed_call_does_not_interrupt(tmp_path):
    agent, _ = agent_for(tmp_path, allow=("execute:echo*",))
    config = {"configurable": {"thread_id": "t2"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    assert not agent.get_state(config).interrupts


def test_auto_mode_does_not_interrupt(tmp_path):
    agent, _ = agent_for(tmp_path, mode="auto")
    config = {"configurable": {"thread_id": "t3"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    assert not agent.get_state(config).interrupts


def test_resume_with_approve_runs_the_command(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t4"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert "gated" in tool_messages[0].content


def test_resume_with_reject_returns_the_message(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t5"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "no thanks"}]}), config
    )
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert tool_messages[0].content == "no thanks"


def test_the_resume_payload_must_be_a_dict_with_decisions(tmp_path):
    """A bare list raises TypeError from inside the middleware. Pin the shape."""
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t6"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    with pytest.raises(TypeError):
        agent.invoke(Command(resume=[{"type": "approve"}]), config)


@pytest.mark.asyncio
async def test_run_with_approvals_yields_chunks_and_resumes(tmp_path):
    from rich.console import Console

    from rudra.permissions import build_gate
    from rudra.config.loader import build_config

    agent, engine = agent_for(tmp_path)
    gate = build_gate(build_config(tmp_path), tmp_path)
    gate.engine = engine
    gate.reader = lambda: "a"

    console = Console(file=open(tmp_path / "out.txt", "w", encoding="utf-8"))
    config = {"configurable": {"thread_id": "t7"}}
    chunks = []
    from rudra.permissions.approval import run_with_approvals

    async for chunk in run_with_approvals(
        agent, {"messages": [{"role": "user", "content": "go"}]}, config, gate, console
    ):
        chunks.append(chunk)
    console.file.close()

    assert chunks, "run_with_approvals yielded nothing"
    assert not agent.get_state(config).interrupts, "the interrupt was never resumed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_interrupts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.permissions.interrupts'`

- [ ] **Step 3: Confirm `pytest-asyncio` is available**

Run: `.venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If it raises `ModuleNotFoundError`, add `"pytest-asyncio>=1.0"` to `[dependency-groups] dev` in `pyproject.toml`, add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`, run `uv sync`, and drop the `@pytest.mark.asyncio` decorator (auto mode makes it unnecessary). Commit that change with this task.

- [ ] **Step 4: Write `interrupts.py`**

Create `src/rudra/permissions/interrupts.py`:

```python
"""Turning `ask` decisions into deepagents interrupts.

Only the mutating tools get an `interrupt_on` entry. Read-only tools are
never gated, and giving them a config would make the `when` predicate run
on every read for an answer that is always False.

The `when` predicate is what keeps the two mechanisms consistent: it calls
the same PermissionEngine the deny middleware calls, so a call already
covered by an allow rule or a session grant never reaches a prompt.
"""

from __future__ import annotations

from typing import Any, Callable

from rudra.permissions.rules import MUTATING_TOOLS, PermissionEngine


def _predicate(engine: PermissionEngine, tool: str) -> Callable[[Any], bool]:
    """Bind `tool` per entry — a closure over the loop variable would make
    every predicate see the last tool name."""

    def when(request: Any) -> bool:
        call = getattr(request, "tool_call", None) or {}
        name = call.get("name", tool)
        args = call.get("args") or {}
        return engine.decide(name, args).effect == "ask"

    return when


def build_interrupt_on(engine: PermissionEngine) -> dict[str, dict[str, Any]]:
    """The `interrupt_on=` mapping for `create_deep_agent`."""
    return {
        tool: {
            "allowed_decisions": ["approve", "reject"],
            "when": _predicate(engine, tool),
        }
        for tool in sorted(MUTATING_TOOLS)
    }


__all__ = ["build_interrupt_on"]
```

- [ ] **Step 5: Append `run_with_approvals` to `approval.py`**

Add these imports at the top of `approval.py`:

```python
from collections.abc import AsyncIterator

from langgraph.types import Command
```

Then append:

```python
async def run_with_approvals(
    agent: Any,
    inputs: Any,
    config: dict[str, Any],
    gate: Any,
    console: Console,
) -> AsyncIterator[Any]:
    """Stream an agent, pausing for approval and resuming, transparently.

    Yields exactly what `agent.astream(...)` yields, so a caller's existing
    parse loop needs no change beyond the call itself.

    The interrupt is read from `get_state` after the stream drains rather
    than from the stream, because `__interrupt__` does not appear in
    "values" chunks and switching stream modes would change the chunk shape
    both of main_agent.py's parse loops depend on.
    """
    payload: Any = inputs
    for _ in range(MAX_APPROVAL_ROUNDS):
        async for chunk in agent.astream(
            payload, config, stream_mode="values", subgraphs=True
        ):
            yield chunk

        state = agent.get_state(config)
        interrupts = getattr(state, "interrupts", ()) or ()
        if not interrupts:
            return

        requests: list[dict[str, Any]] = []
        for interrupt in interrupts:
            value = getattr(interrupt, "value", None) or {}
            requests.extend(value.get("action_requests", []))

        payload = Command(resume={"decisions": gate.prompt(requests, console)})

    raise ApprovalLoopExceeded(
        f"Stopped after {MAX_APPROVAL_ROUNDS} approval rounds. A real run needs "
        f"a handful; this many means the agent is looping rather than progressing."
    )
```

Add `"run_with_approvals"` to `approval.py`'s `__all__`.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_interrupts.py -q`
Expected: PASS, 10 tests.

The async test depends on `build_gate` from Task 11 and will fail until it exists. Either implement Task 11 first and return here, or mark that one test `@pytest.mark.xfail(reason="build_gate lands in Task 11", strict=True)` and remove the marker in Task 11's Step 5. Do not leave a non-strict xfail behind — a test that silently passes is worse than one that fails.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/ tests/test_permissions_interrupts.py
git add src/rudra/permissions/interrupts.py src/rudra/permissions/approval.py tests/test_permissions_interrupts.py pyproject.toml
git commit -m "feat(permissions): interrupt_on wiring and the resume loop

build_interrupt_on gives every mutating tool a `when` predicate that calls
the same engine the deny middleware calls, so an allowed or granted call
never reaches a prompt.

run_with_approvals wraps astream and reads the interrupt from get_state
after the stream drains, because __interrupt__ never appears in \"values\"
chunks. Switching stream modes would change the chunk shape both of
main_agent.py's parse loops depend on — and those are the same two
functions carrying A1.20's unfixed counter.

A test pins the resume payload shape: Command(resume={\"decisions\": [...]}),
not a bare list, which raises TypeError from inside the middleware.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: `build_gate` — one constructor

**Files:**
- Modify: `src/rudra/permissions/__init__.py`
- Test: `tests/test_permissions_gate.py`

**Interfaces:**
- Consumes: everything from Tasks 2–10.
- Produces:
  - `Gate` — a mutable dataclass with `engine`, `middleware`, `interrupt_on`, `grants`, `audit`, `mode`, `project_root`, `reader`, and `prompt(action_requests, console) -> list[dict]`
  - `build_gate(cfg, project_path) -> Gate`
  - `disabled_floor_notice(cfg) -> str | None` — the run-start warning naming disabled floor rules, or `None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_permissions_gate.py`:

```python
"""build_gate: the one constructor everything outside the package uses."""

from __future__ import annotations

import pytest
from rich.console import Console

from rudra.config.loader import build_config, reset_config
from rudra.permissions import Gate, build_gate, disabled_floor_notice
from rudra.permissions.rules import MUTATING_TOOLS


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_build_gate_returns_a_gate(tmp_path):
    assert isinstance(build_gate(build_config(tmp_path), tmp_path), Gate)


def test_the_gate_carries_an_interrupt_config_for_every_mutating_tool(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert set(gate.interrupt_on) == set(MUTATING_TOOLS)


def test_the_engine_and_the_grants_are_the_same_objects_the_prompt_mutates(tmp_path):
    """`always` only works if the engine consults the grants the prompt fills."""
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.engine.grants is gate.grants


def test_the_audit_log_lands_under_run_logs(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.audit.path == tmp_path / ".rudra" / "run" / "logs" / "permissions.jsonl"


def test_the_gate_carries_the_configured_mode(tmp_path):
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n')
    assert build_gate(build_config(root), root).mode == "auto"


def test_auto_mode_still_produces_interrupt_configs(tmp_path):
    """The `when` predicate answers False in auto mode; the entries stay."""
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n')
    gate = build_gate(build_config(root), root)
    assert set(gate.interrupt_on) == set(MUTATING_TOOLS)


def test_rules_from_config_reach_the_engine(tmp_path):
    root = write_config(tmp_path, '[permissions]\ndeny = ["execute:rm *"]\n')
    gate = build_gate(build_config(root), root)
    assert gate.engine.decide("execute", {"command": "rm x"}).effect == "deny"


def test_a_malformed_rule_raises_at_build_time_not_at_first_call(tmp_path):
    root = write_config(tmp_path, '[permissions]\ndeny = ["nosuchtool:x"]\n')
    with pytest.raises(ValueError, match="Unknown tool 'nosuchtool'"):
        build_gate(build_config(root), root)


def test_prompt_delegates_to_the_approval_flow(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    gate.reader = lambda: "a"
    console = Console(file=open(tmp_path / "out.txt", "w", encoding="utf-8"))
    decisions = gate.prompt(
        [{"name": "execute", "args": {"command": "pytest -q"}, "description": "d"}], console
    )
    console.file.close()
    assert decisions == [{"type": "approve"}]


def test_no_floor_notice_when_nothing_is_disabled(tmp_path):
    assert disabled_floor_notice(build_config(tmp_path)) is None


def test_the_floor_notice_names_every_disabled_rule(tmp_path):
    root = write_config(
        tmp_path, '[permissions]\nfloor_disable = ["outside-root", "git-dir"]\n'
    )
    notice = disabled_floor_notice(build_config(root))
    assert "outside-root" in notice and "git-dir" in notice
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_permissions_gate.py -q`
Expected: FAIL — `ImportError: cannot import name 'Gate' from 'rudra.permissions'`

- [ ] **Step 3: Write the implementation**

Replace `src/rudra/permissions/__init__.py` entirely:

```python
"""Rudra's permission layer (Step 7 — C3.1-C3.4).

deepagents' own `permissions=` cannot be used: it raises NotImplementedError
on any backend that supports command execution, and its FilesystemOperation
is ('read', 'write') only, so it never covered `execute` regardless. See
TODO.md U.7 and A1.46.

Everything outside this package goes through `build_gate`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Console

from rudra.permissions.approval import (
    ApprovalLoopExceeded,
    decide_action_requests,
    run_with_approvals,
)
from rudra.permissions.audit import AuditLog
from rudra.permissions.grants import SessionGrants
from rudra.permissions.interrupts import build_interrupt_on
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import PermissionEngine
from rudra.state.paths import rudra_paths

if TYPE_CHECKING:  # pragma: no cover
    from rudra.config.loader import Config


def _default_reader() -> str:
    """One line from the terminal. Replaced wholesale in tests."""
    try:
        return input().strip()
    except EOFError:
        return "r"


@dataclass
class Gate:
    """Everything the agent constructor needs, built together.

    The engine and the grants must be the same objects the prompt mutates,
    which is why they are constructed here rather than separately at each
    call site — an engine holding a different SessionGrants would accept
    `always` and then prompt again on the next call.
    """

    engine: PermissionEngine
    middleware: RudraPermissionMiddleware
    interrupt_on: dict[str, Any]
    grants: SessionGrants
    audit: AuditLog
    mode: str
    project_root: Path
    reader: Callable[[], str] = field(default=_default_reader)

    def prompt(self, action_requests: list[dict[str, Any]], console: Console) -> list[dict]:
        return decide_action_requests(
            action_requests,
            engine=self.engine,
            grants=self.grants,
            audit=self.audit,
            console=console,
            project_root=self.project_root,
            mode=self.mode,
            reader=self.reader,
        )


def build_gate(cfg: Config, project_path: Path) -> Gate:
    """Construct the permission layer for one project run.

    Malformed rules raise here, at construction, rather than at the first
    tool call — a run that is going to fail on its rules should fail before
    it spends a planner pass.
    """
    grants = SessionGrants()
    engine = PermissionEngine(
        mode=cfg.permissions.mode,
        allow=cfg.permissions.allow,
        deny=cfg.permissions.deny,
        floor_disable=cfg.permissions.floor_disable,
        project_root=project_path,
        grants=grants,
    )
    audit = AuditLog(rudra_paths(project_path).logs / "permissions.jsonl")
    return Gate(
        engine=engine,
        middleware=RudraPermissionMiddleware(engine, audit, cfg.permissions.mode),
        interrupt_on=build_interrupt_on(engine),
        grants=grants,
        audit=audit,
        mode=cfg.permissions.mode,
        project_root=Path(project_path),
    )


def disabled_floor_notice(cfg: Config) -> str | None:
    """One line naming every disabled floor rule, or None.

    Printed at run start so a config edited months ago cannot quietly stay
    off (spec §4.5).
    """
    disabled = cfg.permissions.floor_disable
    if not disabled:
        return None
    return (
        f"Deny-floor rules disabled: {', '.join(disabled)}. "
        f"Calls they would have blocked are still recorded in the audit log."
    )


def stdin_is_interactive() -> bool:
    """Whether an approval prompt can actually reach a human."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


__all__ = [
    "ApprovalLoopExceeded",
    "Gate",
    "build_gate",
    "disabled_floor_notice",
    "run_with_approvals",
    "stdin_is_interactive",
]
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_permissions_gate.py -q`
Expected: PASS, 11 tests.

- [ ] **Step 5: Remove Task 10's xfail marker if one was added**

Run: `.venv/bin/pytest tests/test_permissions_interrupts.py -q`
Expected: PASS, 10 tests, no xfail reported.

- [ ] **Step 6: Run the whole permissions suite together**

Run: `.venv/bin/pytest tests/test_permissions_*.py -q`
Expected: PASS. Running them together is what catches an import cycle that per-file runs hide.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/permissions/ tests/
git add src/rudra/permissions/__init__.py tests/test_permissions_gate.py tests/test_permissions_interrupts.py
git commit -m "feat(permissions): build_gate, the single constructor

Gate bundles the engine, the deny middleware, the interrupt_on map, the
session grants, and the audit log. They are constructed together because
they must share objects: an engine holding a different SessionGrants would
accept 'always' and then prompt again on the very next call.

Malformed rules raise at construction rather than at the first tool call,
so a run that will fail on its rules fails before spending a planner pass.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: The composite backend — shell goes live

This is the task that makes `execute` work. It lands after the gate exists, never before: hard gate #2 says `LocalShellBackend` must never ship without the permission layer, and the ordering here is what honours that.

**Files:**
- Modify: `src/rudra/state/paths.py` (add `artifacts`)
- Modify: `src/rudra/agent/main_agent.py:78-88` (`RudraAgent.__init__`), `:310,325,343` (stream call sites), `:554-570` (backend construction), `:595-609` (`coder_config` and the `RudraAgent(...)` return)
- Modify: `src/rudra/agent/coder_agent.py:44-48,77-87`
- Modify: `src/rudra/agent/planner_agent.py:105-117`
- Test: `tests/test_backend_wiring.py`, `tests/test_rudra_dir_migration.py` (extend)

**Interfaces:**
- Consumes: `permissions.build_gate`, `permissions.run_with_approvals` (Task 11); `permissions.env.scrubbed_env` (Task 7); `ToolsConfig` (Task 4).
- Produces:
  - `RudraPaths.artifacts: Path` — `.rudra/run/artifacts`
  - `build_backend(cfg, project_path) -> CompositeBackend` in `main_agent.py`
  - `create_coder_agent(..., gate=None)` and `create_planner_agent(..., gate=None)`
  - `RudraAgent.__init__(..., gate=None)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_backend_wiring.py`:

```python
"""The composite backend (Step 7 spec §5). Closes C3.1 and C3.2.

Also pins A1.45: artifacts must never land in the user's project.
"""

from __future__ import annotations

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.filesystem import FilesystemMiddleware

from rudra.agent.main_agent import build_backend
from rudra.config.loader import build_config, reset_config
from rudra.state.paths import ensure_layout, rudra_paths


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_paths_expose_an_artifacts_directory(tmp_path):
    assert rudra_paths(tmp_path).artifacts == tmp_path / ".rudra" / "run" / "artifacts"


def test_ensure_layout_creates_the_artifacts_directory(tmp_path):
    assert ensure_layout(tmp_path).artifacts.is_dir()


def test_artifacts_are_inside_the_gitignored_run_subtree(tmp_path):
    """D15: run/ is volatile. Artifacts churn, so they belong under it."""
    ensure_layout(tmp_path)
    body = (tmp_path / ".rudra" / ".gitignore").read_text(encoding="utf-8")
    assert "run/" in body


def test_the_backend_is_a_composite(tmp_path):
    ensure_layout(tmp_path)
    assert isinstance(build_backend(build_config(tmp_path), tmp_path), CompositeBackend)


def test_the_default_is_a_local_shell_backend(tmp_path):
    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    assert isinstance(backend.default, LocalShellBackend)


def test_execute_actually_runs_a_command(tmp_path):
    """This is C3.1's whole point. U.17 proved a plain FilesystemBackend cannot."""
    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    result = backend.execute("echo shell-is-live")
    assert result.exit_code == 0
    assert "shell-is-live" in result.output


def test_commands_run_in_the_project_directory(tmp_path):
    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    assert str(tmp_path) in backend.execute("pwd").output


def test_the_shell_environment_carries_path(tmp_path):
    """A1.44: the backend default is an EMPTY environment."""
    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    assert backend.execute("echo $PATH").output.strip()


def test_no_secret_reaches_the_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-canary-not-in-shell")
    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    assert "sk-canary-not-in-shell" not in backend.execute("env").output


def test_artifacts_are_routed_out_of_the_project(tmp_path):
    """A1.45: artifacts_root defaults to the backend root, i.e. the repo."""
    ensure_layout(tmp_path)
    middleware = FilesystemMiddleware(backend=build_backend(build_config(tmp_path), tmp_path))
    assert middleware._large_tool_results_prefix.startswith("/artifacts/")
    assert middleware._conversation_history_prefix.startswith("/artifacts/")


def test_shell_false_yields_a_backend_without_execution(tmp_path):
    root = write_config(tmp_path, "[tools]\nshell = false\n")
    ensure_layout(root)
    backend = build_backend(build_config(root), root)
    assert not isinstance(backend.default, LocalShellBackend)


def test_permissions_are_never_passed_to_deepagents(tmp_path):
    """U.7: passing them with an execute-capable backend raises. Guard it."""
    import inspect

    from rudra.agent import coder_agent, main_agent, planner_agent

    for module in (main_agent, coder_agent, planner_agent):
        source = inspect.getsource(module)
        assert "permissions=" not in source, f"{module.__name__} passes permissions="
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backend_wiring.py -q`
Expected: FAIL — `AttributeError: 'RudraPaths' object has no attribute 'artifacts'`

- [ ] **Step 3: Add `artifacts` to `paths.py`**

Add the field to `RudraPaths` after `logs`:

```python
    logs: Path
    artifacts: Path
```

In `rudra_paths`, add to the constructor call after `logs=run / "logs",`:

```python
        artifacts=run / "artifacts",
```

In `ensure_layout`, extend the directory tuple:

```python
    for directory in (
        paths.root,
        paths.run,
        paths.logs,
        paths.artifacts,
        paths.memory_export,
        paths.memory_palace,
    ):
```

No `.gitignore` change is needed — `run/` already covers it, which is exactly why artifacts belong there.

- [ ] **Step 4: Add `build_backend` to `main_agent.py`**

Replace the backend construction at `main_agent.py:554-570` — the `from deepagents.backends.filesystem import FilesystemBackend` import, the explanatory comment, and the `filesystem_backend = FilesystemBackend(...)` call — with a call to a new module-level function. Add this function near the other module-level helpers, above `create_main_agent`:

```python
def build_backend(cfg, project_path: Path):
    """The backend for one run: shell on the default, artifacts on a route.

    A composite from the start per D13 — Step 11 adds a "/skills/" route to
    `routes` and changes nothing else. Retrofitting the composite later
    would rewire every agent constructor.

    `artifacts_root` matters more than it looks. It defaults to "/", i.e.
    the backend root, i.e. the user's project — so deepagents' oversized
    tool-result eviction and its summarization middleware would both write
    into the repo Rudra is working on (TODO.md A1.45). Routing it into
    .rudra/run/artifacts/ puts both in D15's volatile subtree.
    """
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.backends.local_shell import LocalShellBackend

    from rudra.permissions.env import scrubbed_env
    from rudra.state.paths import rudra_paths

    paths = rudra_paths(project_path)

    if cfg.tools.shell:
        # inherit_env is not used: its True form would pass every API key the
        # user exported straight to a model-driven shell (spec §5.3).
        default = LocalShellBackend(
            root_dir=str(project_path), virtual_mode=True, env=scrubbed_env(cfg)
        )
    else:
        default = FilesystemBackend(root_dir=str(project_path), virtual_mode=True)

    return CompositeBackend(
        default=default,
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )
```

In `create_main_agent`, replace the deleted construction with:

```python
    filesystem_backend = build_backend(cfg, project_path)
```

`cfg` must be in scope. `create_main_agent` already calls `get_config()` for the model names at `:546-547`; hoist one call into a local `cfg = get_config()` above and reuse it, rather than calling it three times.

- [ ] **Step 5: Thread the gate through both agent constructors**

In `coder_agent.py`, change `create_coder_agent`'s signature to accept `gate=None` and pass it through:

```python
def create_coder_agent(
    tech_stack_content,
    filesystem_backend,
    checkpointer,
    gate=None,
):
```

and its `create_deep_agent(...)` call at `:77-87`:

```python
    middleware = build_coder_middleware(
        compat_task_anchor=cfg.compat.task_anchor,
        compat_sandbox_paths=cfg.compat.sandbox_paths,
    )
    if gate is not None:
        # First in the list: a denied call must be stopped before any other
        # middleware rewrites its arguments.
        middleware.insert(0, gate.middleware)

    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=_CODER_SYSTEM_PROMPT,
        backend=filesystem_backend,
        checkpointer=checkpointer,
        middleware=middleware,
        interrupt_on=gate.interrupt_on if gate is not None else None,
    )
```

Apply the identical change to `planner_agent.py`'s `create_planner_agent` and its `create_deep_agent(...)` at `:105-117`, keeping its existing `memory=[".rudra/AGENTS.md"]` and `tools=custom_tools` arguments untouched.

**Do not add `permissions=`.** `tests/test_backend_wiring.py::test_permissions_are_never_passed_to_deepagents` fails if anyone does.

- [ ] **Step 6: Wire the gate into `RudraAgent` and the stream calls**

In `RudraAgent.__init__` (`:72-88`), add a `gate=None` parameter after `plan_path` and store it:

```python
        self.gate = gate
```

In `create_main_agent`, build the gate once and pass it to both agents and to `RudraAgent`:

```python
    from rudra.permissions import build_gate

    gate = build_gate(cfg, project_path)
```

Add `"gate": gate` to the `coder_config` dict at `:595`, pass `gate=gate` to `create_planner_agent`, and add `gate=gate` to the `RudraAgent(...)` return at `:603-609`.

Then in both `_stream_planner` (`:159`) and `_stream_coder` (`:234`), replace the `astream` call. Before:

```python
        async for chunk in self.planner_agent.astream(
            {"messages": messages},
            lg_config,
            stream_mode="values",
            subgraphs=True,
        ):
```

After:

```python
        from rudra.permissions import run_with_approvals

        async for chunk in run_with_approvals(
            self.planner_agent, {"messages": messages}, lg_config, self.gate, self.console
        ):
```

and the coder equivalent with `coder_agent` in place of `self.planner_agent`. **Change nothing below those lines.** The parse bodies stay byte-identical — that is the design commitment in spec §6.2, and it is what keeps A1.20's shared-counter hole exactly as unfixed as it already is rather than tangling this step with it.

`run_with_approvals` must tolerate `gate is None` so a caller that has not built one still streams. Add at the top of its loop body in `approval.py`:

```python
        if gate is None:
            async for chunk in agent.astream(
                payload, config, stream_mode="values", subgraphs=True
            ):
                yield chunk
            return
```

- [ ] **Step 7: Extend the path-migration guard**

`tests/test_rudra_dir_migration.py` exists to fail when code and prompt text disagree about a `.rudra/` path. Add:

```python
def test_the_artifacts_route_matches_the_paths_module(tmp_path):
    """The composite's route prefix and the on-disk directory must agree."""
    from rudra.agent.main_agent import build_backend
    from rudra.config.loader import build_config
    from rudra.state.paths import ensure_layout, rudra_paths

    ensure_layout(tmp_path)
    backend = build_backend(build_config(tmp_path), tmp_path)
    assert "/artifacts/" in backend.routes
    assert rudra_paths(tmp_path).artifacts.is_dir()
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/pytest tests/test_backend_wiring.py tests/test_rudra_dir_migration.py -q`
Expected: PASS.

- [ ] **Step 9: Run the whole suite — this task edits the agent constructor**

Run: `.venv/bin/pytest -q`
Expected: at least `286 passed`. `tests/test_agent_wiring.py` asserts on `main_agent.py`'s AST for the old `FilesystemBackend(virtual_mode=True)` construction and **will** fail. Rewrite it to assert the property rather than the syntax — that a composite is built whose default supports execution — which is the same correction Step 6 applied to that very file.

- [ ] **Step 10: Confirm shell is genuinely live**

Run:

```bash
.venv/bin/python - <<'EOF'
import tempfile
from pathlib import Path
from rudra.agent.main_agent import build_backend
from rudra.config.loader import build_config
from rudra.state.paths import ensure_layout

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    ensure_layout(root)
    backend = build_backend(build_config(root), root)
    print(backend.execute("which git; python3 --version").output)
EOF
```

Expected: a real `git` path and a Python version. If either is empty, `scrubbed_env` is not reaching the constructor — A1.44 is not actually fixed.

- [ ] **Step 11: Commit**

```bash
git add src/rudra/state/paths.py src/rudra/agent/ tests/test_backend_wiring.py tests/test_rudra_dir_migration.py tests/test_agent_wiring.py
git commit -m "feat(agent): composite backend with shell and the permission gate

C3.1: the backend becomes CompositeBackend(default=LocalShellBackend), so
execute works. U.17 proved a plain FilesystemBackend registers the tool and
returns an error when it is called.

C3.2 needs no offload code: deepagents already evicts oversized tool
results. It just needed routing — artifacts_root defaults to the backend
root, so both large_tool_results/ and conversation_history/ would have
landed in the user's project (A1.45). Both now go to .rudra/run/artifacts/.

The gate is built once and threaded into both agents. Shell and the
permission layer land in the same commit, per Section E hard gate 2.

The two astream call sites change by one line each; their parse bodies are
byte-identical, which is what keeps A1.20 exactly as unfixed as it was
rather than tangling it with this step.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: CLI — the TTY check and honest messaging

Step 6 deliberately printed "NOT ENFORCED" on the default path. That text is now false and must go in the same step that makes it false.

**Files:**
- Modify: `src/rudra/cli.py:261-271` (`_permission_notice`), `:418-423` (`doctor`), `:492-496` (flag help), `:531-534` (callback)
- Test: `tests/test_cli_permissions.py`

**Interfaces:**
- Consumes: `permissions.stdin_is_interactive`, `permissions.disabled_floor_notice` (Task 11).
- Produces: `EXIT_NO_TTY = 2` in `cli.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_permissions.py`:

```python
"""CLI permission surface: the TTY gate and honest messaging (spec §6.5)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config.loader import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def test_ask_mode_without_a_tty_exits_two(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert result.exit_code == 2


def test_the_message_names_both_escape_hatches(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert "--auto" in result.output
    assert 'mode = "auto"' in result.output or "permissions.mode" in result.output


def test_no_model_is_constructed_when_the_tty_check_fails(tmp_path, monkeypatch):
    """Failing at second zero is the point — it must cost no inference."""
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    built = []
    monkeypatch.setattr(
        "rudra.llm.factory.build_model", lambda *a, **k: built.append(1)
    )
    runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert built == []


def test_auto_mode_runs_without_a_tty(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "--auto", "x"])
    assert result.exit_code != 2


def test_plan_mode_runs_without_a_tty(tmp_path, monkeypatch):
    """plan mode makes no changes, so it needs no approvals."""
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "--plan", "x"])
    assert result.exit_code != 2


def test_the_not_enforced_wording_is_gone_from_the_cli(tmp_path):
    import inspect

    from rudra import cli

    assert "NOT ENFORCED" not in inspect.getsource(cli)


def test_doctor_reports_the_permission_mode(tmp_path):
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "permissions" in result.output.lower()


def test_doctor_does_not_claim_permissions_are_unenforced(tmp_path):
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "not enforced" not in result.output.lower()


def test_the_flag_help_no_longer_mentions_step_seven(tmp_path):
    result = runner.invoke(app, ["--help"])
    assert "until Step 7" not in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_permissions.py -q`
Expected: FAIL — the "NOT ENFORCED" assertions fail because the text is still there.

- [ ] **Step 3: Replace `_permission_notice`**

`cli.py:261-271` currently returns a string saying `permissions: <mode> — NOT ENFORCED (Step 7); …`. Replace the whole function:

```python
EXIT_NO_TTY = 2


def _permission_notice(cfg) -> str:
    """One line describing what the permission mode will actually do."""
    described = {
        "ask": "prompting before each write, edit, delete, or command",
        "auto": "approving everything without prompting",
        "plan": "making no project changes and running no commands",
    }
    line = f"permissions: {cfg.permissions.mode} — {described[cfg.permissions.mode]}"
    if cfg.permissions.floor_disable:
        line += f" · floor disabled: {', '.join(cfg.permissions.floor_disable)}"
    return line
```

- [ ] **Step 4: Add the TTY gate to the main callback**

In `main`, immediately after the `cfg = get_config(...)` line at `:532` and before anything else uses `cfg`:

```python
    from rudra.permissions import disabled_floor_notice, stdin_is_interactive

    if cfg.permissions.mode == "ask" and not stdin_is_interactive():
        console.print(
            '[red]Error:[/red] permissions.mode = "ask" needs an interactive '
            "terminal, but stdin is not a TTY.\n\n"
            "  --auto                      approve everything (unattended)\n"
            '  permissions.mode = "auto"   same, persisted in .rudra/config.toml\n'
        )
        raise typer.Exit(EXIT_NO_TTY)

    floor_notice = disabled_floor_notice(cfg)
    if floor_notice:
        console.print(f"[yellow]Warning:[/yellow] {floor_notice}")
```

The check sits before `load_project_context` and before any agent construction, so the promise "no model call was made" is structural rather than incidental.

- [ ] **Step 5: Fix the flag help and the doctor section**

At `:492-496`, drop the stale parentheticals:

```python
    auto: bool = typer.Option(
        False, "--auto", "--yolo", help="Approve every action without prompting"
    ),
    plan: bool = typer.Option(
        False, "--plan", help="Plan only: make no project changes, run no commands"
    ),
```

Delete the `if permission_mode is not None: console.print(...)` block at `:533-534` — the mode now appears in the run panel and in `_permission_notice`, so a second line is noise.

At `:418-423`, replace `doctor`'s permissions rows:

```python
    rows.append(
        (
            "permissions",
            f"mode = {cfg.permissions.mode} — "
            f"{len(cfg.permissions.allow)} allow, {len(cfg.permissions.deny)} deny, "
            f"floor disabled: {', '.join(cfg.permissions.floor_disable) or 'none'}",
        )
    )
    rows.append(("tools.shell", "enabled" if cfg.tools.shell else "disabled"))
```

Match the surrounding call's actual shape — read `:410-425` and follow it rather than assuming a two-tuple.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_permissions.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: at least `286 passed`. `tests/test_cli_smoke.py` may assert on the old notice text; fix it to assert behavior.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/ tests/
git add src/rudra/cli.py tests/test_cli_permissions.py tests/test_cli_smoke.py
git commit -m "feat(cli): enforce the permission mode, and stop saying it is not enforced

Step 6 printed 'NOT ENFORCED' on the default path deliberately, because an
inert default of ask claims more safety than you get. That text is now
false, so it goes in the same step that makes it false.

ask mode without a TTY exits 2 before any model is constructed. In ask mode
every write is gated and writing files is Rudra's whole job, so a non-TTY
ask run would hit a prompt within seconds — failing at second zero is the
honest version of failing at second thirty.

Disabled floor rules are named at run start, so a config edited months ago
cannot quietly stay off.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 14: Contract guard, docs, acceptance, close-out

**Files:**
- Modify: `tests/test_deepagents_contract.py`
- Modify: `CLAUDE.md` §3, §4, §6, §8
- Modify: `TODO.md`
- Test: the acceptance run itself

**Interfaces:**
- Consumes: everything.
- Produces: `test_permissions_still_rejected_with_execute_backend` — the test that reopens `U.7` if upstream ever lifts the restriction.

- [ ] **Step 1: Write the contract guard**

Append to `tests/test_deepagents_contract.py`:

```python
def test_permissions_still_rejected_with_execute_backend(tmp_path):
    """U.7's revisit trigger.

    deepagents 0.7.4 refuses `permissions=` on any backend implementing
    SandboxBackendProtocol, which is why Rudra owns its permission layer
    instead of adopting the upstream one (TODO.md U.7). When this test
    starts FAILING, upstream has lifted the restriction and U.7 can reopen.
    """
    import pytest
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents.middleware.filesystem import FilesystemPermission

    from deepagents import create_deep_agent

    backend = CompositeBackend(
        default=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True), routes={}
    )
    with pytest.raises(NotImplementedError, match="does not yet support permissions"):
        create_deep_agent(
            model=ScriptedToolModel(),
            backend=backend,
            permissions=[
                FilesystemPermission(operations=["write"], paths=["/x/**"], mode="deny")
            ],
        )


def test_filesystem_operations_still_exclude_execute():
    """A1.46: `permissions=` never covered execute, only read and write."""
    import typing

    from deepagents.middleware.filesystem import FilesystemOperation

    assert typing.get_args(FilesystemOperation) == ("read", "write")
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/test_deepagents_contract.py -q`
Expected: PASS.

- [ ] **Step 3: Correct `CLAUDE.md`**

Four edits, all of which the ledger already requires:

**§3, the architecture tree** — add the package after `middleware/`:

```
├── permissions/            Gate: rules, floor, grants, diff, audit, approval (Step 7)
```

**§3, the `.rudra/` table** — add a row after `run/checkpoints.db`:

```
| `run/logs/permissions.jsonl` | volatile | `AuditLog` | humans, later `rudra audit` | Every gated decision |
| `run/artifacts/` | volatile | deepagents eviction + summarization | the agent, via `/artifacts/` route | Kept out of the project by `artifacts_root` (A1.45) |
```

**§4** — replace the `**Critical:**` paragraph. The current text asserts `execute` returns an error and then contradicts itself with a parenthetical about U.17. Both halves are now settled:

```markdown
**Critical:** `execute` only works on a backend implementing
`SandboxBackendProtocol`. `LocalShellBackend` does; plain `FilesystemBackend`
does not — the tool is still *registered* in the default stack, and returns
`"Error: Execution not available. This agent's backend does not support
command execution (SandboxBackendProtocol)."` when called. Measured through a
real graph run, closing U.17. **Since Step 7 Rudra ships
`CompositeBackend(default=LocalShellBackend(...))`, so `execute` works** and
agents can run tests, linters, builds, and git.

`permissions=` is deliberately **never** passed: it raises
`NotImplementedError` on any execute-capable backend, and its
`FilesystemOperation` is `('read','write')` only, so it never covered
`execute` regardless. Rudra's own gate in `permissions/` does the whole job.
See TODO.md U.7 and A1.46.
```

Also update the `permissions=` and `interrupt_on` rows of the table above it: `permissions=` → ❌ **cannot be used** (U.7); `interrupt_on` → ✅ approval gates, Step 7.

**§6** — replace the `[permissions]` and `[tools]` blocks of the example with the real syntax, matching `config/template.py` exactly, and correct the reserved-sections sentence to name only `[skills]` (11) and `[memory]` (14).

**§8** — update the pytest count to whatever the suite now reports, and add:

```bash
.venv/bin/rudra --auto "..."        # unattended: approve everything
.venv/bin/rudra --plan "..."        # plan only: no writes, no commands
```

- [ ] **Step 4: Run the acceptance suite**

All four runs from a `mktemp -d` outside the repo, under `env -i` with no `RUDRA_*` or `OLLAMA_*` set, configured only by `.rudra/config.toml`. Capture every command and its output — this text goes into the ledger row, not a scratch file, because U.12's `/tmp` logs no longer resolve.

**Run 1 — interactive `ask`.** Task: write a small script and run it. Approve the write after reading its diff; approve the command; use `A` on a repeated command and confirm the second occurrence does not prompt.

```bash
WORK=$(mktemp -d) && cd "$WORK"
env -i HOME="$HOME" PATH="$PATH" TERM="$TERM" \
  <repo>/.venv/bin/rudra init
# edit .rudra/config.toml for a reachable provider, then:
env -i HOME="$HOME" PATH="$PATH" TERM="$TERM" \
  <repo>/.venv/bin/rudra "write wordcount.py that counts lines and words in a file, then run it on a sample"
echo "exit=$?"
cat .rudra/run/logs/permissions.jsonl
```

Expected: exits 0; the generated script exists and runs correctly; the audit log holds one `approve` per prompt and a `session-grant` entry for the repeated command.

**Run 2 — unattended `--auto`.** The same task in a fresh tempdir with `--auto`. Expected: no prompts, exits 0, audit log present.

**Run 3 — the floor, both ways.** In a fresh tempdir under `--auto`, a task told to write outside the project root. Expected: denied, `source: "floor"` in the audit log, and the file absent. Then add `floor_disable = ["outside-root"]` and repeat: the write succeeds, the run start prints the warning, and the audit line reads `source: "floor-disabled"`.

**Run 4 — non-TTY.** The Run 1 command with `< /dev/null` and no `--auto`. Expected: exit 2, the message naming both escape hatches, and no model call — confirm by the absence of any provider latency and of `.rudra/run/checkpoints.db` growth.

- [ ] **Step 5: Final local verification**

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
git status --short
```

Expected: `All checks passed!`; formatting clean; pass count at or above 286 plus the ~100 tests this step adds; `Rudra v0.2.0`; a clean tree apart from the files this task edits.

- [ ] **Step 6: Close the ledger**

In `TODO.md`:

- `C3.1`, `C3.2`, `C3.3`, `C3.4` → **DONE** with the verification output pasted in. `C3.2`'s row must say plainly that no offload code was written because deepagents already evicts oversized results, that the work was routing `artifacts_root` out of the project, and that the eviction **threshold** was found unreachable and is tracked as `A1.47` rather than silently left at 20 000 as though that were a choice.
- `A1.16` → **DONE**, closed by `C3.4`, noting that its evidence pointer (`compat/overwrite_backend.py:50-65`) has pointed at a file deleted by `U.3` since then.
- `A1.44`, `A1.45` → **DONE** with the fix and its guarding test named. `A1.46` → **DONE** as a recorded upstream fact with no code change, cross-referenced from `§0.8`.
- Section E Step 7 row → **STEP 7 COMPLETE 2026-08-10**, following the shape Steps 5 and 6 set: rows closed, decisions taken (`S7.1`–`S7.7`), verification commands and counts, the acceptance evidence, defects found by executing the plan rather than writing it, and the sentence **"Step 8 (`C3.5`, `C3.6` — git tools + test-runner) is unblocked."**
- Add a line to `§0.8` pointing at `A1.46`: Step 13's MCP permission question now has a measured answer for half of itself — `execute` and MCP tools are in the same uncovered category, and Rudra's gate is the mechanism for both.
- If `C11.2` or `C11.3` became reachable (both were blocked on shell since Step 4), say so explicitly rather than leaving a future session to work it out.

- [ ] **Step 7: Commit**

```bash
git add tests/test_deepagents_contract.py CLAUDE.md TODO.md
git commit -m "docs: close out Step 7 — shell execution and the permission layer

C3.1-C3.4 DONE. A1.16 closed by the diff preview. A1.44-A1.46 DONE.
U.17 DONE, U.7 WONTFIX.

Adds the contract guard that reopens U.7: it asserts create_deep_agent
still raises NotImplementedError when permissions= meets an execute-capable
backend. When that test starts failing, upstream has lifted the restriction.

CLAUDE.md section 4 corrected — execute is registered but non-functional on
a plain FilesystemBackend, and Rudra now ships a composite whose default is
LocalShellBackend. Section 6's [permissions] example rewritten with real
tool names, and [tools] is no longer listed as reserved.

Step 8 (C3.5, C3.6 — git tools and the test runner) is unblocked.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Plan self-review

Run against the spec after writing, before execution.

**Spec coverage — every section maps to a task:**

| Spec § | Task |
|---|---|
| §2 findings | 1 (ledger), 14 (contract guard) |
| §3 S7.1–S7.7 | 4 (S7.2), 9 (S7.3), 13 (S7.4), 2+3 (S7.5), 12 (S7.6), 7 (S7.7) |
| §4.1 two mechanisms | 6 (deny), 10 (ask) |
| §4.2 module layout | 2–11 |
| §4.3 precedence | 3 |
| §4.4 matching | 3 |
| §4.4a `[tools]` | 4 |
| §4.5 floor + `floor_disable` | 2, 3, 4 |
| §4.6 control plane | 3, 6 |
| §4.7 not per-agent | 12 (one gate, both agents) |
| §5.1 backend swap | 12 |
| §5.2 artifacts + threshold | 4 (config), 12 (routing) |
| §5.3 shell env | 7, 12 |
| §6.1 batched interrupts | 10 |
| §6.2 resume loop | 10, 12 |
| §6.3 prompt | 9 |
| §6.4 diff | 8 |
| §6.5 non-TTY | 13 |
| §6.6 `--plan` | 3 (deny mutations), 13 (help text) |
| §6.7 audit | 5 |
| §7 error handling | 5, 9, 10, 13 |
| §8 testing | every task |
| §10 ledger deltas | 1, 14 |
| §11 acceptance | 14 |

**One gap found, probed, and closed before execution.** §5.2 originally committed to passing `tool_token_limit_before_evict=4000`, and no task delivered it — `FilesystemMiddleware` is constructed by `create_deep_agent`, not by Rudra. Probing settled it rather than deferring it to the implementer:

```
[p for p in inspect.signature(create_deep_agent).parameters if "token" in p or "evict" in p]
  -> []
graph.py:820-826  ->  deepagent_middleware.append(FilesystemMiddleware(...))   # unconditional
```

The parameter does not exist, and supplying a configured `FilesystemMiddleware` in `middleware=` yields **two** filesystem middlewares rather than replacing the default. The only remaining route is assigning a private attribute on an upstream object, which is the `U.13` hazard class.

**Resolution:** the threshold is not configurable in Step 7. `[tools]` ships `shell` alone; the spec's §5.2 and §4.4a are corrected; the gap is logged as `A1.47` in Task 1 and belongs to `C7.x`, which owns context budgeting. Shipping `tool_output_limit_tokens` as an inert key would have reproduced exactly the defect `A1.40` records and that Step 6 refused to commit with `[permissions]`.

**Placeholder scan:** none. Every code step carries runnable code; every test step carries real assertions.

**Type consistency:** `Decision(effect, rule, source)` is constructed in `rules.py` and consumed with those three names in `audit.py`, `middleware.py`, and `approval.py`. `Rule(tool, pattern)` likewise in `rules.py`, `grants.py`, `approval.py`. `gated_arg(tool, args)` has one signature everywhere. `Gate.prompt(action_requests, console)` matches `run_with_approvals`'s call. `build_backend(cfg, project_path)` matches its test and its call site.
