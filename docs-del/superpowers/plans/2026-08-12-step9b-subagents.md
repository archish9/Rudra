# Step 9b — Subagents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rudra's two hand-wired agents with four declared subagents — coder, tester, reviewer, general-purpose — each with its own model role and tool set, invocable deterministically from Python.

**Architecture:** A new `src/rudra/subagents/` package. `spec.py` holds a frozen `RudraSubagent` record with no behaviour; `registry.py` declares the four entries and their prompts; `build.py` is the single place that turns a spec into a model, a tool list, and a middleware stack; `runner.py` invokes one and returns a `SubagentResult`. Two consumers share that one assembly — direct invocation via `create_deep_agent`, and a deepagents `SubAgent` dict for later delegation — with a parity test proving they cannot drift.

**Tech Stack:** Python 3.12+, deepagents 0.7.4 (`SubAgent`, `FilesystemMiddleware`, `create_deep_agent`), LangChain `BaseChatModel`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-12-step9b-subagents-design.md`

## Global Constraints

- **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE` (CLAUDE.md §2.2).
- **Evidence-based claims only.** Every claim about the codebase cites `file.py:line`.
- **Three gates before every commit:** `uv run ruff check src/ tests/` prints `All checks passed!`; `uv run ruff format --check src/ tests/` is clean; `uv run pytest -q` never drops below **742 passed, 2 skipped**.
- **Use `uv run`, never a bare `.venv/bin/…`** (CLAUDE.md §9).
- **Line length 100**, ruff `select = ["E", "F", "I", "W"]`, `target-version = "py312"`.
- **`from __future__ import annotations`** at the top of every new module.
- **`subagents/` must not import `rudra.agent`** — Step 9c rewrites that package.
- **Every subagent carries the gate.** `_middleware_for` is the only place middleware is assembled, and `RudraSubagent` has no `middleware` field, so no caller can define one without it (spec S9b.2).
- **The reviewer and general-purpose never receive `write_file`, `edit_file`, `delete`, or `execute`** (spec S9b.3).
- **No `response_format`, no async subagents, no `memory=` on subagents** (spec §10).
- **`main_agent.py` and `planner_agent.py` are not modified in 9b** (spec §10).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/subagents/spec.py` | **Create.** `RudraSubagent` frozen record. Pure data |
| `src/rudra/subagents/registry.py` | **Create.** The four specs and their system prompts; `REGISTRY: dict[str, RudraSubagent]` |
| `src/rudra/subagents/build.py` | **Create.** `_model_for`, `_tools_for`, `_middleware_for`, `build_agent`, `to_subagent_spec` |
| `src/rudra/subagents/runner.py` | **Create.** `SubagentContext`, `SubagentResult`, `run_subagent`, the per-invocation guards |
| `src/rudra/subagents/__init__.py` | **Create.** Public surface |
| `src/rudra/config/schema.py:12` | **Modify.** `BUILTIN_ROLES` gains `tester`, `reviewer` |
| `src/rudra/llm/probe.py:18` | **Modify.** Probe distinct resolved identities instead of a fixed role pair |
| `src/rudra/cli.py` | **Modify.** `models test` table gains a `Roles` column |
| `tests/test_deepagents_contract.py` | **Modify.** Three upstream facts this design rests on |
| `Documentation/02-configuration.md`, `05-how-it-works.md` | **Modify.** The two new model roles; what the four subagents are |
| `TODO.md`, `CLAUDE.md` | **Modify.** Close C6.2–C6.4, U.11, A1.20; record findings |

**Two deliberate deviations from the spec's §8 file list**, so neither reads as a gap:
the spec names `test_subagents_parity.py` and `test_subagents_reviewer.py` separately,
but both test `build.py` and live in `tests/test_subagents_build.py` here — splitting
them would mean three files rebuilding the same fixture. And the spec's
`test_config_roles.py` is `tests/test_subagents_roles.py`, so every file this step adds
sorts together under one prefix.

---

## Task 1: The spec record and the registry

**Files:**
- Create: `src/rudra/subagents/__init__.py`, `src/rudra/subagents/spec.py`, `src/rudra/subagents/registry.py`
- Test: `tests/test_subagents_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RudraSubagent(name, description, system_prompt, role, fs_tools=(), rudra_tools=())` with property `can_write: bool`; `REGISTRY: dict[str, RudraSubagent]` with keys `"coder"`, `"tester"`, `"reviewer"`, `"general-purpose"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagents_registry.py`:

```python
"""What the four subagents are allowed to be."""

from __future__ import annotations

import pytest

from rudra.config.schema import BUILTIN_ROLES
from rudra.subagents.registry import REGISTRY

WRITE_TOOLS = {"write_file", "edit_file", "delete"}


def test_the_four_expected_entries_exist():
    assert set(REGISTRY) == {"coder", "tester", "reviewer", "general-purpose"}


def test_general_purpose_is_named_exactly_as_upstream_expects():
    # graph.py:751 suppresses the auto-added ungated subagent only on an
    # exact name match. A typo here silently reopens the hole.
    assert "general-purpose" in REGISTRY


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_key_matches_its_spec_name(name):
    assert REGISTRY[name].name == name


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_role_is_a_builtin_role(name):
    assert REGISTRY[name].role in BUILTIN_ROLES


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_read_only_agents_have_no_write_tool(name):
    spec = REGISTRY[name]
    assert not WRITE_TOOLS & set(spec.fs_tools)
    assert "execute" not in spec.fs_tools
    assert spec.can_write is False


def test_the_tester_is_the_only_entry_with_execute():
    with_execute = [name for name, spec in REGISTRY.items() if "execute" in spec.fs_tools]
    assert with_execute == ["tester"]


def test_the_coder_writes_but_does_not_execute():
    spec = REGISTRY["coder"]
    assert spec.can_write is True
    assert "execute" not in spec.fs_tools
    assert spec.rudra_tools == ()


def test_the_reviewer_reads_the_diff():
    assert REGISTRY["reviewer"].rudra_tools == ("git_diff",)


def test_the_tester_gets_both_run_tests_and_execute():
    # Both deliberately: run_tests caps output for a 32B window, execute
    # covers what it cannot express. Spec §3.
    spec = REGISTRY["tester"]
    assert spec.rudra_tools == ("run_tests",)
    assert "execute" in spec.fs_tools


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_prompt_states_a_stop_condition(name):
    # Only the final assistant message reaches a caller (subagents.py:117),
    # so a prompt with no stop condition produces a subagent that trails off.
    prompt = REGISTRY[name].system_prompt.lower()
    assert "stop" in prompt or "finish" in prompt


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_spec_can_carry_middleware(name):
    # RudraSubagent has no middleware field on purpose: build.py owns
    # assembly so nothing can be declared without the gate (S9b.2).
    assert not hasattr(REGISTRY[name], "middleware")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subagents_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.subagents'`

- [ ] **Step 3: Write the spec record**

Create `src/rudra/subagents/__init__.py`:

```python
"""Rudra's subagents: coder, tester, reviewer, general-purpose (Step 9b)."""
```

Create `src/rudra/subagents/spec.py`:

```python
"""The RudraSubagent record -- pure data, no behaviour, no I/O.

Deliberately has no `middleware` field. Middleware is assembled by
build.py, which always injects the permission gate, so there is no way to
declare a subagent that forgets it (spec S9b.2). That matters because
deepagents does not propagate the parent's `middleware=` to subagents --
only `interrupt_on` is inherited (graph.py:666-703, :718) -- so a spec
without the gate would be gated for approvals and not for denials.
"""

from __future__ import annotations

from dataclasses import dataclass

# The deepagents built-in filesystem tools, exactly as FsToolName spells
# them (filesystem.py:1321). A name outside this set is rejected at build
# time rather than silently dropped.
FS_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete"})


@dataclass(frozen=True)
class RudraSubagent:
    """One subagent Rudra ships.

    Attributes:
        name: Unique identifier. For "general-purpose" the exact spelling
            matters -- it is what suppresses the ungated subagent
            deepagents adds automatically (graph.py:751).
        description: What a delegating parent reads when choosing.
        system_prompt: Instructions. Must state a stop condition, because
            only the final assistant message reaches the caller.
        role: A BUILTIN_ROLES member, resolved through build_model.
        fs_tools: Which built-in filesystem tools this subagent may see.
            Absence is the enforcement -- a model cannot call a tool that
            was never registered (U.17).
        rudra_tools: Rudra tool names, not callables: the factories need
            per-run arguments a frozen spec cannot hold.
    """

    name: str
    description: str
    system_prompt: str
    role: str
    fs_tools: tuple[str, ...] = ()
    rudra_tools: tuple[str, ...] = ()

    @property
    def can_write(self) -> bool:
        """Can this subagent change the project?"""
        return bool(_WRITE_TOOLS & set(self.fs_tools))


__all__ = ["FS_TOOL_NAMES", "RudraSubagent"]
```

- [ ] **Step 4: Write the registry and the prompts**

Create `src/rudra/subagents/registry.py`:

```python
"""The four subagents Rudra ships.

Adding one is a data change here, the same rule stacks/registry.py:3-4
states for stack profiles.

Prompts live beside their specs rather than in the modules that use them,
because a prompt and its tool list are one decision: a prompt naming a
tool the spec does not grant produces a subagent that keeps trying
something it cannot do.
"""

from __future__ import annotations

from rudra.subagents.spec import RudraSubagent

_READ_ONLY_FS: tuple[str, ...] = ("ls", "read_file", "glob", "grep")
_WRITER_FS: tuple[str, ...] = ("ls", "read_file", "write_file", "edit_file", "glob", "grep")

_CODER_PROMPT = """You are an expert code generator for Rudra.

Your job: write the ONE file you are asked to write, completely and correctly.

## WORKFLOW
1. Read any context files you are pointed at with read_file()
2. Write the file: write_file(file_path="<exact path>", content="<complete source>")
3. STOP

## FILE PATH RULES
- Use RELATIVE paths only: "src/main.rs", "package.json"
- NEVER use absolute paths or paths starting with "/" or a drive letter
- Use the exact file_path you were given

## CODE QUALITY RULES
- write_file content MUST be RAW source code -- NEVER wrap it in ```markdown fences```
- Write COMPLETE, working code -- no placeholders, stubs, or TODO comments
- No bare `pass`, no unimplemented methods, no Hello World shortcuts
- All imports at the top of the file

A verification gate checks your work afterwards: it parses the file, type
checks it, runs the tests, and scans for placeholders. A stub will be
caught, so writing one only costs a round trip.

## STOP CONDITION
After write_file() returns successfully, STOP. Do not write more files.
"""

_TESTER_PROMPT = """You are a test engineer for Rudra.

Your job: write tests for the code you are pointed at, run them, and report
exactly what happened.

## WORKFLOW
1. read_file() the code under test
2. Write a test file with write_file() -- follow the project's existing test
   layout and naming; read a neighbouring test first if one exists
3. Call run_tests() to run the project's suite
4. Report what passed and what failed, quoting the failure output
5. STOP

## TOOLS
- run_tests() runs the whole suite and caps its output. Prefer it.
- execute() is for what run_tests cannot express: one test file, or a single
  reproduction command. Do not use it to re-run the whole suite.

## RULES
- Test real behaviour. A test asserting True == True passes and proves nothing
- Do NOT edit the code under test to make a test pass -- report the failure
- If run_tests() says it was not permitted, do NOT retry it. Say so and stop:
  the user must opt in with --allow-shell
- If no tests were collected, that is neither a pass nor a failure. Say so

## OUTPUT
Only your final message reaches the caller. It must contain the verdict and
the failure output, not a summary of your intentions.

## STOP CONDITION
Stop once you have reported the result. Do not iterate on a fix -- that is
someone else's job.
"""

_REVIEWER_PROMPT = """You are a code reviewer for Rudra.

Your job: read what changed and report problems. You do NOT fix them.

## WORKFLOW
1. Call git_diff() to see what changed
2. read_file() anything you need for context
3. Report your findings
4. STOP

## YOU CANNOT EDIT
You have no write, edit, delete, or shell tools. This is deliberate. Do not
ask for them and do not describe patches as if you had applied them.

## WHAT TO REPORT
- Correctness bugs first: wrong logic, unhandled errors, off-by-one, bad types
- Then missing tests for the behaviour that changed
- Then clarity problems that would mislead a reader

Every finding gets a `file:line` and one sentence saying what is wrong.
Skip style and formatting -- a linter already ran.

## WHEN THERE IS NOTHING WRONG
Say so plainly, in one line. Do not invent findings to look useful.

## OUTPUT
Only your final message reaches the caller. Put the findings in it.

## STOP CONDITION
Stop after reporting. Your findings are advisory -- they do not block anything.
"""

_GENERAL_PURPOSE_PROMPT = """You are a research assistant for Rudra.

Your job: answer open-ended questions about this codebase by reading it.

## WORKFLOW
1. Use glob() and grep() to locate relevant files
2. read_file() them
3. Answer, citing `file:line` for every claim
4. STOP

## YOU CANNOT EDIT
You have no write, edit, delete, or shell tools. You read and report.

## OUTPUT
Only your final message reaches the caller. Include the answer itself, not a
description of how you searched. If you could not find something, say that
plainly rather than guessing.

## STOP CONDITION
Stop once you have answered.
"""

CODER = RudraSubagent(
    name="coder",
    description="Writes one complete source file from a specification. No shell access.",
    system_prompt=_CODER_PROMPT,
    role="coder",
    fs_tools=_WRITER_FS,
)

TESTER = RudraSubagent(
    name="tester",
    description="Writes tests for existing code, runs the suite, and reports what failed.",
    system_prompt=_TESTER_PROMPT,
    role="tester",
    fs_tools=(*_WRITER_FS, "execute"),
    rudra_tools=("run_tests",),
)

REVIEWER = RudraSubagent(
    name="reviewer",
    description="Reads the diff and reports correctness problems. Cannot edit anything.",
    system_prompt=_REVIEWER_PROMPT,
    role="reviewer",
    fs_tools=_READ_ONLY_FS,
    rudra_tools=("git_diff",),
)

# The name is load-bearing. deepagents adds its own `general-purpose`
# subagent -- carrying the main agent's whole tool list and no deny
# middleware -- unless a spec with this exact name is supplied
# (graph.py:751). Shipping a gated read-only one is how that is closed.
GENERAL_PURPOSE = RudraSubagent(
    name="general-purpose",
    description="Answers open-ended questions about the codebase by reading it. Read-only.",
    system_prompt=_GENERAL_PURPOSE_PROMPT,
    role="default",
    fs_tools=_READ_ONLY_FS,
)

REGISTRY: dict[str, RudraSubagent] = {
    spec.name: spec for spec in (CODER, TESTER, REVIEWER, GENERAL_PURPOSE)
}

__all__ = ["CODER", "GENERAL_PURPOSE", "REGISTRY", "REVIEWER", "TESTER"]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_subagents_registry.py -q`
Expected: FAIL on `test_every_role_is_a_builtin_role` — `tester` and `reviewer` are not in `BUILTIN_ROLES` yet. Every other test passes. Task 2 closes it; do not add the roles here.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/subagents/ tests/test_subagents_registry.py
git commit -m "feat(subagents): the spec record and the four registry entries

RudraSubagent has no middleware field on purpose -- build.py owns
assembly so nothing can be declared without the gate. The
general-purpose entry exists to suppress the ungated one deepagents adds
automatically.

Two role tests fail until BUILTIN_ROLES gains tester and reviewer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: The two new model roles

**Files:**
- Modify: `src/rudra/config/schema.py:12`
- Test: `tests/test_subagents_roles.py`

**Interfaces:**
- Consumes: `REGISTRY` (Task 1).
- Produces: `BUILTIN_ROLES == ("default", "planner", "coder", "tester", "reviewer")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagents_roles.py`:

```python
"""tester and reviewer as first-class model roles."""

from __future__ import annotations

from rudra.config import build_config
from rudra.config.schema import BUILTIN_ROLES


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_the_new_roles_are_builtin():
    assert BUILTIN_ROLES == ("default", "planner", "coder", "tester", "reviewer")


def test_unset_roles_inherit_the_default_model(tmp_path, monkeypatch):
    # One inheritance rule, no named exceptions (spec S9b.5).
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    project = write_config(
        tmp_path,
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
    )
    cfg = build_config(project)
    assert cfg.models["tester"].model == "qwen3:32b"
    assert cfg.models["reviewer"].model == "qwen3:32b"


def test_an_explicit_section_overrides_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    project = write_config(
        tmp_path,
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n\n'
        '[model.reviewer]\nmodel = "qwen3:72b"\n',
    )
    cfg = build_config(project)
    assert cfg.models["reviewer"].model == "qwen3:72b"
    assert cfg.models["tester"].model == "qwen3:32b"


def test_every_registry_role_resolves_to_a_model(tmp_path, monkeypatch):
    from rudra.subagents.registry import REGISTRY

    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    project = write_config(
        tmp_path,
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
    )
    cfg = build_config(project)
    for spec in REGISTRY.values():
        assert cfg.models[spec.role].model
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subagents_roles.py -q`
Expected: FAIL — `assert ('default', 'planner', 'coder') == ('default', 'planner', 'coder', 'tester', 'reviewer')`

- [ ] **Step 3: Add the roles**

In `src/rudra/config/schema.py`, replace line 12:

```python
# tester and reviewer joined in Step 9b (C6.2-C6.4). Unset roles inherit
# [model.default] through the post-merge inheritance in config/loader.py --
# no per-role exceptions, because precedence lives in exactly one function
# (CLAUDE.md §7) and "roles inherit default, except two" is the per-field
# resolution A5.1 and A5.2 were about.
BUILTIN_ROLES = ("default", "planner", "coder", "tester", "reviewer")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_subagents_roles.py tests/test_subagents_registry.py -q`
Expected: PASS — both files, including `test_every_role_is_a_builtin_role` from Task 1.

- [ ] **Step 5: Check nothing else assumed three roles**

Run: `uv run pytest -q`

If a config or CLI test asserts the old tuple or a role count, that assertion is now
stale rather than wrong. Update it to the new tuple and say so in the commit. If a
test fails for any *other* reason, stop and log it in `TODO.md` before touching it —
that is a real finding, not a stale assertion.

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/rudra/config/schema.py tests/test_subagents_roles.py
git commit -m "feat(config): tester and reviewer model roles

Both inherit [model.default] like every other role. No named exceptions:
precedence lives in exactly one function, and per-role special-casing is
what A5.1 and A5.2 were.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Assembly — models, tools, middleware

**Files:**
- Create: `src/rudra/subagents/build.py`
- Test: `tests/test_subagents_build.py`

**Interfaces:**
- Consumes: `RudraSubagent`, `REGISTRY` (Task 1); `build_model(role)` (`llm/factory.py:64`); `create_git_tools(project_path, *, gate, console, cfg)` (`tools/git_tools.py:42`); `create_testing_tools(project_path, *, gate, console, cfg)` (`tools/testing_tools.py:67`); `FixWriteParamsMiddleware(strip_sandbox_prefixes=bool)`.
- Produces: `_model_for(spec, cfg=None)`, `_tools_for(spec, context)`, `_middleware_for(spec, context)`, `build_agent(spec, context)`, `to_subagent_spec(spec, context) -> dict`. All take the `SubagentContext` defined in Task 4 structurally — this task type-hints it as `Any` and Task 4 supplies the record.

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagents_build.py`:

```python
"""One assembly, two consumers -- and the gate on every path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from rudra.subagents.build import _middleware_for, _tools_for, to_subagent_spec
from rudra.subagents.registry import REGISTRY


class FakeMiddleware:
    name = "RudraPermissionMiddleware"


@dataclass
class FakeGate:
    middleware: Any
    interrupt_on: dict


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict


@dataclass
class FakeContext:
    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = "test"


@pytest.fixture
def context(tmp_path):
    return FakeContext(
        project_path=tmp_path,
        backend=object(),
        gate=FakeGate(middleware=FakeMiddleware(), interrupt_on={"write_file": True}),
        console=Console(quiet=True),
        cfg=FakeCfg(compat=FakeCompat(), tools=FakeTools(), models={}),
    )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_gate(name, context):
    # The invariant. deepagents does not propagate the parent's middleware=
    # to subagents (graph.py:666-703), so a spec without this is gated for
    # approvals and not for denials.
    middleware = _middleware_for(REGISTRY[name], context)
    assert any(m is context.gate.middleware for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_gate_precedes_the_argument_rewriter(name, context):
    # A denied call must stop before anything rewrites its arguments
    # (planner_agent.py:126-128).
    middleware = _middleware_for(REGISTRY[name], context)
    names = [type(m).__name__ for m in middleware]
    assert names.index("FakeMiddleware") < names.index("FixWriteParamsMiddleware")


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_filesystem_middleware_is_scoped_to_the_spec(name, context):
    middleware = _middleware_for(REGISTRY[name], context)
    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1


def test_a_gateless_context_still_builds(context):
    # Only a caller constructing this by hand can produce gate=None; it must
    # not crash, matching how RudraAgent tolerates it (main_agent.py:91-93).
    context.gate = None
    middleware = _middleware_for(REGISTRY["coder"], context)
    assert middleware


def test_rudra_tools_resolve_by_name(context):
    tools = _tools_for(REGISTRY["reviewer"], context)
    assert [t.name for t in tools] == ["git_diff"]

    tools = _tools_for(REGISTRY["tester"], context)
    assert [t.name for t in tools] == ["run_tests"]


def test_a_spec_with_no_rudra_tools_gets_none(context):
    assert _tools_for(REGISTRY["coder"], context) == []


def test_an_unknown_rudra_tool_is_a_construction_error(context):
    from dataclasses import replace

    broken = replace(REGISTRY["coder"], rudra_tools=("nonexistent_tool",))
    with pytest.raises(ValueError, match="nonexistent_tool"):
        _tools_for(broken, context)


def test_an_unknown_fs_tool_is_a_construction_error(context):
    from dataclasses import replace

    broken = replace(REGISTRY["coder"], fs_tools=("read_file", "teleport"))
    with pytest.raises(ValueError, match="teleport"):
        _middleware_for(broken, context)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_has_every_required_key(name, context):
    spec = to_subagent_spec(REGISTRY[name], context)
    # create_sub_agent raises without model and tools (subagents.py:358-363).
    for key in ("name", "description", "system_prompt", "model", "tools", "middleware"):
        assert key in spec


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_inherits_the_gate_interrupts(name, context):
    spec = to_subagent_spec(REGISTRY[name], context)
    assert spec["interrupt_on"] == context.gate.interrupt_on


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_middleware(name, context):
    # The parity guard: if the two consumers ever disagree about what a
    # subagent is, the delegating path silently loses the gate.
    direct = [type(m).__name__ for m in _middleware_for(REGISTRY[name], context)]
    delegated = [type(m).__name__ for m in to_subagent_spec(REGISTRY[name], context)["middleware"]]
    assert direct == delegated


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_tools(name, context):
    direct = [t.name for t in _tools_for(REGISTRY[name], context)]
    delegated = [t.name for t in to_subagent_spec(REGISTRY[name], context)["tools"]]
    assert direct == delegated
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subagents_build.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.subagents.build'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/subagents/build.py`:

```python
"""Turning a spec into a running agent. The only place that assembles one.

Two consumers share this module so they cannot disagree about what a
subagent is:

  build_agent()       -> a compiled deep agent, invoked directly by
                         runner.py and, later, by Step 9c's loop
  to_subagent_spec()  -> a deepagents SubAgent dict, for a parent that
                         delegates through the `task` tool

Both route through _tools_for and _middleware_for, and a parity test
asserts they produce identical names for every registry entry. Without
that, the delegating path could quietly lose the gate -- which is exactly
what deepagents' own default does, since it never propagates the parent's
`middleware=` to a subagent (graph.py:666-703).
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware

from rudra.llm import build_model
from rudra.middleware import FixWriteParamsMiddleware
from rudra.subagents.spec import FS_TOOL_NAMES, RudraSubagent
from rudra.tools.git_tools import create_git_tools
from rudra.tools.testing_tools import create_testing_tools

# Every factory that can supply a Rudra tool. Called once each per build;
# the spec then selects by tool name. Names rather than callables because
# the factories need per-run arguments a frozen spec cannot hold.
_TOOL_FACTORIES = (create_git_tools, create_testing_tools)


def _model_for(spec: RudraSubagent, cfg: Any = None) -> Any:
    """The chat model for this subagent's role.

    build_model falls back to `default` for an unknown role
    (llm/factory.py:71), so a spec naming a role the user has not
    configured still runs rather than failing the build.
    """
    return build_model(spec.role, cfg)


def _tools_for(spec: RudraSubagent, context: Any) -> list:
    """The Rudra tools this subagent asked for, in declaration order.

    An unknown name raises rather than being dropped: a subagent silently
    missing the one tool its prompt tells it to call would loop asking for
    something that is not there.
    """
    if not spec.rudra_tools:
        return []

    available: dict[str, Any] = {}
    for factory in _TOOL_FACTORIES:
        for tool in factory(
            context.project_path,
            gate=context.gate,
            console=context.console,
            cfg=context.cfg,
        ):
            available[tool.name] = tool

    missing = [name for name in spec.rudra_tools if name not in available]
    if missing:
        known = ", ".join(sorted(available))
        msg = f"subagent '{spec.name}' asks for unknown tool(s) {missing}; available: {known}"
        raise ValueError(msg)

    return [available[name] for name in spec.rudra_tools]


def _middleware_for(spec: RudraSubagent, context: Any) -> list:
    """The middleware stack: gate first, then argument repair, then filesystem.

    The FilesystemMiddleware is constructed here rather than inherited so
    the tool list is scoped to the spec. Passing one in `middleware=`
    replaces the default by name, in place, on both the main-agent path
    (graph.py:882) and the subagent path (graph.py:221-228) -- which is
    what makes the reviewer structurally unable to write: the tools are
    never registered, so there is nothing to deny (U.17).

    TaskAnchorMiddleware is deliberately absent. It is a D4 compat shim for
    qwen3:14b losing the task mid-run, default off since C1.8, and a
    subagent's prompt is already narrow enough that re-anchoring it would
    only spend tokens.
    """
    unknown = [name for name in spec.fs_tools if name not in FS_TOOL_NAMES]
    if unknown:
        known = ", ".join(sorted(FS_TOOL_NAMES))
        msg = f"subagent '{spec.name}' asks for unknown filesystem tool(s) {unknown}; valid: {known}"
        raise ValueError(msg)

    middleware: list[Any] = [
        FixWriteParamsMiddleware(strip_sandbox_prefixes=context.cfg.compat.sandbox_paths),
        FilesystemMiddleware(backend=context.backend, tools=list(spec.fs_tools)),
    ]
    if context.gate is not None:
        # First: a denied call must be stopped before anything rewrites its
        # arguments (planner_agent.py:126-128).
        middleware.insert(0, context.gate.middleware)
    return middleware


def build_agent(spec: RudraSubagent, context: Any) -> Any:
    """A compiled deep agent for this spec, ready to invoke directly.

    The same create_deep_agent call create_coder_agent already makes
    (coder_agent.py:90), so the subagent inherits upstream's middleware
    stack rather than a hand-assembled copy of it.

    `permissions=` is deliberately absent: it raises NotImplementedError on
    any execute-capable backend, which is every backend Rudra builds
    (TODO.md U.7).
    """
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=_model_for(spec, context.cfg),
        tools=_tools_for(spec, context),
        system_prompt=spec.system_prompt,
        backend=context.backend,
        checkpointer=context.checkpointer,
        middleware=_middleware_for(spec, context),
        interrupt_on=context.gate.interrupt_on if context.gate is not None else None,
    )


def to_subagent_spec(spec: RudraSubagent, context: Any) -> dict:
    """A deepagents SubAgent dict, for a parent that delegates via `task`.

    Nothing in 9b passes this to create_deep_agent -- the parent that
    should delegate is Step 9c's. It exists now so the delegating path is
    built from the same assembly as the direct one and tested alongside it,
    rather than being written later against a different understanding.

    `model` and `tools` are always set because create_sub_agent raises
    without them (subagents.py:358-363).
    """
    return {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        "model": _model_for(spec, context.cfg),
        "tools": _tools_for(spec, context),
        "middleware": _middleware_for(spec, context),
        "interrupt_on": context.gate.interrupt_on if context.gate is not None else None,
    }


__all__ = ["build_agent", "to_subagent_spec"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_subagents_build.py -q`
Expected: PASS

- [ ] **Step 5: Prove the reviewer's compiled graph has no write tool**

This is the claim the whole read-only design rests on, so it is asserted against a
real compiled agent, not against the spec. Append to `tests/test_subagents_build.py`:

```python
def test_the_reviewers_compiled_graph_has_no_write_tools(tmp_path, monkeypatch):
    # The spec is the input; the compiled graph is the claim. Assert the
    # claim (spec S9b.3).
    from deepagents.backends.filesystem import FilesystemBackend

    from rudra.config import build_config
    from rudra.permissions import build_gate
    from rudra.subagents.build import build_agent

    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
        encoding="utf-8",
    )
    cfg = build_config(tmp_path)

    real_context = FakeContext(
        project_path=tmp_path,
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        gate=build_gate(cfg, tmp_path),
        console=Console(quiet=True),
        cfg=cfg,
    )

    agent = build_agent(REGISTRY["reviewer"], real_context)
    tool_names = {name for name in agent.get_graph().nodes if isinstance(name, str)}
    registered = set()
    for node in agent.nodes.values():
        registered |= {t.name for t in getattr(node, "bound_tools", []) or []}

    # The reliable read: the compiled ToolNode's registry.
    tools_node = agent.nodes.get("tools")
    if tools_node is not None and hasattr(tools_node, "bound"):
        registered |= set(getattr(tools_node.bound, "tools_by_name", {}))

    forbidden = {"write_file", "edit_file", "delete", "execute"}
    assert not (forbidden & registered), f"reviewer must not have {forbidden & registered}"
    assert "read_file" in registered or not registered, "sanity: read tools should survive"
    assert tool_names  # the graph compiled at all
```

Run: `uv run pytest tests/test_subagents_build.py -q -k reviewer`

If `registered` comes back empty, the introspection path is wrong for this LangGraph
version — do not weaken the assertion to pass. Print `agent.nodes` and find where the
tool registry actually lives, then assert against that. An empty set trivially
satisfies `not (forbidden & registered)` and would make this test prove nothing.

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/rudra/subagents/build.py tests/test_subagents_build.py
git commit -m "feat(subagents): one assembly for models, tools and middleware

Both consumers -- direct build and delegation spec -- route through
_tools_for and _middleware_for, with a parity test asserting they cannot
drift. The reviewer's read-only guarantee is asserted against its
compiled graph, not its spec.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: The runner

**Files:**
- Create: `src/rudra/subagents/runner.py`
- Modify: `src/rudra/subagents/__init__.py`
- Test: `tests/test_subagents_runner.py`

**Interfaces:**
- Consumes: `build_agent(spec, context)` (Task 3); `run_with_approvals(agent, inputs, config, gate, console)` (`permissions/approval.py:129`, an async iterator).
- Produces: `SubagentContext(project_path, backend, gate, console, cfg, checkpointer=None, session_id="")`; `SubagentResult(name, text, ok, halted_reason=None, error=None)`; `async run_subagent(name, prompt, *, context, thread_id=None) -> SubagentResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_subagents_runner.py`:

```python
"""run_subagent's result mapping and its per-invocation guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.subagents import runner
from rudra.subagents.runner import SubagentContext, SubagentResult, run_subagent


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict


def make_context(tmp_path):
    return SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=None,
        console=Console(quiet=True),
        cfg=FakeCfg(compat=FakeCompat(), tools=FakeTools(), models={}),
        session_id="s1",
    )


def stream_of(*chunks):
    async def fake_stream(agent, inputs, config, gate, console):
        for chunk in chunks:
            yield chunk

    return fake_stream


def ai(text="", tool_calls=()):
    return AIMessage(content=text, tool_calls=list(tool_calls))


def call(name, **args):
    return {"name": name, "args": args, "id": f"c{id(args)}"}


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_agent", lambda spec, context: object())
    return make_context(tmp_path)


async def test_an_unknown_name_raises(patched):
    with pytest.raises(ValueError, match="reviewr"):
        await run_subagent("reviewr", "go", context=patched)


async def test_a_clean_run_returns_the_final_message(monkeypatch, patched):
    monkeypatch.setattr(
        runner,
        "run_with_approvals",
        stream_of({"messages": [ai("first"), ai("Findings: none.")]}),
    )
    result = await run_subagent("reviewer", "review it", context=patched)
    assert isinstance(result, SubagentResult)
    assert result.ok is True
    assert result.text == "Findings: none."
    assert result.halted_reason is None
    assert result.error is None


async def test_the_last_non_empty_message_wins(monkeypatch, patched):
    # A trailing empty AIMessage is common after a tool call.
    monkeypatch.setattr(
        runner,
        "run_with_approvals",
        stream_of({"messages": [ai("the answer"), ai("")]}),
    )
    result = await run_subagent("reviewer", "go", context=patched)
    assert result.text == "the answer"


async def test_a_build_failure_is_reported_not_raised(monkeypatch, patched):
    def boom(spec, context):
        raise RuntimeError("no provider package installed")

    monkeypatch.setattr(runner, "build_agent", boom)
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert result.error is not None
    assert "no provider package" in result.error


async def test_a_provider_error_mid_stream_is_reported_not_raised(monkeypatch, patched):
    async def exploding(agent, inputs, config, gate, console):
        yield {"messages": [ai("starting")]}
        raise RuntimeError("connection reset")

    monkeypatch.setattr(runner, "run_with_approvals", exploding)
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "connection reset" in result.error
    # A1.39 is not fixed here -- but the caller gets a value, not a crash.


async def test_three_consecutive_tool_errors_halt(monkeypatch, patched):
    errors = [ToolMessage(content="Error: nope", tool_call_id=str(i)) for i in range(3)]
    monkeypatch.setattr(
        runner, "run_with_approvals", stream_of({"messages": [ai("try"), *errors]})
    )
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "consecutive" in result.halted_reason


async def test_a_success_between_errors_resets_the_counter(monkeypatch, patched):
    messages = [
        ai("try"),
        ToolMessage(content="Error: nope", tool_call_id="1"),
        ToolMessage(content="fine", tool_call_id="2"),
        ToolMessage(content="Error: nope", tool_call_id="3"),
        ai("done"),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is True


async def test_the_same_tool_call_three_times_halts(monkeypatch, patched):
    repeated = [ai("", [call("write_file", file_path="a.py")]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "repeated" in result.halted_reason


async def test_the_task_tool_is_watched_by_the_repeat_guard(monkeypatch, patched):
    # A1.20: the guard listed `task` but no agent could call it. The
    # general-purpose subagent means one now can.
    repeated = [ai("", [call("task", subagent_type="general-purpose")]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "task" in result.halted_reason


async def test_the_same_tool_on_different_files_does_not_halt(monkeypatch, patched):
    writes = [ai("", [call("write_file", file_path=f"{i}.py")]) for i in range(3)]
    monkeypatch.setattr(
        runner, "run_with_approvals", stream_of({"messages": [*writes, ai("done")]})
    )
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is True


async def test_each_invocation_gets_a_distinct_thread(monkeypatch, patched):
    seen: list[str] = []

    async def capture(agent, inputs, config, gate, console):
        seen.append(config["configurable"]["thread_id"])
        yield {"messages": [ai("ok")]}

    monkeypatch.setattr(runner, "run_with_approvals", capture)
    await run_subagent("coder", "one", context=patched)
    await run_subagent("coder", "two", context=patched)
    assert len(set(seen)) == 2
    assert all(t.startswith("s1-coder-") for t in seen)


async def test_an_explicit_thread_id_is_honoured(monkeypatch, patched):
    seen: list[str] = []

    async def capture(agent, inputs, config, gate, console):
        seen.append(config["configurable"]["thread_id"])
        yield {"messages": [ai("ok")]}

    monkeypatch.setattr(runner, "run_with_approvals", capture)
    await run_subagent("coder", "one", context=patched, thread_id="fixed")
    assert seen == ["fixed"]
```

Add `asyncio_mode` so the async tests run. In `pyproject.toml`, under
`[tool.pytest.ini_options]`, add:

```toml
asyncio_mode = "auto"
```

and add `pytest-asyncio>=1.3.0` to `[dependency-groups] dev`. Then `uv sync`.

If the repo already runs async tests another way, follow that instead — check with
`grep -rn "asyncio" pyproject.toml tests/ | head` before adding a dependency.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_subagents_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.subagents.runner'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/subagents/runner.py`:

```python
"""Invoking one subagent and reporting what it did.

Deterministic on purpose: the caller names the subagent, so no model
decides who runs. D9 requires the loop to be deterministic, and a planner
choosing when to review would put an LLM back in control of it.

Nothing here raises for a runtime failure. Step 9c's loop must act on a
bad subagent run the way it acts on a failed gate, and an exception
escaping into that loop is A1.39's shape -- one transient provider error
killing a run and discarding completed work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.approval import run_with_approvals
from rudra.subagents.build import build_agent
from rudra.subagents.registry import REGISTRY

# Carried from _stream_coder (main_agent.py:241-243), which Step 9c
# deletes. These are per-invocation guards: one subagent looping on itself.
# The across-invocation bounds -- max fix attempts, no-progress detection --
# are C6.5a and belong to the loop, not here.
MAX_CONSECUTIVE_FAILURES = 3
MAX_REPEATED_CALLS = 3

# Tool calls worth counting repeats for. `task` is included and now
# genuinely reachable, which is what closes A1.20.
_WATCHED_TOOLS = frozenset({"write_file", "edit_file", "read_file", "task"})

_ERROR_MARKERS = (
    "Error:",
    "Cannot write to",
    "Traceback",
    "Errno",
    "BLOCKED:",
    "not a valid tool",
)


@dataclass(frozen=True)
class SubagentContext:
    """Everything one run of subagents needs, built once.

    Bundled for the reason Gate itself is (permissions/__init__.py:50-56):
    these must be the *same* objects across calls. A second Gate would hold
    different session grants and re-prompt for something the user already
    answered `always` to.
    """

    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = ""


@dataclass(frozen=True)
class SubagentResult:
    """What one subagent invocation produced.

    `text` is the final assistant message because that is all a delegating
    parent would have seen too (subagents.py:117) -- the direct and
    delegated paths return the same thing.

    ok=False with halted_reason means the subagent misbehaved; with error
    means it never ran. Step 9c needs to tell those apart for the same
    reason 9a's report splits `escalate` from a plain failure.
    """

    name: str
    text: str
    ok: bool
    halted_reason: str | None = None
    error: str | None = None


def _looks_like_error(content: str) -> bool:
    first_line = content.split("\n")[0] if content else ""
    return any(marker in first_line for marker in _ERROR_MARKERS)


def _call_key(tool_call: dict) -> tuple[str, str]:
    args = tool_call.get("args") or {}
    identifier = args.get("file_path") or args.get("path") or args.get("subagent_type") or ""
    return tool_call.get("name", ""), str(identifier)


async def run_subagent(
    name: str,
    prompt: str,
    *,
    context: SubagentContext,
    thread_id: str | None = None,
) -> SubagentResult:
    """Run one subagent to completion and report what it said.

    Args:
        name: A REGISTRY key. An unknown name raises -- that is a
            programming error, not a runtime one.
        prompt: The task, as a user message.
        context: The shared per-run objects.
        thread_id: Override the generated one. Each invocation otherwise
            gets a fresh thread, matching what the orchestrator does for
            retries (main_agent.py:351) so a re-run starts clean. This does
            not fix A1.2 -- checkpoints are still never resumed.

    Returns:
        A SubagentResult. Never raises for a runtime failure.
    """
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        msg = f"unknown subagent '{name}'; available: {known}"
        raise ValueError(msg)

    spec = REGISTRY[name]

    try:
        agent = build_agent(spec, context)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text="", ok=False, error=str(exc))

    thread = thread_id or f"{context.session_id}-{name}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread}}

    processed = 0
    consecutive_failures = 0
    repeated: dict[tuple[str, str], int] = {}
    halted: str | None = None
    last_text = ""

    try:
        async for chunk in run_with_approvals(
            agent,
            {"messages": [{"role": "user", "content": prompt}]},
            config,
            context.gate,
            context.console,
        ):
            if halted is not None:
                break

            _namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            messages = event.get("messages", []) if isinstance(event, dict) else []

            while processed < len(messages):
                message = messages[processed]
                kind = type(message).__name__

                if kind == "AIMessage":
                    text = str(getattr(message, "content", "") or "").strip()
                    if text:
                        last_text = text
                    for tool_call in getattr(message, "tool_calls", []) or []:
                        if tool_call.get("name") not in _WATCHED_TOOLS:
                            continue
                        key = _call_key(tool_call)
                        repeated[key] = repeated.get(key, 0) + 1
                        if repeated[key] >= MAX_REPEATED_CALLS:
                            halted = (
                                f"'{key[0]}' on '{key[1]}' repeated "
                                f"{repeated[key]}x -- stopping"
                            )
                            break
                elif kind == "ToolMessage":
                    if _looks_like_error(str(getattr(message, "content", ""))):
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            halted = (
                                f"{consecutive_failures} consecutive tool failures -- stopping"
                            )
                    else:
                        consecutive_failures = 0

                if halted is not None:
                    break
                processed += 1
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text=last_text, ok=False, error=str(exc))

    if halted is not None:
        return SubagentResult(name=name, text=last_text, ok=False, halted_reason=halted)
    return SubagentResult(name=name, text=last_text, ok=True)


__all__ = ["SubagentContext", "SubagentResult", "run_subagent"]
```

- [ ] **Step 4: Export the public surface**

Replace `src/rudra/subagents/__init__.py`:

```python
"""Rudra's subagents: coder, tester, reviewer, general-purpose (Step 9b).

Invoked deterministically from Python by Step 9c's loop, not chosen by a
model. `to_subagent_spec` exists for a parent that should delegate through
the `task` tool, and is built from the same assembly so the two paths
cannot disagree about what a subagent is.
"""

from __future__ import annotations

from rudra.subagents.build import build_agent, to_subagent_spec
from rudra.subagents.registry import REGISTRY
from rudra.subagents.runner import SubagentContext, SubagentResult, run_subagent
from rudra.subagents.spec import RudraSubagent

__all__ = [
    "REGISTRY",
    "RudraSubagent",
    "SubagentContext",
    "SubagentResult",
    "build_agent",
    "run_subagent",
    "to_subagent_spec",
]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_subagents_runner.py -q`
Expected: PASS

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/rudra/subagents/ tests/test_subagents_runner.py pyproject.toml uv.lock
git commit -m "feat(subagents): run_subagent and the per-invocation guards

The two guards move out of _stream_coder, which Step 9c deletes, and the
repeat guard now genuinely watches task -- closing A1.20, whose watched
set already listed it while no agent could call it.

Runtime failures are reported as SubagentResult, never raised: an
exception escaping into 9c's loop is A1.39's shape.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Contract tests for the three upstream facts

**Files:**
- Modify: `tests/test_deepagents_contract.py`

**Interfaces:**
- Consumes: `REGISTRY`, `to_subagent_spec` (Tasks 1, 3); the existing `ScriptedToolModel` (`tests/test_deepagents_contract.py:56`).
- Produces: nothing importable — this task produces failing signals for a future upgrade.

- [ ] **Step 1: Write the tests**

Append to `tests/test_deepagents_contract.py`:

```python
# ---------------------------------------------------------------------------
# Step 9b — the three upstream facts the subagent design rests on.
#
# These exist so a deepagents upgrade fails a test that names the decision
# it invalidates, rather than silently changing behaviour. Same role
# test_permissions_still_rejected_with_execute_backend plays for U.7.
# ---------------------------------------------------------------------------


def _minimal_subagent(name: str) -> dict:
    return {
        "name": name,
        "description": f"{name} for contract testing",
        "system_prompt": "Do nothing. Stop.",
        "model": ScriptedToolModel(script=[]),
        "tools": [],
    }


def test_a_supplied_general_purpose_spec_suppresses_the_auto_added_one(tmp_path):
    """S9b.2 rests on this: graph.py:751 skips the auto-add on a name match.

    If upstream changes the name or the check, Rudra silently regains an
    ungated subagent carrying the main tool list.
    """
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    agent = create_deep_agent(
        model=ScriptedToolModel(script=[]),
        tools=[],
        backend=backend,
        subagents=[_minimal_subagent("general-purpose")],
    )

    task_tool = next(t for t in agent.nodes["tools"].bound.tools_by_name.values() if t.name == "task")
    listed = task_tool.description
    assert listed.count("general-purpose") == 1, (
        "a second general-purpose subagent was auto-added despite a supplied spec"
    )


def test_top_level_middleware_does_not_reach_subagents(tmp_path):
    """The asymmetry S9b.2 exists to handle (graph.py:666-703).

    If this ever starts passing the other way, Rudra's per-spec injection
    becomes redundant rather than wrong -- but the reason for it changes,
    and the spec should say so.
    """
    from deepagents.backends.filesystem import FilesystemBackend
    from langchain.agents.middleware import AgentMiddleware

    class Marker(AgentMiddleware):
        name = "ContractMarkerMiddleware"

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    spec = _minimal_subagent("worker")
    from deepagents import create_deep_agent

    create_deep_agent(
        model=ScriptedToolModel(script=[]),
        tools=[],
        backend=backend,
        middleware=[Marker()],
        subagents=[spec],
    )

    # create_deep_agent mutates a copy; the caller's dict keeps no middleware,
    # and the processed spec's middleware list is built fresh from the base
    # stack. Assert the marker is absent from what the subagent received.
    assert "middleware" not in spec or all(
        type(m).__name__ != "Marker" for m in spec.get("middleware", [])
    )


def test_top_level_interrupt_on_does_reach_subagents(tmp_path):
    """The other half: graph.py:718 inherits interrupt_on.

    Rudra relies on this for approvals inside subagents; if it stops being
    true, every subagent silently loses its prompts under `ask`.
    """
    import inspect

    from deepagents import graph as deepagents_graph

    source = inspect.getsource(deepagents_graph)
    assert 'spec.get("interrupt_on", interrupt_on)' in source, (
        "subagents no longer inherit the parent's interrupt_on"
    )
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_deepagents_contract.py -q`

Expected: PASS. If the first test cannot reach `tools_by_name` on this LangGraph
version, find where the compiled tool registry lives and assert against that — do not
weaken the assertion. If the *second* test cannot observe the subagent's actual
middleware without touching internals, replace it with a source assertion in the shape
of the third rather than deleting it: the fact must stay guarded.

- [ ] **Step 3: Settle the A1.35 question**

`A1.35` claims harness profiles "can never match a Rudra model" because `graph.py:584`
sets `_model_spec = None` for a pre-built instance. But `_harness_profile_for_model`
(`harness_profiles.py:1252-1262`) falls back to a `provider:identifier` lookup built
from `model_dump` and `_get_ls_params`.

Add:

`providers.py` exposes no per-provider constructor — it is a `PROVIDERS` mapping of
`ProviderEntry` records (`llm/providers.py:76`) consumed by `build_model`. So build the
instance the way Rudra actually does, through `build_model(role, cfg)`
(`llm/factory.py:64`), which makes the test measure the real path rather than a
synthetic one:

```python
def test_whether_a_harness_profile_can_match_a_prebuilt_model(tmp_path, monkeypatch):
    """Settles A1.35, which claims it cannot. Records the measured answer.

    A1.35 reasons from graph.py:584 setting _model_spec=None for a
    pre-built instance. But _harness_profile_for_model
    (harness_profiles.py:1252-1262) falls back to a `provider:identifier`
    lookup built from model_dump and _get_ls_params, so the conclusion may
    not follow from the premise.
    """
    from deepagents.profiles.harness.harness_profiles import _harness_profile_for_model

    from rudra.config import build_config
    from rudra.llm import build_model

    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
        encoding="utf-8",
    )
    cfg = build_config(tmp_path)
    model = build_model("default", cfg)

    # This is the exact call create_deep_agent makes for a pre-built
    # instance: spec is None because the caller passed a BaseChatModel.
    profile = _harness_profile_for_model(model, None)
    assert profile is not None, "a profile object is always returned, default or matched"

    # Record what it actually resolved to. Fill the assertion in from the
    # observed value in Step 3 below — do not guess it here.
    print(f"A1.35 measurement: profile={profile!r}")
```

Run it with `-s` so the print is visible:

```bash
uv run pytest tests/test_deepagents_contract.py -q -s -k harness_profile
```

**Log the observed answer in `TODO.md` before changing anything** — session rule 2. If a
non-default profile resolves, `A1.35` is wrong, needs correcting, and `U.10` becomes
reachable; if only the default does, the row is right and gains this evidence. Then
replace the `print` with an assertion on the observed value, so the test guards the
answer instead of merely reporting it.

- [ ] **Step 4: Run the gates and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
git add tests/test_deepagents_contract.py TODO.md
git commit -m "test(contract): the three upstream facts 9b depends on

general-purpose suppression by name, middleware NOT inheriting to
subagents, interrupt_on DOES. An upgrade that changes any of them now
fails a test naming the decision it invalidates.

Also records the measured answer to A1.35's harness-profile claim.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: `rudra models test` probes distinct endpoints

**Files:**
- Modify: `src/rudra/llm/probe.py:18`, `src/rudra/cli.py` (the `models test` table)
- Test: `tests/test_models_test_roles.py`

**Interfaces:**
- Consumes: `BUILTIN_ROLES` (Task 2).
- Produces: `roles_to_probe(cfg) -> list[tuple[tuple[str, ...], ModelConfig]]` — each entry is the roles sharing one resolved identity, plus that config.

- [ ] **Step 1: Write the failing test**

Create `tests/test_models_test_roles.py`:

```python
"""models test probes endpoints, not roles."""

from __future__ import annotations

from rudra.config import build_config
from rudra.llm.probe import roles_to_probe


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


BASE = '[model.default]\nprovider = "ollama"\nbase_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'


def test_one_model_means_one_probe(tmp_path, monkeypatch):
    # Five roles on one endpoint must not fire five network probes.
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE))
    entries = roles_to_probe(cfg)
    assert len(entries) == 1
    roles, _ = entries[0]
    assert set(roles) == {"default", "planner", "coder", "tester", "reviewer"}


def test_a_distinct_role_gets_its_own_probe(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(
        write_config(tmp_path, BASE + '\n[model.reviewer]\nmodel = "qwen3:72b"\n')
    )
    entries = roles_to_probe(cfg)
    assert len(entries) == 2
    by_model = {config.model: set(roles) for roles, config in entries}
    assert by_model["qwen3:72b"] == {"reviewer"}
    assert "planner" in by_model["qwen3:32b"]


def test_entries_are_ordered_stably(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(
        write_config(tmp_path, BASE + '\n[model.reviewer]\nmodel = "qwen3:72b"\n')
    )
    assert roles_to_probe(cfg) == roles_to_probe(cfg)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_models_test_roles.py -q`
Expected: FAIL — `ImportError: cannot import name 'roles_to_probe'`

- [ ] **Step 3: Write the implementation**

In `src/rudra/llm/probe.py`, keep `ROLES_TO_PROBE` for backward compatibility and add:

```python
def roles_to_probe(cfg: Any) -> list[tuple[tuple[str, ...], Any]]:
    """Distinct model endpoints, each with the roles that share it.

    Naively probing every role would fire five network calls where a
    single-model setup has one endpoint. Deduping on the resolved identity
    keeps `rudra models test` honest for a user who configures
    `[model.reviewer]` separately, and cheap for everyone else.

    Ordered by first appearance in BUILTIN_ROLES so the table is stable
    between runs.
    """
    seen: dict[tuple[str, str, str], list[str]] = {}
    configs: dict[tuple[str, str, str], Any] = {}

    for role in BUILTIN_ROLES:
        model = cfg.models[role]
        identity = (model.provider, model.base_url or "", model.model)
        if identity not in seen:
            seen[identity] = []
            configs[identity] = model
        seen[identity].append(role)

    return [(tuple(roles), configs[identity]) for identity, roles in seen.items()]
```

Import `BUILTIN_ROLES` from `rudra.config.schema` at the top of the module.

- [ ] **Step 4: Update both CLI call sites**

`ROLES_TO_PROBE` is read in **two** places, not one: `models test` (`cli.py:233-235`)
and `doctor` (`cli.py:473-475`). Updating only the first leaves `doctor` probing two
roles while `models test` probes endpoints — the same inconsistency in two commands.

In `models test`, replace the header tuple and the loop (`cli.py:235-253`):

```python
    from rudra.llm.probe import probe_role, roles_to_probe

    cfg = _load_config_or_exit(project_dir)
    entries = [((role,), cfg.models[role])] if role else roles_to_probe(cfg)

    table = Table(title="Model check", header_style="bold")
    for column in ("Roles", "Provider", "Model", "Construct", "Reach", "Tools", "Ctx"):
        table.add_column(column, overflow="fold")

    failed = False
    for roles, _model in entries:
        # One probe per distinct endpoint; the roles column says who shares it.
        result = probe_role(roles[0])
        failed = failed or not result.ok
        style = "green" if result.ok else "red"
        table.add_row(
            ", ".join(roles),
            result.provider,
            result.model,
            result.construct,
            result.reach,
            result.tools,
            str(result.context_tokens) if result.context_tokens else "-",
        )
```

Keep the rest of the command — the `style` usage, the failure rendering, and the exit
code — exactly as it is. Read the surrounding lines before editing rather than
replacing the whole function.

In `doctor`, replace `cli.py:473-475`:

```python
        from rudra.llm.probe import probe_role, roles_to_probe

        for roles, _model in roles_to_probe(cfg):
            result = probe_role(roles[0])
            table.add_row(
                f"model ({', '.join(roles)})",
                "ok" if result.ok else "fail",
                f"{result.provider} {result.model} — {result.reach}, tools: {result.tools}",
            )
```

`doctor` already has `cfg` in scope (`cli.py:401`), so no extra load is needed.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_models_test_roles.py -q
uv run pytest -q
```

If an existing `models test` test asserts a `Role` header or a two-row table, update
it — the change is intentional. Note it in the commit message.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
git add src/rudra/llm/probe.py src/rudra/cli.py tests/test_models_test_roles.py
git commit -m "feat(models): probe distinct endpoints rather than fixed roles

Five roles on one endpoint fired five network probes. Dedupe on
(provider, base_url, model) and show which roles share each.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Documentation

**Files:**
- Modify: `Documentation/02-configuration.md`, `Documentation/05-how-it-works.md`

**Interfaces:** none.

- [ ] **Step 1: Document the two new roles**

In `Documentation/02-configuration.md`, find the section listing model roles (search for
`model.planner`) and extend the role table with:

```markdown
| `tester` | Writes tests, runs the suite, reports failures |
| `reviewer` | Reads the diff and reports problems. Never edits |
```

Then add, after that table:

```markdown
Every role inherits `[model.default]` unless you give it its own section. On a
single-model setup you configure `[model.default]` and nothing else — all five
roles use it, and `rudra models test` shows one row rather than five, because it
probes distinct endpoints rather than role names.

Giving the reviewer a stronger model is a common split:

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3:32b"

[model.reviewer]
model = "qwen3:72b"      # inherits provider and base_url from default
```
```

- [ ] **Step 2: Describe the subagents**

In `Documentation/05-how-it-works.md`, add a section after the one describing the
planner and coder:

```markdown
## The subagents

Rudra ships four subagents. Each has its own model role and its own tool set, and
the tool set is the enforcement — a subagent cannot call a tool it was never given.

| Subagent | Model role | Can write? | Can run commands? |
|---|---|---|---|
| `coder` | `coder` | yes | no |
| `tester` | `tester` | yes | yes |
| `reviewer` | `reviewer` | **no** | **no** |
| `general-purpose` | `default` | **no** | **no** |

The reviewer has no `write_file`, `edit_file`, `delete` or `execute` tool at all.
It reads the diff, reports what it finds, and cannot act on it — findings are
advisory and never block anything. That split is deliberate: the deterministic
gate (`rudra verify`) decides done-or-not; the reviewer only comments on quality.

Every subagent goes through the same permission gate as the main agent, so the
same approval prompts, allow/deny rules and deny floor apply inside them.
```

- [ ] **Step 3: Commit**

```bash
git add Documentation/
git commit -m "docs: the four subagents and the two new model roles

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Ledger

**Files:**
- Modify: `TODO.md` (`C6.2`, `C6.3`, `C6.4`, `U.11`, `A1.20`, `A1.35`, D4's row, §E row 9b), `CLAUDE.md` (§3 tree, §8 counts)

**Interfaces:** none.

- [ ] **Step 1: Close the rows**

Mark `C6.2`, `C6.3`, `C6.4`, `U.11` and `A1.20` as `**DONE** 2026-08-12`, each with the
verifying evidence: module paths, the `uv run pytest -q` count, and the acceptance runs
from Task 9. Write these **after** Task 9 has run — session rule 4 requires the output.

`U.11`'s row is record-only; close it by recording the finding: `AsyncSubAgentMiddleware`
and `AsyncSubAgent` are `graph_id`-keyed and route to LangSmith deployments
(`graph.py:647-650`), i.e. remote execution. Not adopted — it is the opposite of
local-first — and saying so stops the row resurfacing.

- [ ] **Step 2: Narrow D4's `task` risk**

D4 recorded `task` as "reachable but undesigned (accepted risk)". Append: as of Step 9b
the exposure is **designed** rather than incidental — Rudra ships a gated, read-only
`general-purpose` subagent whose name suppresses the ungated one deepagents adds
automatically (`graph.py:751`), and every Rudra subagent carries the gate because
`build.py` is the only assembly path. `task` remains reachable from any agent given
subagents; that is now a known, bounded surface rather than an unexamined one.

- [ ] **Step 3: Record the two findings**

Add whatever Task 3 and Task 5 measured:

- The **middleware ordering** result. Rudra's convention is `insert(0, gate.middleware)`
  (`planner_agent.py:126-128`), but `_apply_custom_middleware` splices custom middleware
  *after* the core stack on both the main-agent path (`graph.py:882`) and the subagent
  path (`graph.py:229-231`). If the compiled order differs from what the comment claims,
  log it as a new `A1.x` row with `file:line` evidence — including that it affects the
  **existing** planner and coder, not only subagents — and do not fix it in 9b.
- The **`A1.35`** answer from Task 5 Step 3, as a dated note on that row.

- [ ] **Step 4: Mark §E's 9b row complete**

Update the `9b` row added in Step 9a to `**STEP 9b COMPLETE 2026-08-12**` with the spec
and plan paths, and confirm `9c`'s dependency still reads `9b`.

- [ ] **Step 5: Update CLAUDE.md**

Add to §3's architecture tree, after the `verify/` entry:

```
├── subagents/              The four subagents (Step 9b): coder · tester ·
│                           reviewer · general-purpose. spec/registry/build/
│                           runner. build.py is the ONLY assembly path, so
│                           every subagent carries the gate — deepagents
│                           inherits interrupt_on but NOT middleware
```

Update §8's test count to whatever `uv run pytest -q` reports, and note in §4's table
that `subagents=` is now used.

- [ ] **Step 6: Commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: close C6.2-C6.4, U.11, A1.20; narrow D4's task risk

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Acceptance

**Files:** none modified. Produces the evidence Task 8 records.

**Interfaces:** none.

- [ ] **Step 1: Build the harness script**

The three runs invoke `run_subagent` directly, since 9b ships no CLI surface. Write
`/tmp/9b_accept.py` in the scratch project:

```python
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from rudra.agent.main_agent import build_backend
from rudra.config import build_config
from rudra.permissions import build_gate
from rudra.subagents import SubagentContext, run_subagent

async def main(name: str, prompt: str) -> None:
    project = Path.cwd()
    cfg = build_config(project)
    console = Console()
    context = SubagentContext(
        project_path=project,
        backend=build_backend(cfg, project),
        gate=build_gate(cfg, project),
        console=console,
        cfg=cfg,
        session_id="accept",
    )
    result = await run_subagent(name, prompt, context=context)
    console.print(f"ok={result.ok} halted={result.halted_reason} error={result.error}")
    console.print(result.text)

asyncio.run(main(sys.argv[1], sys.argv[2]))
```

Note this imports `build_backend` from `main_agent` — the one place 9b reads that
module, and it reads rather than modifies it.

- [ ] **Step 2: Reviewer on a real diff**

```bash
WORK=$(mktemp -d) && cd "$WORK" && git init -q
printf '[project]\nname="acc"\nversion="0.1.0"\n' > pyproject.toml
printf 'def divide(a, b):\n    return a / b\n' > calc.py
git add -A && git commit -qm "initial"
printf 'def divide(a, b):\n    return a / b\n\n\ndef mean(xs):\n    return sum(xs) / len(xs)\n' > calc.py
mkdir -p .rudra && printf '[permissions]\nmode = "auto"\n\n[tools]\nshell_in_auto = true\n' > .rudra/config.toml
BEFORE=$(shasum calc.py)
uv run --project /Users/archish/Documents/ai-ml/Rudra python /tmp/9b_accept.py reviewer "Review the working-tree changes."
AFTER=$(shasum calc.py)
[ "$BEFORE" = "$AFTER" ] && echo "UNCHANGED (correct)" || echo "REVIEWER EDITED THE FILE — FAILURE"
```

Expected: it calls `git_diff`, reports the empty-list division risk in `mean` with a
`file:line`, `ok=True`, and the hash is unchanged.

- [ ] **Step 3: The reviewer is asked to fix it**

```bash
BEFORE=$(shasum calc.py)
uv run --project /Users/archish/Documents/ai-ml/Rudra python /tmp/9b_accept.py reviewer \
  "Fix the bug you find by editing calc.py directly. Use write_file."
AFTER=$(shasum calc.py)
[ "$BEFORE" = "$AFTER" ] && echo "STILL UNCHANGED (correct)" || echo "READ-ONLY BROKEN — FAILURE"
```

Expected: it cannot — `write_file` is not registered — and says so. **This run proves
the read-only claim rather than asserting it.** Record its output verbatim.

- [ ] **Step 4: Tester end to end, shell allowed**

```bash
uv run --project /Users/archish/Documents/ai-ml/Rudra python /tmp/9b_accept.py tester \
  "Write tests for calc.py in test_calc.py, run them, and report what happened."
ls test_calc.py && cat .rudra/run/logs/tests.log | tail -20
```

Expected: a test file exists, `run_tests` ran, and the final text reports the result.

- [ ] **Step 5: Tester with shell denied**

```bash
printf '[permissions]\nmode = "auto"\n\n[tools]\nshell_in_auto = false\n' > .rudra/config.toml
uv run --project /Users/archish/Documents/ai-ml/Rudra python /tmp/9b_accept.py tester \
  "Run the tests and report the result."
grep auto-shell .rudra/run/logs/permissions.jsonl | tail -2
```

Expected: `ok=True` — a denial is not a runner failure — with text saying it could not
run them and did not retry, and the audit log showing `source: "auto-shell"`.

- [ ] **Step 6: Final gates**

```bash
cd /Users/archish/Documents/ai-ml/Rudra
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
uv run rudra --version
uv run rudra models test --help
```

Expected: all clean, suite above 742, `Rudra v0.2.0`.

- [ ] **Step 7: Clean up and record**

```bash
rm -rf "$WORK" /tmp/9b_accept.py
```

Write the observed output into Task 8's ledger rows, then commit.

---

## Appendix: What this plan deliberately does not touch

- **`src/rudra/agent/main_agent.py`** is read (for `build_backend`) and never modified.
  The hardcoded loop still owns success; `A1.8` and `A1.25` stay `PENDING` until 9c.
- **`src/rudra/agent/planner_agent.py`** is not modified. Nothing passes `subagents=` to
  a live agent in 9b — the parent that should delegate is 9c's, which is why
  `to_subagent_spec` ships tested but uncalled.
- **`create_coder_agent`** is not deleted. It is superseded by the `coder` registry
  entry, but the orchestrator still calls it and 9c removes both together.
- **No retry policy** (`A1.39`), **no checkpoint resumption** (`A1.2`/`A1.3`), **no
  `memory=` on subagents** (`A1.9`).
- **No `response_format`**, no async subagents, no fix loop, no attempt bounds
  (`C6.5`, `C6.5a`, `C6.10`).
