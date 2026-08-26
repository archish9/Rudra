"""OPEN-32: what one planning stage records must reach the next one's prompt.

`CLAUDE.md` §3 states the invariant: *"The fact store is the only channel
between them."* `main_agent.py` repeats it beside the construction site.
The store is shared by reference, but `facts_block` renders it to a
**string** that is frozen into the compiled graph's `system_prompt`
(`planner_agent.py`), so sharing the object buys the prompt nothing --
what matters is WHEN the prompt is built.

Built through `create_main_agent` rather than `create_planner_agent`,
because the defect was never in either function: both are correct on their
own. It was in the wiring that built all three stages before any of them
ran. A test of the parts cannot see that, which is why this file exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from rudra.agent.main_agent import create_main_agent
from rudra.config import reset_config


@pytest.fixture(autouse=True)
def _coded_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build against the shipped defaults, not the developer's .env.

    Config.load() reads <cwd>/.env, so running from the repo root would
    build this against whatever backend the developer happens to have --
    the same fixture test_memory_run_wiring.py uses, for the same reason.
    """
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


@pytest.fixture
def planner_prompts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every system prompt the planner hands to deepagents, in order.

    Patched on `rudra.agent.planner_agent`, which is where
    `create_planner_agent` resolves the name.
    """
    prompts: list[str] = []

    def fake_create_deep_agent(**call: Any) -> object:
        prompts.append(call.get("system_prompt", ""))
        return object()

    monkeypatch.setattr("rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent)
    return prompts


@pytest.fixture
def consulted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the consult itself -- a real one is a model call."""
    stages: list[str] = []

    async def fake_consult_planner(_agent: Any, _ledger: Any, _request: str, **call: Any) -> None:
        stages.append(call["stage"])

    monkeypatch.setattr("rudra.agent.planner_agent.consult_planner", fake_consult_planner)
    return stages


async def test_a_stage_consulted_after_a_fact_is_recorded_carries_it(
    tmp_path: Path, planner_prompts: list[str], consulted: list[str]
) -> None:
    """The whole of OPEN-32, as the run performs it.

    In run `eb2e1e2e2b2c` the user answered "I want only BE in flask, and
    sqlite as DB". `clarify` recorded it faithfully. `architect` then
    designed a Typer CLI over JSON files and `breakdown` queued a FastAPI
    app -- three stages, three architectures, because neither of the last
    two ever saw the fact. Their prompts had been rendered before the run
    began.
    """
    project = tmp_path / "demo"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="write a todo application")
    try:
        # What the clarify stage does with the user's answer.
        agent._facts.record("app_type", "web_flask", "the user asked for Flask", "asked")

        before = len(planner_prompts)
        await agent._planner_callback(agent._ledger, "write a todo application", stage="architect")

        assert consulted == ["architect"]
        built = planner_prompts[before:]
        assert built, "the architect stage was not built at consult time"
        assert "app_type = web_flask" in built[-1]
    finally:
        await agent.close()


async def test_a_re_entered_breakdown_carries_facts_recorded_since_the_first_one(
    tmp_path: Path, planner_prompts: list[str], consulted: list[str]
) -> None:
    """`breakdown` is the one stage consulted again (S10b.3).

    Re-using one agent object made a re-plan five tasks into a run read the
    fact store as it stood before task 1 -- the worst case of the same
    defect, because by then the store holds everything the run learned.
    """
    project = tmp_path / "demo"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="write a todo application")
    try:
        await agent._planner_callback(agent._ledger, "req", stage="breakdown")
        agent._facts.record("storage", "sqlite", "settled while planning", "asked")

        before = len(planner_prompts)
        await agent._planner_callback(
            agent._ledger, "req", stage="breakdown", reason="ledger_empty"
        )

        built = planner_prompts[before:]
        assert built, "the re-entered breakdown stage reused a prompt built earlier"
        assert "storage = sqlite" in built[-1]
    finally:
        await agent.close()
