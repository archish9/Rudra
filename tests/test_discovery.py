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


def test_skill_rows_mark_systematic_debugging_applied(tmp_path: Path):
    from rudra.discovery import skill_rows

    rows = {r.name: r for r in skill_rows(_cfg(tmp_path))}
    assert "systematic-debugging" in rows
    assert rows["systematic-debugging"].enabled
    assert rows["systematic-debugging"].applied_at  # where it is inlined


def test_skill_rows_cover_all_vendored_skills(tmp_path: Path):
    from rudra.discovery import skill_rows
    from rudra.skills.registry import BUNDLES

    vendored = {name for b in BUNDLES for name in b.skill_names()}
    assert {r.name for r in skill_rows(_cfg(tmp_path))} == vendored


def test_skill_rows_have_descriptions(tmp_path: Path):
    from rudra.discovery import skill_rows

    for r in skill_rows(_cfg(tmp_path)):
        assert r.description


def test_mcp_rows_empty_when_no_config(tmp_path: Path):
    from rudra.discovery import mcp_rows

    assert mcp_rows(tmp_path, _cfg(tmp_path)) == []


def test_mcp_rows_never_leak_env(tmp_path: Path):
    import json

    from rudra.discovery import mcp_rows

    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mem": {"command": "mempalace", "args": ["mcp"], "env": {"SECRET": "xyz"}}
                }
            }
        )
    )
    rows = mcp_rows(tmp_path, _cfg(tmp_path))
    assert any(r.name == "mem" for r in rows)
    assert "xyz" not in repr(rows)
