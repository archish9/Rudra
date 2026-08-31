"""The suppression spec costs what it is worth, which is nothing (OPEN-58).

`_nested_subagents` supplies deepagents a spec literally named
`general-purpose` so that upstream skips auto-adding its own ungated one
(graph.py:750-751, OPEN-14). Passing the spec IS the suppression -- it is not
a delegation Rudra wants.

Nothing can reach it. `can_delegate` defaults to False on every registry spec
(spec.py:97, and `test_no_shipped_subagent_may_delegate` holds that), so
`DelegationGuardMiddleware` strips `task` from every model call; and deepagents
constructs `SubAgentMiddleware` without `system_prompt=` (graph.py:829), so the
name never reaches a prompt either. Yet `to_subagent_spec` rendered the full
prompt for it on EVERY coder, tester and reviewer invocation -- twelve ChromaDB
queries in run11, and 13,068 of that run's 23,519 recall characters, 56%,
attributed to a prompt no model ever saw. `recall_chars` exists so
RECALL_FRACTION can be revised with evidence (Step 14b); more than half of the
first evidence collected was measuring nothing.

**OPEN-54 did not cause this.** Before it closed, `recall_limit` returned None
on an undeclared window, `recall_block` returned "", and `if block:` was false
-- so the search ran and nothing was recorded. OPEN-54 made it visible and made
it cost. There is no 2026-08-30 change to build.py to look for.

The branch is on the parent's capability, never on the spec's name: a name check
is the special case the next never-invoked spec would not inherit (OPEN-17's
mistake), and CLAUDE.md 1.8 asks for capability branches because they are
testable -- which is what the delegating half of these tests does.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from rich.console import Console

from rudra.memory.entry import MemoryEntry
from rudra.subagents.build import _nested_subagents, _prompt_for, to_subagent_spec
from rudra.subagents.registry import GENERAL_PURPOSE, REGISTRY


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeModelConfig:
    context_tokens: int | None = 131072


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict

    def model_for(self, role: str):
        return self.models.get(role, self.models.get("default", FakeModelConfig()))


class CountingStore:
    """A palace that records being asked, which is the whole measurement."""

    def __init__(self) -> None:
        self.searches: list[str] = []

    def search(self, query: str, limit: int = 8) -> list[MemoryEntry]:
        self.searches.append(query)
        return [MemoryEntry(content="we chose uv over pip", room="decisions", added_by="rudra")]


class CountingUsage:
    """Only the two calls `_prompt_for` makes; a real RunUsage would do."""

    def __init__(self) -> None:
        self.recalls: list[tuple[str, int]] = []
        self.trees: list[tuple[str, int]] = []

    def record_recall(self, role: str, chars: int) -> None:
        self.recalls.append((role, chars))

    def record_tree(self, role: str, chars: int) -> None:
        self.trees.append((role, chars))


@dataclass
class FakeContext:
    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = "test"
    facts: Any = None
    skills_sources: tuple[str, ...] | None = None
    usage: Any = None
    mcp: Any = None
    memory: Any = None
    trace: Any = None


class FakeModel(BaseChatModel):
    """Subclasses BaseChatModel because Step 12b's compaction middleware
    type-checks its model (summarization.py:1663)."""

    role: str = ""

    @property
    def _llm_type(self) -> str:
        return "fake-suppression-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    import rudra.subagents.build as build

    monkeypatch.setattr(build, "build_model", lambda role, cfg=None: FakeModel(role=role))


@pytest.fixture
def context(tmp_path):
    return FakeContext(
        project_path=tmp_path,
        backend=object(),
        # None rather than a stand-in: these tests are about the prompt, and
        # a gate would only add a fake middleware to every assembled spec.
        gate=None,
        console=Console(quiet=True),
        cfg=FakeCfg(
            compat=FakeCompat(),
            tools=FakeTools(),
            models={"default": FakeModelConfig(context_tokens=131072)},
        ),
        usage=CountingUsage(),
        memory=CountingStore(),
    )


def _only(specs: list[dict]) -> dict:
    assert len(specs) == 1
    return specs[0]


def test_a_parent_that_cannot_delegate_does_not_search_the_palace(context):
    """The item, in one assertion. Twelve of these ran in run11."""
    _nested_subagents(context, can_delegate=False)

    assert context.memory.searches == []


def test_a_parent_that_cannot_delegate_records_no_recall_spend(context):
    """The cost that matters. 56% of run11's recall_chars was this role, and
    `recall_chars` is the evidence RECALL_FRACTION is revised from."""
    _nested_subagents(context, can_delegate=False)

    assert [role for role, _ in context.usage.recalls] == []


def test_a_delegating_parent_still_gets_the_rendered_prompt(context):
    """The capability branch, exercised. If a spec ever sets
    can_delegate=True, its delegate must arrive with facts and memories --
    which is why this is keyed on capability rather than on the name."""
    spec = _only(_nested_subagents(context, can_delegate=True))

    assert context.memory.searches
    assert "uv over pip" in spec["system_prompt"]
    assert spec["system_prompt"] == _prompt_for(GENERAL_PURPOSE, context)


def test_the_suppressed_prompt_says_why_it_is_a_stub(context):
    """A model reading this prompt means delegation was enabled without
    re-rendering it. The stub reports that rather than being mysterious."""
    spec = _only(_nested_subagents(context, can_delegate=False))

    assert "uv over pip" not in spec["system_prompt"]
    assert "_nested_subagents" in spec["system_prompt"]


def test_the_name_is_what_suppresses_the_auto_add(context):
    """Pinned against the constant upstream actually compares, not a literal
    of ours: graph.py:751 skips its auto-add only on this exact name."""
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    for can_delegate in (False, True):
        spec = _only(_nested_subagents(context, can_delegate=can_delegate))
        assert spec["name"] == GENERAL_PURPOSE_SUBAGENT["name"]


def test_the_description_is_identical_on_both_branches(context):
    """The one field a delegating parent reads before choosing. It rides in
    the `task` tool description (subagents.py:463), so trimming it would
    change what a delegating model sees, which is not what this is for."""
    suppressed = _only(_nested_subagents(context, can_delegate=False))
    rendered = _only(_nested_subagents(context, can_delegate=True))

    assert suppressed["description"] == rendered["description"] == GENERAL_PURPOSE.description


def test_the_suppressed_spec_still_carries_the_keys_deepagents_demands(context):
    """create_sub_agent raises without `model` or `tools` (subagents.py:357-362)
    and indexes `system_prompt` unconditionally (subagents.py:376). The spec is
    compiled EAGERLY for every registered subagent, delegated to or not
    (subagents.py:451), so a cheap prompt is the most that can be skipped."""
    spec = _only(_nested_subagents(context, can_delegate=False))

    assert isinstance(spec["system_prompt"], str)
    assert spec["model"] is not None
    # By name: `_tools_for` builds fresh tool objects per call, so identity
    # says nothing. What matters is that suppression trims the PROMPT and not
    # the tool list -- those tools are what keep the eagerly compiled agent
    # gated.
    rendered = to_subagent_spec(GENERAL_PURPOSE, context)
    assert [t.name for t in spec["tools"]] == [t.name for t in rendered["tools"]]


def test_deepagents_compiles_the_suppressed_spec(context):
    """The question OPEN-58 said to settle first, pinned as a test: upstream
    accepts a spec whose prompt is a placeholder. If a future version
    validates prompts, this fails here rather than at the first delegation."""
    from deepagents.middleware.subagents import create_sub_agent

    spec = _only(_nested_subagents(context, can_delegate=False))

    assert create_sub_agent(spec) is not None


def test_build_agent_asks_for_what_this_spec_may_do(monkeypatch, context):
    """The wiring, and the reason the branch is not hard-coded: build_agent
    reads the PARENT's capability, so enabling delegation on a spec restores
    its delegate's prompt with no second edit."""
    import rudra.subagents.build as build

    seen: list[bool] = []
    monkeypatch.setattr(
        build,
        "_nested_subagents",
        lambda ctx, *, can_delegate: seen.append(can_delegate) or [],
    )
    monkeypatch.setattr(build, "create_deep_agent", lambda **kwargs: kwargs)

    build.build_agent(REGISTRY["coder"], context)
    build.build_agent(replace(REGISTRY["coder"], can_delegate=True), context)

    assert seen == [False, True]
