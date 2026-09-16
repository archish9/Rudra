"""The planner's prompt after the static-Q&A purge (Step 10a, C6.8a).

Surface #6 was a single line -- "Do NOT call ask_user() if the task
already specifies a framework or language" (planner_agent.py:78) -- and
it is the prompt-level twin of the middleware C0.2 deleted. Deleting the
middleware never removed it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.agent.planner_agent import STAGES, build_planner_prompt
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
    """Step 10b moved asking onto the clarify stage; the budget went with it."""
    prompt = build_planner_prompt(
        "build a web API", tmp_path, stage="clarify", can_ask=True, max_questions=4
    )
    assert "4" in prompt
    assert "ask_user" in prompt
    assert "record_fact" in prompt


def test_an_unattended_run_is_told_there_is_nobody_to_ask(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path, stage="clarify", can_ask=False)
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


# --- OPEN-116: the listing replaces the order to list ----------------------


@pytest.mark.parametrize("can_read", [True, False])
@pytest.mark.parametrize("stage", STAGES)
def test_no_stage_is_told_to_list_the_project(tmp_path: Path, stage: str, can_read: bool):
    """`_COMMON_HEADER` said *"To find out what is here, call ls("/")"*, and
    20 of 25 archived planner stages opened with exactly that call -- 53.6%
    of run `8f160d92c6da`'s model time. Removing an order, not adding a
    counter-order (OPEN-17)."""
    prompt = build_planner_prompt("build a web API", tmp_path, stage=stage, can_read=can_read)

    assert 'ls("/")' not in prompt
    assert "To find out what is here" not in prompt


def test_the_structure_block_is_the_listing_the_tools_would_give(tmp_path: Path):
    """Virtual and capped, the subagents' form (`subagents/build.py`). The
    relative spelling beside `ls`'s `/`-prefixed answer is what invited the
    check (plan §3.2)."""
    from rudra.filesystem import as_virtual_paths, project_tree
    from rudra.filesystem.tree import TREE_MAX_ENTRIES

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("hi\n")

    prompt = build_planner_prompt("build a web API", tmp_path)
    listing = as_virtual_paths(project_tree(tmp_path, max_entries=TREE_MAX_ENTRIES))

    assert "## PROJECT STRUCTURE" in prompt
    assert listing in prompt
    assert "/src/app.py" in prompt
    assert "\nsrc/app.py" not in prompt


def test_the_structure_block_is_capped_like_the_subagents(tmp_path: Path):
    from rudra.filesystem.tree import TREE_MAX_ENTRIES

    for i in range(TREE_MAX_ENTRIES + 20):
        (tmp_path / f"mod_{i:03d}.py").write_text("x = 1\n")

    prompt = build_planner_prompt("build a web API", tmp_path)

    assert f"(cap: {TREE_MAX_ENTRIES})" in prompt
    assert prompt.count("/mod_") == TREE_MAX_ENTRIES


def test_the_structure_block_is_billed(tmp_path: Path):
    """`CLAUDE.md` §5a: a fixed-prompt block ships with the number that
    prices it. `roles.planner.tree_chars` was 0 in every usage.json while
    the block was always there."""
    from rudra.context.usage import RunUsage

    (tmp_path / "app.py").write_text("x = 1\n")
    usage = RunUsage()

    prompt = build_planner_prompt("build a web API", tmp_path, usage=usage)

    planner = usage.as_dict()["planner"]
    assert planner["tree_injections"] == 1
    assert 0 < planner["tree_chars"] < len(prompt)
    assert "/app.py" in prompt


@pytest.mark.parametrize("stage", STAGES)
def test_a_stage_without_file_tools_is_told_the_project_is_empty(tmp_path: Path, stage: str):
    """And names no file tool, because it holds none (OPEN-15)."""
    (tmp_path / ".mcp.json").write_text("{}\n")

    prompt = build_planner_prompt("build a web API", tmp_path, stage=stage, can_read=False)

    assert "holds no files yet" in prompt
    assert "/.mcp.json" in prompt
    for name in ("ls", "read_file", "glob", "grep"):
        assert f"`{name}`" not in prompt, name
        assert f"{name}(" not in prompt, name


def test_a_stage_without_file_tools_is_not_told_to_read_first(tmp_path: Path):
    """Clarify's *"Look before you ask. Read what is already here first."* is
    an order to read, given to a stage with nothing to read and no tool to
    read it with."""
    readable = build_planner_prompt("build a web API", tmp_path, stage="clarify")
    greenfield = build_planner_prompt("build a web API", tmp_path, stage="clarify", can_read=False)

    assert "Read what is already here first" in readable
    assert "Read what is already here first" not in greenfield
