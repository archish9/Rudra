"""plan() and work(), and that run_loop still equals their sum (Step 10c).

The seam exists so RudraAgent can put an approval gate between planning
and working. run_loop keeping its exact behaviour is what lets every 9c
and 10b test stand unmodified, so that equality is asserted here rather
than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, plan, run_loop, work
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths


@dataclass
class FakeAgentCfg:
    max_fix_attempts: int = 3
    verbose: bool = False


@dataclass
class FakeToolsCfg:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    agent: FakeAgentCfg = field(default_factory=FakeAgentCfg)
    tools: FakeToolsCfg = field(default_factory=FakeToolsCfg)
    models: dict = field(default_factory=dict)


@dataclass
class FakeSubagents:
    gate: object = None
    session_id: str = "s1"


@pytest.fixture
def context(tmp_path):
    return LoopContext(
        subagents=FakeSubagents(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(tmp_path),
    )


def _planner(seen, tasks=("write it",)):
    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append((stage, reason))
        if stage == "breakdown" and reason == "initial":
            for description in tasks:
                ledger.add(description)

    return planner


async def _noop(context, ledger=None):
    return None


def _always_done():
    async def fake_run_task(task, ledger, *, context):
        task.status = TaskStatus.DONE
        task.attempts = 1
        return Outcome.DONE

    return fake_run_task


async def test_plan_runs_the_three_stages_and_returns_a_filled_ledger(context):
    seen: list[tuple[str, str]] = []

    ledger = await plan("build it", context=context, planner=_planner(seen))

    assert seen == [
        ("clarify", "initial"),
        ("architect", "initial"),
        ("breakdown", "initial"),
    ]
    assert [task.description for task in ledger.tasks] == ["write it"]


async def test_plan_runs_no_task(monkeypatch, context):
    """Planning must not touch the project. That is the whole point."""
    ran: list[str] = []

    async def fake_run_task(task, ledger, *, context):
        ran.append(task.id)
        return Outcome.DONE

    monkeypatch.setattr(engine, "run_task", fake_run_task)
    await plan("build it", context=context, planner=_planner([]))

    assert ran == []


async def test_plan_uses_the_ledger_it_is_given(context):
    """The planner's tools are bound to one Ledger; a copy loses the tasks."""
    shared = Ledger()
    returned = await plan("build it", context=context, planner=_planner([]), ledger=shared)
    assert returned is shared


async def test_work_consults_neither_clarify_nor_architect(monkeypatch, context):
    seen: list[tuple[str, str]] = []
    ledger = Ledger()
    ledger.add("write it")

    monkeypatch.setattr(engine, "run_task", _always_done())
    monkeypatch.setattr(engine, "review_once", _noop)

    await work("build it", context=context, planner=_planner(seen), ledger=ledger)

    assert all(stage == "breakdown" for stage, _ in seen), seen


async def test_work_runs_the_tasks_it_is_handed(monkeypatch, context):
    ran: list[str] = []

    async def fake_run_task(task, ledger, *, context):
        ran.append(task.description)
        task.status = TaskStatus.DONE
        task.attempts = 1
        return Outcome.DONE

    ledger = Ledger()
    ledger.add("write it")
    ledger.add("test it")

    monkeypatch.setattr(engine, "run_task", fake_run_task)
    monkeypatch.setattr(engine, "review_once", _noop)

    result = await work("build it", context=context, planner=_planner([]), ledger=ledger)

    assert ran == ["write it", "test it"]
    assert result.success is True


async def test_run_loop_equals_plan_then_work(monkeypatch, context):
    """The composition must be exactly that -- no third behaviour."""
    monkeypatch.setattr(engine, "run_task", _always_done())
    monkeypatch.setattr(engine, "review_once", _noop)

    composed_seen: list[tuple[str, str]] = []
    composed_ledger = await plan("build it", context=context, planner=_planner(composed_seen))
    composed = await work(
        "build it", context=context, planner=_planner(composed_seen), ledger=composed_ledger
    )

    whole_seen: list[tuple[str, str]] = []
    whole = await run_loop("build it", context=context, planner=_planner(whole_seen))

    assert whole.success == composed.success
    assert whole.message == composed.message
    assert whole_seen == composed_seen
