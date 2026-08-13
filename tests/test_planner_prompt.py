"""The planner's prompt after the static-Q&A purge (Step 10a, C6.8a).

Surface #6 was a single line -- "Do NOT call ask_user() if the task
already specifies a framework or language" (planner_agent.py:78) -- and
it is the prompt-level twin of the middleware C0.2 deleted. Deleting the
middleware never removed it.
"""

from __future__ import annotations

from pathlib import Path

from rudra.agent.planner_agent import build_planner_prompt
from rudra.facts import FactStore


def test_the_prohibition_on_asking_is_gone(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path)
    assert "Do NOT call ask_user" not in prompt


def test_no_static_field_names_survive(tmp_path: Path):
    """The four boxes must not reappear as prompt text."""
    prompt = build_planner_prompt("build a web API", tmp_path).lower()
    for field_name in ("primary_language", "primary language", "database"):
        assert field_name not in prompt


def test_the_prompt_states_the_question_budget(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path, can_ask=True, max_questions=4)
    assert "4" in prompt
    assert "ask_user" in prompt
    assert "record_fact" in prompt


def test_an_unattended_run_is_told_there_is_nobody_to_ask(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path, can_ask=False)
    assert "ask_user" not in prompt
    assert "unattended" in prompt
    assert "record_fact" in prompt


def test_established_facts_are_rendered_into_the_prompt(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    prompt = build_planner_prompt("add a --format flag", tmp_path, facts=store)
    assert "## PROJECT FACTS" in prompt
    assert "Rust" in prompt


def test_no_facts_adds_no_section(tmp_path: Path):
    prompt = build_planner_prompt("add a --format flag", tmp_path, facts=FactStore())
    assert "PROJECT FACTS" not in prompt


def test_the_prompt_still_names_no_rudra_path(tmp_path: Path):
    """C6.10's property: the ledger is reached only through tools."""
    prompt = build_planner_prompt("build a web API", tmp_path)
    assert ".rudra" not in prompt
