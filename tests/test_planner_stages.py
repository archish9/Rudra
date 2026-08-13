"""Three stages, three tool sets (Step 10b, C6.7).

A stage cannot do another stage's job because the tool is not registered
-- the enforcement S9b.3 used for the reviewer and S10a.5 for ask_user.
These tests assert the tool lists, because that is the mechanism; a
prompt saying "clarify first" is a hint, and Step 7's acceptance run
already watched a model route around one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.agent.planner_agent import STAGES, create_planner_agent
from rudra.facts import FactStore


def _capture(monkeypatch, tmp_path: Path, stage: str, **kwargs) -> dict:
    captured: dict = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr("rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent)
    create_planner_agent(
        task="build a web API",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
        stage=stage,
        **kwargs,
    )
    return captured


def _tool_names(monkeypatch, tmp_path: Path, stage: str, **kwargs) -> set[str]:
    return {tool.name for tool in _capture(monkeypatch, tmp_path, stage, **kwargs)["tools"]}


def test_the_stage_names_are_the_three_the_spec_names():
    assert STAGES == ("clarify", "architect", "breakdown")


def test_clarify_can_ask_and_record_but_not_plan(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "clarify")
    assert "ask_user" in names
    assert "record_fact" in names
    assert "add_tasks" not in names
    assert "drop_task" not in names


def test_architect_can_record_but_not_ask_or_plan(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "architect")
    assert "record_fact" in names
    assert "ask_user" not in names, "the questions are settled by now"
    assert "add_tasks" not in names


def test_breakdown_can_plan_but_not_ask_or_record(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "breakdown")
    assert {"add_tasks", "drop_task", "read_ledger"} <= names
    assert "ask_user" not in names
    assert "record_fact" not in names


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_can_mark_anything_done(monkeypatch, tmp_path: Path, stage: str):
    """S9c.1 is structural and stays that way."""
    names = _tool_names(monkeypatch, tmp_path, stage)
    assert not {"mark_done", "complete_task", "set_status"} & names


def test_an_unattended_clarify_has_no_ask_user(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "clarify", interactive=False)
    assert "ask_user" not in names
    assert "record_fact" in names


def test_an_unknown_stage_raises(monkeypatch, tmp_path: Path):
    """A typo must not silently produce a toolless agent."""
    with pytest.raises(ValueError, match="architcet"):
        _tool_names(monkeypatch, tmp_path, "architcet")


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_stage_carries_the_permission_gate(monkeypatch, tmp_path: Path, stage: str):
    from rudra.config.loader import build_config, reset_config
    from rudra.permissions import build_gate

    captured: dict = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr("rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent)
    reset_config()
    try:
        gate = build_gate(build_config(tmp_path), tmp_path)
        create_planner_agent(
            task="t",
            project_path=tmp_path,
            filesystem_backend=object(),
            checkpointer=None,
            console=Console(quiet=True),
            gate=gate,
            stage=stage,
        )
    finally:
        reset_config()

    assert captured["middleware"][0] is gate.middleware
    assert captured["interrupt_on"] is gate.interrupt_on


def test_a_fact_recorded_in_clarify_reaches_the_architect(monkeypatch, tmp_path: Path):
    """The store is the only channel between stages (S10b.1)."""
    store = FactStore()
    store.record("language", "Rust", "the user asked for Rust", "asked")

    captured = _capture(monkeypatch, tmp_path, "architect", facts=store)

    assert "Rust" in captured["system_prompt"]
