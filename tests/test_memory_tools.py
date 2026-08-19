"""The two agent-facing memory tools.

Neither raises. A model reads what comes back and tries again, so a
rejection is a sentence saying what would be acceptable -- the REJECTED
idiom interaction_tools.py and loop/tools.py both use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.tools.memory_tools import create_memory_tools


@pytest.fixture
def tools(tmp_path: Path) -> dict:
    project = tmp_path / "demo"
    project.mkdir()
    return {t.name: t for t in create_memory_tools(project)}


def test_both_tools_are_present_under_their_exact_names(tools: dict) -> None:
    """The names are load-bearing: permissions/rules.py lists them, and a
    tool whose name does not match its rule is ungated."""
    assert set(tools) == {"remember", "search_memory"}


def test_remember_stores_something_search_can_find(tools: dict) -> None:
    tools["remember"].invoke({"content": "the user prefers uv over pip", "room": "preferences"})
    out = tools["search_memory"].invoke({"query": "package manager"})
    assert "uv" in out


def test_remember_tags_the_model_as_the_author(tools: dict) -> None:
    """The spine writes `rudra`; this tool must write `agent`, or 14c's
    `forget --added-by agent` cannot tell them apart."""
    tools["remember"].invoke({"content": "a model opinion", "room": "decisions"})
    out = tools["search_memory"].invoke({"query": "opinion"})
    assert "agent" in out


def test_an_unknown_room_is_rejected_with_the_real_ones_named(tools: dict) -> None:
    out = tools["remember"].invoke({"content": "x", "room": "thoughts"})
    assert "REJECTED" in out
    assert "decisions" in out


def test_empty_content_is_rejected_rather_than_stored(tools: dict) -> None:
    out = tools["remember"].invoke({"content": "   ", "room": "tasks"})
    assert "REJECTED" in out


def test_search_with_no_results_says_so_rather_than_returning_nothing(tools: dict) -> None:
    """An empty string reads to a model as a broken tool, and it retries."""
    out = tools["search_memory"].invoke({"query": "nothing was ever written"})
    assert out.strip()


def test_search_rejects_an_unknown_room_rather_than_silently_ignoring_it(tools: dict) -> None:
    out = tools["search_memory"].invoke({"query": "x", "room": "thoughts"})
    assert "REJECTED" in out


def test_neither_tool_raises_when_the_palace_is_broken(tmp_path: Path) -> None:
    """C8.6 reaches the tools too: a broken store degrades, and the model
    is told, rather than the whole run dying on a bookkeeping call."""
    project = tmp_path / "demo"
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("not a directory")

    tools = {t.name: t for t in create_memory_tools(project)}
    assert tools["remember"].invoke({"content": "x", "room": "tasks"})
    assert tools["search_memory"].invoke({"query": "x"})
