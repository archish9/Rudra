"""Ctrl-C stops at a task boundary and leaves a resumable ledger (C9.3).

No signals here. A signal test proves the plumbing; this proves the
*state*, which is what decides whether the interrupted work is
recoverable — and A1.93 is a state bug, not a plumbing one: the in-flight
task was left `IN_PROGRESS`, which `Ledger.resumable()` excludes on
purpose (ledger.py:91-100), so `--continue` skipped the one task the user
had interrupted and said nothing about it.

The two halves are tested apart for that reason. `tests/test_cli_cancel.py`
owns the signal.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, work
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths


@dataclass
class FakeAgentCfg:
    max_fix_attempts: int = 3
    verbose: bool = False


@dataclass
class FakeCfg:
    agent: FakeAgentCfg = field(default_factory=FakeAgentCfg)


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


async def _noop_planner(*args, **kwargs):
    return None


def _cancelling(started: list[str] | None = None):
    """A run_task that marks the task in progress, then is cancelled."""

    async def cancelled_task(task, run_ledger, *, context):
        if started is not None:
            started.append(task.id)
        task.status = TaskStatus.IN_PROGRESS
        raise asyncio.CancelledError

    return cancelled_task


async def test_a_cancelled_task_goes_back_to_pending(monkeypatch, context):
    """A1.93 verbatim. IN_PROGRESS is worse than useless here: it is the
    one status a resume refuses to pick up."""
    ledger = Ledger()
    ledger.add("write the parser")
    monkeypatch.setattr(engine, "run_task", _cancelling())

    await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert ledger.tasks[0].status is TaskStatus.PENDING


async def test_the_ledger_on_disk_is_the_one_continue_accepts(monkeypatch, context):
    """The cancel path and the resume path are the same path."""
    from rudra.agent.main_agent import check_resumable

    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(context.paths.ledger_json, request="build it")
    monkeypatch.setattr(engine, "run_task", _cancelling())

    await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    resumed = check_resumable(context.paths.ledger_json, None)
    assert [task.description for task in resumed.resumable()] == ["write the parser"]


async def test_work_stops_rather_than_taking_the_next_task(monkeypatch, context):
    ledger = Ledger()
    ledger.add("first")
    ledger.add("second")
    started: list[str] = []
    monkeypatch.setattr(engine, "run_task", _cancelling(started))

    await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert started == ["t1"], "a cancel must not roll on to the next task"


async def test_a_cancelled_run_is_not_reported_successful(monkeypatch, context):
    ledger = Ledger()
    ledger.add("write the parser")
    monkeypatch.setattr(engine, "run_task", _cancelling())

    result = await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert result.success is False
    assert "cancel" in result.message.lower()


async def test_work_that_is_not_cancelled_says_nothing_about_cancelling(monkeypatch, context):
    ledger = Ledger()
    ledger.add("write the parser")

    async def finished(task, run_ledger, *, context):
        task.status = TaskStatus.DONE
        return engine.Outcome.DONE

    monkeypatch.setattr(engine, "run_task", finished)
    monkeypatch.setattr(engine, "review_once", _noop_planner)
    monkeypatch.setattr(engine, "summarise_architecture", _noop_planner)

    result = await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert result.success is True
    assert "cancel" not in result.message.lower()


async def test_no_review_and_no_architecture_summary_after_a_cancel(monkeypatch, context):
    """Both are model calls. Somebody who just pressed Ctrl-C is not
    waiting through two more inferences to be told they succeeded in
    stopping."""
    ledger = Ledger()
    ledger.add("already done")
    ledger.tasks[0].status = TaskStatus.DONE
    ledger.add("interrupted")
    calls: list[str] = []

    async def record_review(context, ledger):
        calls.append("review")

    async def record_summary(context, ledger):
        calls.append("summary")

    monkeypatch.setattr(engine, "run_task", _cancelling())
    monkeypatch.setattr(engine, "review_once", record_review)
    monkeypatch.setattr(engine, "summarise_architecture", record_summary)

    await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert calls == []


async def test_the_usage_log_is_still_written_on_a_cancel(monkeypatch, context):
    """A cancelled run still cost tokens and wall clock, and that is
    exactly when somebody wants to know how much."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=2, seconds=1.0)
    context.usage = usage

    ledger = Ledger()
    ledger.add("write the parser")
    monkeypatch.setattr(engine, "run_task", _cancelling())

    await work("build it", context=context, planner=_noop_planner, ledger=ledger)

    assert (context.paths.logs / "usage.json").exists()
