"""The planner's memory prompt is Rudra's, not deepagents' (OPEN-70).

Upstream's `MEMORY_SYSTEM_PROMPT` tells the agent to persist what it learns
by calling `edit_file` -- twice in prose and twice as worked
`Tool Call: edit_file(...)` examples. The planner has no `edit_file`:
absence is the enforcement (`_tools_for_stage`). So the order arrives as
`Error: edit_file is not a valid tool`, measured on run14 and run15 and not
on run16 -- 2 of 3 runs.

OPEN-66 tried to answer it with a line in `AGENTS.md`'s body and it did not
hold, because upstream's own template tells the model that body is untrusted
data and not instructions. The correction had to move up a level.

What these tests hold:

1. the planner's stack carries a MemoryMiddleware whose prompt does NOT
   order `edit_file`;
2. it REPLACES the auto-added one rather than joining it -- a count of two
   means both prompts are live and the order is back;
3. **the memory itself still reaches the prompt.** That is the regression
   this fix could plausibly cause, and it is the one worth a test: passing
   `system_prompt=None` would also remove the order, and would silently
   remove `AGENTS.md` from every planner call with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.middleware.memory_prompt import (
    PLANNER_MEMORY_SOURCES,
    RUDRA_MEMORY_PROMPT,
    build_memory_middleware,
)


def test_the_prompt_keeps_the_slot_upstream_requires() -> None:
    """`MemoryMiddleware.__init__` raises without it, so this is the one
    thing a rewrite of the text must not lose."""
    assert "{agent_memory}" in RUDRA_MEMORY_PROMPT


def test_the_prompt_does_not_order_a_tool_the_planner_lacks() -> None:
    assert "edit_file" not in RUDRA_MEMORY_PROMPT
    assert "Tool Call:" not in RUDRA_MEMORY_PROMPT


def test_the_prompt_keeps_the_trust_guidance() -> None:
    """Kept deliberately, not left in by accident. `.rudra/AGENTS.md` is
    partly MODEL-written -- `summarise_architecture` folds each run's work
    into the Architecture Notes -- so "treat it as reference, prefer the
    user and the project" describes a real hazard rather than a generic
    caution."""
    lowered = RUDRA_MEMORY_PROMPT.lower()
    assert "reference material" in lowered
    assert "not as instructions" in lowered


def test_it_is_much_smaller_than_the_prompt_it_replaces() -> None:
    """The saving is the other half of the item. Upstream's block is ~5,072
    chars on EVERY planner call -- run16's planner made 41 -- and all of it
    addresses an agent that can write the file."""
    from deepagents.middleware.memory import MEMORY_SYSTEM_PROMPT

    assert len(RUDRA_MEMORY_PROMPT) < len(MEMORY_SYSTEM_PROMPT) / 4


def test_the_middleware_carries_rudras_prompt_and_the_shared_sources() -> None:
    middleware = build_memory_middleware(backend=object())
    assert middleware.system_prompt == RUDRA_MEMORY_PROMPT
    assert middleware.sources == list(PLANNER_MEMORY_SOURCES)


def test_add_cache_control_matches_what_upstream_would_have_set() -> None:
    """Replacing the instance must not quietly change prompt-cache behaviour:
    `graph.py:866` passes True."""
    assert build_memory_middleware(backend=object())._add_cache_control is True


def test_the_planner_stack_carries_it(tmp_path: Path) -> None:
    from rudra.agent.planner_agent import build_planner_middleware

    names = [type(m).__name__ for m in build_planner_middleware("t", backend=object())]
    assert "MemoryMiddleware" in names


def test_the_planner_builds_exactly_one_memory_middleware(monkeypatch, tmp_path: Path) -> None:
    """Two would mean both prompts are live and the `edit_file` order is back.

    Driven through the real `create_planner_agent` rather than through
    `_apply_custom_middleware` directly: the contract test already covers the
    mechanism, and what this one checks is that RUDRA reaches it.
    """
    import deepagents.graph as graph
    from rich.console import Console

    from rudra.agent.planner_agent import create_planner_agent

    captured: dict = {}
    real_create_agent = graph.create_agent

    def spy(*args, **kwargs):
        captured["middleware"] = kwargs.get("middleware")
        return real_create_agent(*args, **kwargs)

    monkeypatch.setattr(graph, "create_agent", spy)

    from deepagents.backends.filesystem import FilesystemBackend

    create_planner_agent(
        task="t",
        project_path=tmp_path,
        filesystem_backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        checkpointer=None,
        console=Console(quiet=True),
    )

    memory = [m for m in captured["middleware"] if type(m).__name__ == "MemoryMiddleware"]
    assert len(memory) == 1, f"expected 1 MemoryMiddleware, got {len(memory)}"
    assert memory[0].system_prompt == RUDRA_MEMORY_PROMPT


def test_the_memory_itself_still_reaches_the_prompt(tmp_path: Path) -> None:
    """The regression this fix could cause, and the reason `system_prompt=None`
    was not the answer: that also removes the order, and takes AGENTS.md out
    of every planner call with it."""
    from deepagents.backends.filesystem import FilesystemBackend

    agents_md = tmp_path / ".rudra" / "AGENTS.md"
    agents_md.parent.mkdir(parents=True)
    agents_md.write_text("## Architecture Notes\n\nthe parser lives in src/p.py\n", "utf-8")

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    middleware = build_memory_middleware(backend)
    rendered = middleware._format_agent_memory(
        {".rudra/AGENTS.md": agents_md.read_text("utf-8")}, RUDRA_MEMORY_PROMPT
    )

    assert "the parser lives in src/p.py" in rendered
    assert "<agent_memory>" in rendered
    assert "edit_file" not in rendered


def test_the_sources_are_one_spelling(tmp_path: Path, monkeypatch) -> None:
    """The middleware's `sources` and `create_deep_agent`'s `memory=` must
    name the same file. Two literals would be two chances to drift, and a
    drift here is silent: the middleware would render one file and upstream
    would load another."""
    from rich.console import Console

    from rudra.agent import planner_agent

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(planner_agent, "create_deep_agent", fake_create_deep_agent)
    planner_agent.create_planner_agent(
        task="t",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
    )

    assert captured["memory"] == list(PLANNER_MEMORY_SOURCES)
    memory = [m for m in captured["middleware"] if type(m).__name__ == "MemoryMiddleware"]
    assert memory and memory[0].sources == captured["memory"]


def test_upstream_is_the_thing_being_corrected() -> None:
    """Stated as a test so the fix cannot outlive its reason: if upstream
    drops the order, this fails and the override can be reconsidered."""
    from deepagents.middleware.memory import MEMORY_SYSTEM_PROMPT

    assert "edit_file" in MEMORY_SYSTEM_PROMPT


@pytest.mark.parametrize("bad", ["", "no slot", "<agent_memory></agent_memory>"])
def test_a_prompt_without_the_slot_is_rejected_upstream(bad: str) -> None:
    """Why `RUDRA_MEMORY_PROMPT` is validated by construction rather than by
    review: upstream raises, loudly, at build time."""
    from deepagents.middleware.memory import MemoryMiddleware

    with pytest.raises(ValueError, match="agent_memory"):
        MemoryMiddleware(backend=object(), sources=["a"], system_prompt=bad)
