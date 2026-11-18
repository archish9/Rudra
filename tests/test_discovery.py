"""The REPL capability-discovery row-builders (OPEN-169).

Pure functions over the live registries, tested without a terminal.
"""

from __future__ import annotations

from pathlib import Path

from rudra.config import get_config
from rudra.discovery import tool_rows


def _cfg(tmp_path: Path):
    return get_config(tmp_path)


def test_tool_rows_attribute_run_tests_to_the_tester(tmp_path: Path):
    by_name = {r.name: r for r in tool_rows(tmp_path, _cfg(tmp_path))}
    assert "run_tests" in by_name
    assert "tester" in by_name["run_tests"].agents
    assert by_name["run_tests"].description


def test_tool_rows_cover_fs_and_planner_tools(tmp_path: Path):
    names = {r.name for r in tool_rows(tmp_path, _cfg(tmp_path))}
    assert {"read_file", "write_file", "execute"} <= names
    assert {"record_fact", "add_tasks"} <= names


def test_every_tool_row_has_a_description(tmp_path: Path):
    """A new tool must not slip in undescribed."""
    for row in tool_rows(tmp_path, _cfg(tmp_path)):
        assert row.description, f"{row.name} has no gloss"
        assert row.agents, f"{row.name} is attributed to no agent"


def test_coder_fs_tools_match_the_registry(tmp_path: Path):
    """tool_rows derives agents from REGISTRY, not a hand-copied list."""
    from rudra.subagents.registry import REGISTRY

    rows = {r.name: r for r in tool_rows(tmp_path, _cfg(tmp_path))}
    for t in REGISTRY["coder"].fs_tools:
        assert "coder" in rows[t].agents
