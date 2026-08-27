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


# --- each stage's prompt states its own job and no other ---


def _prompt(tmp_path: Path, stage: str, **kwargs) -> str:
    from rudra.agent.planner_agent import build_planner_prompt

    return build_planner_prompt("build a web API", tmp_path, stage=stage, **kwargs)


def test_the_clarify_prompt_asks_for_facts_not_tasks(tmp_path: Path):
    prompt = _prompt(tmp_path, "clarify")
    assert "record_fact" in prompt
    assert "ask_user" in prompt
    assert "add_tasks" not in prompt, "clarify has no such tool; naming it invites a dead call"


def test_the_architect_prompt_asks_for_decisions_with_reasons(tmp_path: Path):
    prompt = _prompt(tmp_path, "architect")
    assert "record_fact" in prompt
    assert "ask_user" not in prompt
    assert "add_tasks" not in prompt
    for word in ("layout", "why"):
        assert word in prompt.lower()


def test_the_breakdown_prompt_is_the_task_one(tmp_path: Path):
    prompt = _prompt(tmp_path, "breakdown")
    assert "add_tasks" in prompt
    assert "record_fact" not in prompt
    assert "ask_user" not in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_prompt_claims_it_can_finish_anything(tmp_path: Path, stage: str):
    """S9c.1: the gate decides done, and every stage must say so."""
    prompt = _prompt(tmp_path, stage).lower()
    assert "cannot mark" in prompt or "gate" in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_prompt_names_a_rudra_path(tmp_path: Path, stage: str):
    assert ".rudra" not in _prompt(tmp_path, stage)


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_stage_prompt_says_where_the_project_root_is(tmp_path: Path, stage: str):
    """OPEN-9: every subagent gets a `## FILE PATH RULES` block
    (subagents/registry.py:34-36) and the planner got nothing, while
    holding ls, read_file, glob and grep. It guessed `/home/user`."""
    prompt = _prompt(tmp_path, stage)
    assert "project root" in prompt.lower()
    assert "virtual" in prompt.lower()


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_stage_prompt_names_the_prefixes_models_hallucinate(tmp_path: Path, stage: str):
    """Named explicitly rather than left to inference, because these are the
    exact strings `compat/path_constants.py` records models inventing -- and
    `/home/user` is the one that was actually observed."""
    prompt = _prompt(tmp_path, stage)
    assert "/home/user" in prompt


def test_an_unattended_clarify_prompt_says_nobody_can_answer(tmp_path: Path):
    prompt = _prompt(tmp_path, "clarify", can_ask=False)
    assert "unattended" in prompt
    assert "ask_user" not in prompt


def test_an_unknown_stage_prompt_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="architcet"):
        _prompt(tmp_path, "architcet")


def test_planner_middleware_carries_a_filesystem_middleware_with_a_limit():
    """A1.47: before Step 12a the planner had none, so it took 20000."""
    from deepagents.backends.filesystem import FilesystemBackend

    from rudra.agent.planner_agent import build_planner_middleware

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)
    middleware = build_planner_middleware("a task", backend=backend, evict_tokens=13107)

    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1
    assert filesystem[0]._tool_token_limit_before_evict == 13107


def test_planner_middleware_without_a_backend_is_unchanged():
    """Every existing caller and test passes no backend and must still work.

    RepeatGuardMiddleware joined the always-on trio with OPEN-10 and
    DelegationGuardMiddleware with OPEN-37, and the ORDER is the assertion
    that matters: the repeat guard must sit after the param fixer so a
    repaired path is judged as the call it became, not as the one the model
    mistyped."""
    from rudra.agent.planner_agent import build_planner_middleware

    middleware = build_planner_middleware("a task")
    assert [type(m).__name__ for m in middleware] == [
        "FixWriteParamsMiddleware",
        "RepeatGuardMiddleware",
        "DelegationGuardMiddleware",
    ]


def test_planner_with_no_declared_window_keeps_the_upstream_default():
    """Same omit-vs-None hazard as the subagents (see evict_kwargs)."""
    from deepagents.backends.filesystem import FilesystemBackend

    from rudra.agent.planner_agent import build_planner_middleware

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)
    middleware = build_planner_middleware("a task", backend=backend, evict_tokens=None)

    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"][0]
    assert filesystem._tool_token_limit_before_evict == 20000


def test_planner_middleware_reports_its_usage():
    from rudra.agent.planner_agent import build_planner_middleware
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    middleware = build_planner_middleware("a task", usage=usage)

    recorders = [m for m in middleware if type(m).__name__ == "UsageMiddleware"]
    assert len(recorders) == 1
    assert recorders[0].role == "planner"
    assert recorders[0].usage is usage
