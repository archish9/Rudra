"""The plan gate says, in the record, what it decided (OPEN-73).

`_settle_plan` is the branch that decides whether a run does any work at
all, and until this it printed to the console and nowhere else -- so the
debug log, which CLAUDE.md 3 calls "the COMPLETE record", the transcript
and usage.json all said nothing about it. Establishing that one real run
had not been approved took reading `select count(*) from embeddings` out
of the memory palace, because `_record_plan_memory` runs only after
APPROVE. An empty vector store is a fine proxy and a terrible instrument.

This is OPEN-44's rule one branch over: a NOTICE is what Rudra says about
ITSELF, as opposed to something a model did.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.console import Console

from rudra.agent.main_agent import AgentContext, RudraAgent
from rudra.loop.plan_view import PlanAnswer, PlanDecision

pytestmark = pytest.mark.anyio


class _Trace:
    """Records what TraceSink.notice was asked to emit."""

    def __init__(self) -> None:
        self.notices: list[dict] = []

    def notice(self, payload: str, *, role: str, name: str = "", **kwargs) -> None:
        self.notices.append({"payload": payload, "role": role, "name": name})


def _agent(tmp_path, *, approve, trace=None) -> RudraAgent:
    loop_context = SimpleNamespace(subagents=SimpleNamespace(trace=trace))
    return RudraAgent(
        context=AgentContext(project_path=tmp_path, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=loop_context,
        planner_callback=None,
        ledger=None,
        facts=None,
        approve=approve,
    )


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(
        "rudra.agent.main_agent.get_config",
        lambda *a, **k: SimpleNamespace(permissions=SimpleNamespace(mode=mode)),
    )


def _ledger():
    from rudra.loop.ledger import Ledger

    ledger = Ledger()
    ledger.add("write the database layer")
    return ledger


async def test_approving_is_recorded(tmp_path, monkeypatch):
    _mode(monkeypatch, "ask")
    trace = _Trace()
    agent = _agent(tmp_path, approve=lambda console: PlanAnswer(PlanDecision.APPROVE), trace=trace)

    assert await agent._settle_plan(_ledger()) is PlanDecision.APPROVE

    assert [n["name"] for n in trace.notices] == ["plan"]
    assert "approve" in trace.notices[0]["payload"]


async def test_cancelling_is_recorded(tmp_path, monkeypatch):
    """The case the whole item is filed on: a run that did no work must
    say why in the file a bug report attaches."""
    _mode(monkeypatch, "ask")
    trace = _Trace()
    agent = _agent(tmp_path, approve=lambda console: PlanAnswer(PlanDecision.CANCEL), trace=trace)

    assert await agent._settle_plan(_ledger()) is PlanDecision.CANCEL

    assert "cancel" in trace.notices[0]["payload"]


async def test_the_record_names_how_the_answer_was_obtained(tmp_path, monkeypatch):
    """`cancel` from a human and `cancel` from a closed pipe are different
    events, and the decision alone cannot tell them apart."""
    from rudra.loop.plan_view import auto_approve

    _mode(monkeypatch, "ask")
    trace = _Trace()
    agent = _agent(tmp_path, approve=auto_approve, trace=trace)

    await agent._settle_plan(_ledger())

    assert "auto" in trace.notices[0]["payload"]


async def test_plan_mode_is_recorded_as_itself(tmp_path, monkeypatch):
    """`--plan` presents and stops. That is a decision Rudra made, not one
    a user declined to make."""
    _mode(monkeypatch, "plan")
    trace = _Trace()

    def explode(console):
        raise AssertionError("plan mode must never prompt")

    agent = _agent(tmp_path, approve=explode, trace=trace)

    assert await agent._settle_plan(_ledger()) is PlanDecision.CANCEL
    assert "plan-mode" in trace.notices[0]["payload"]


async def test_the_revision_budget_running_out_is_recorded(tmp_path, monkeypatch):
    """Three revisions then cancel is not the same event as a user
    cancelling, and the ledger has spent a day on that distinction before."""
    _mode(monkeypatch, "ask")
    trace = _Trace()
    agent = _agent(
        tmp_path,
        approve=lambda console: PlanAnswer(PlanDecision.REVISE, "smaller tasks"),
        trace=trace,
    )

    async def planner(*args, **kwargs):
        return None

    agent._planner_callback = planner

    assert await agent._settle_plan(_ledger()) is PlanDecision.CANCEL
    assert "revision-limit" in trace.notices[-1]["payload"]


async def test_a_run_with_no_trace_still_settles(tmp_path, monkeypatch):
    """A hand-built RudraAgent carries no sink, and several tests build
    one. Recording must never be the reason a gate raises."""
    _mode(monkeypatch, "ask")
    agent = _agent(tmp_path, approve=lambda console: PlanAnswer(PlanDecision.APPROVE), trace=None)

    assert await agent._settle_plan(_ledger()) is PlanDecision.APPROVE
