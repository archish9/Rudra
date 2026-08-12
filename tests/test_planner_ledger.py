"""The planner declares work; it cannot declare anything finished."""

from __future__ import annotations

from rudra.agent.planner_agent import build_planner_prompt
from rudra.loop.ledger import Ledger
from rudra.loop.tools import create_ledger_tools


def test_the_prompt_names_no_rudra_paths(tmp_path):
    # The ledger is reached through tools, so the prompt has no path to
    # drift from (CLAUDE.md §3).
    prompt = build_planner_prompt("build a parser", tmp_path, "")
    assert "PLAN.md" not in prompt
    assert "current_task.md" not in prompt
    assert ".rudra" not in prompt


def test_the_prompt_asks_for_work_not_filenames(tmp_path):
    prompt = build_planner_prompt("build a parser", tmp_path, "").lower()
    assert "add_tasks" in prompt
    assert "filename" in prompt  # it says NOT to use them


def test_the_prompt_says_it_cannot_mark_anything_done(tmp_path):
    prompt = build_planner_prompt("build a parser", tmp_path, "").lower()
    assert "done" in prompt
    assert "verification" in prompt or "gate" in prompt


def test_the_planner_gets_exactly_the_ledger_tools(tmp_path):
    names = {tool.name for tool in create_ledger_tools(Ledger(), tmp_path / "l.json")}
    assert names == {"add_tasks", "drop_task", "read_ledger"}


def test_the_coder_prompt_no_longer_demands_a_single_file():
    from rudra.subagents.registry import CODER

    prompt = CODER.system_prompt.lower()
    assert "one file" not in prompt
    assert "every file" in prompt
