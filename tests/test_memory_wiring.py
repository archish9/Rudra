"""Memory reaches the agents, and only the ones that should have it.

The recall block is built per invocation inside build_agent, which is why
facts work the same way (facts/render.py's docstring): a memory recorded
during task 1 reaches task 2's coder with no plumbing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.memory.entry import MemoryEntry
from rudra.memory.store import MemoryStore
from rudra.subagents.build import _prompt_for, _tools_for
from rudra.subagents.registry import REGISTRY


class _Model:
    context_tokens = 32_000


class _Memory:
    backend = "chroma"


class _Cfg:
    memory = _Memory()

    def model_for(self, role):
        return _Model()


class _Ctx:
    """The fields build.py reads. Mirrors SubagentContext without needing
    a gate, a backend or a checkpointer."""

    def __init__(self, project_path, memory):
        self.project_path = project_path
        self.memory = memory
        self.cfg = _Cfg()
        self.gate = None
        self.console = Console()
        self.facts = None
        self.mcp = None
        self.skills_sources = None


def _spec(name: str):
    # registry.py exports REGISTRY, a dict keyed by name -- there is no
    # SUBAGENTS sequence.
    return REGISTRY[name]


@pytest.fixture
def ctx(tmp_path: Path) -> _Ctx:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="we chose uv over pip", room="decisions", added_by="rudra"))
    return _Ctx(project, store)


def test_the_coder_prompt_carries_recalled_memories(ctx: _Ctx) -> None:
    assert "uv over pip" in _prompt_for(_spec("coder"), ctx)


def test_the_reviewer_prompt_does_not(ctx: _Ctx) -> None:
    """The reviewer reads a diff and reports. Project history does not
    change what the diff says, and it would pay for the block on every
    review -- the reasoning S11b.1 used to keep skills off the reviewer."""
    assert "uv over pip" not in _prompt_for(_spec("reviewer"), ctx)


def test_a_context_without_memory_builds_a_prompt_anyway(tmp_path: Path) -> None:
    """9b-era tests build a context with no memory at all, exactly as they
    do with no facts."""
    assert _prompt_for(_spec("coder"), _Ctx(tmp_path, None))


def test_the_coder_gets_both_memory_tools(ctx: _Ctx) -> None:
    names = {t.name for t in _tools_for(_spec("coder"), ctx)}
    assert {"remember", "search_memory"} <= names


def test_the_tester_keeps_run_tests_and_gains_the_memory_tools(ctx: _Ctx) -> None:
    names = {t.name for t in _tools_for(_spec("tester"), ctx)}
    assert {"run_tests", "remember", "search_memory"} <= names


def test_the_reviewer_gets_neither(ctx: _Ctx) -> None:
    names = {t.name for t in _tools_for(_spec("reviewer"), ctx)}
    assert "remember" not in names
    assert "search_memory" not in names


def test_the_reviewer_still_has_its_diff_tool(ctx: _Ctx) -> None:
    """Absence of memory must not be absence of everything -- the spec's
    own tools are untouched."""
    assert "git_diff" in {t.name for t in _tools_for(_spec("reviewer"), ctx)}


def test_the_planner_prompt_carries_recalled_memories(tmp_path: Path) -> None:
    """C8.4 names "before planning" as a retrieval trigger by name.

    The planner decides what work exists, so what earlier runs decided is
    worth more there than anywhere -- and the spec's §4.6 said "subagents"
    while C8.4's own row said "before planning". This closes that gap.
    """
    from rudra.agent.planner_agent import build_planner_prompt

    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="we chose uv over pip", room="decisions", added_by="rudra"))

    prompt = build_planner_prompt(
        "add a feature", project, memory=store, recall_tokens=655, stage="breakdown"
    )
    assert "uv over pip" in prompt


def test_the_planner_prompt_builds_without_a_store(tmp_path: Path) -> None:
    """Memory is optional at every prompt site, as facts are."""
    from rudra.agent.planner_agent import build_planner_prompt

    assert build_planner_prompt("add a feature", tmp_path, stage="breakdown")
