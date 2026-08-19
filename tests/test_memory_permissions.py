"""Where the memory tools sit in the permission model.

A1.75, twice. `record_fact` was left out of CONTROL_PLANE_TOOLS and was
denied in every mode that mattered -- `--plan` presented a plan with no
facts, which is the one thing that flag exists to show. `remember` writes
only under .rudra/ and would land exactly there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.permissions.rules import (
    ALL_GATED_TOOLS,
    CONTROL_PLANE_TOOLS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    PermissionEngine,
)
from rudra.tools.memory_tools import create_memory_tools


def test_remember_is_control_plane() -> None:
    assert "remember" in CONTROL_PLANE_TOOLS


def test_search_memory_is_read_only() -> None:
    assert "search_memory" in READ_ONLY_TOOLS


def test_neither_is_mutating() -> None:
    """Mutating means the user's project. These write under .rudra/ only,
    and gating them would prompt for Rudra's own bookkeeping."""
    assert "remember" not in MUTATING_TOOLS
    assert "search_memory" not in MUTATING_TOOLS


def test_every_memory_tool_the_factory_makes_is_classified(tmp_path: Path) -> None:
    """The test that survives a rename. Naming the two strings above is
    not enough: a tool renamed in the factory and not here would be
    unclassified and silently denied, which is A1.75 exactly."""
    project = tmp_path / "demo"
    project.mkdir()
    for made in create_memory_tools(project):
        assert made.name in ALL_GATED_TOOLS, f"{made.name} is not classified"


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_remember_is_permitted_in_every_mode(tmp_path: Path, mode: str) -> None:
    """Including `plan`. A plan-mode run that cannot record what it learned
    is A1.75's defect with a different tool name."""
    engine = PermissionEngine(mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path)
    # Decision carries `effect`, one of allow/deny/ask -- there is no
    # `.allowed`. Control-plane tools resolve to "allow" in every mode.
    assert engine.decide("remember", {"content": "x", "room": "tasks"}).effect == "allow"


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_search_memory_is_permitted_in_every_mode(tmp_path: Path, mode: str) -> None:
    """Reads are never gated."""
    engine = PermissionEngine(mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path)
    assert engine.decide("search_memory", {"query": "x"}).effect == "allow"
