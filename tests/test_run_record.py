"""A run says when it started and why it ended (OPEN-77).

Nothing marked either end before this. A debug log that stopped mid-stage
was indistinguishable from a cancel, a crash, a declined plan and a
finished run -- and the run this was filed on stopped for a reason no file
in the folder recorded.

The two ends that leave by raising are the two that most need a record,
which is why this catches BaseException rather than using `else`: a
cancelled turn arrives as CancelledError.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from rich.console import Console

from rudra.agent.main_agent import AgentContext, RudraAgent
from rudra.loop.plan_view import PlanDecision

pytestmark = pytest.mark.anyio


class _Trace:
    def __init__(self) -> None:
        self.notices: list[dict] = []

    def notice(self, payload: str, *, role: str, name: str = "", **kwargs) -> None:
        self.notices.append({"payload": payload, "role": role, "name": name})


def _agent(tmp_path, trace) -> RudraAgent:
    return RudraAgent(
        context=AgentContext(project_path=tmp_path, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=SimpleNamespace(subagents=SimpleNamespace(trace=trace)),
        planner_callback=None,
        ledger=None,
    )


def _run_records(trace) -> list[str]:
    return [n["payload"] for n in trace.notices if n["name"] == "run"]


async def test_both_ends_are_recorded(tmp_path, monkeypatch):
    from rudra.agent.main_agent import AgentResult

    trace = _Trace()
    agent = _agent(tmp_path, trace)

    async def finished():
        return AgentResult(True, "Tasks: 3 requested · 3 done", [], [])

    monkeypatch.setattr(agent, "_run", finished)
    await agent.run()

    started, ended = _run_records(trace)
    assert "started" in started
    assert "ended: ok: Tasks: 3 requested · 3 done" in ended


async def test_the_verdict_is_carried_because_the_panel_is_printed_after_close(
    tmp_path, monkeypatch
):
    """cli.py prints result.message in a panel AFTER close() has detached
    the log, so this is the only place that line can be recorded."""
    from rudra.agent.main_agent import AgentResult

    trace = _Trace()
    agent = _agent(tmp_path, trace)

    async def declined():
        return AgentResult(True, "Plan not executed (cancel): 15 task(s) declared", [], [])

    monkeypatch.setattr(agent, "_run", declined)
    await agent.run()

    assert "Plan not executed (cancel): 15 task(s) declared" in _run_records(trace)[-1]


async def test_a_crash_is_recorded_and_still_raises(tmp_path, monkeypatch):
    trace = _Trace()
    agent = _agent(tmp_path, trace)

    async def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "_run", explode)
    with pytest.raises(RuntimeError):
        await agent.run()

    assert "ended: RuntimeError" in _run_records(trace)[-1]


async def test_a_cancelled_turn_is_recorded(tmp_path, monkeypatch):
    """Ctrl-C leaves as CancelledError, which is a BaseException. An
    `except Exception` here would record every end except this one."""
    trace = _Trace()
    agent = _agent(tmp_path, trace)

    async def cancelled():
        raise asyncio.CancelledError

    monkeypatch.setattr(agent, "_run", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await agent.run()

    assert "ended: CancelledError" in _run_records(trace)[-1]


async def test_an_agent_with_no_sink_still_runs(tmp_path, monkeypatch):
    from rudra.agent.main_agent import AgentResult

    agent = _agent(tmp_path, None)

    async def finished():
        return AgentResult(True, "done", [], [])

    monkeypatch.setattr(agent, "_run", finished)
    assert (await agent.run()).success


def test_plan_decision_still_has_its_own_record():
    """OPEN-73's NOTICE is named `plan` and this one `run`. Two events,
    two questions: what the gate decided, and how the run ended."""
    assert PlanDecision.CANCEL.value == "cancel"
