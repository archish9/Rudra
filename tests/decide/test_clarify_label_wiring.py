"""RudraAgent._record_clarify_label emits the row at run end (OPEN-176).

Verifies the agent gathers the right primitives (greenfield@start, asked from
the fact store, run verdict, attempted from the ledger) and calls record_label
only when a task was worked.
"""

from __future__ import annotations

from types import SimpleNamespace

from rich.console import Console

import rudra.agent.main_agent as main_agent
from rudra.agent.main_agent import AgentContext, RudraAgent
from rudra.facts import FactStore
from rudra.loop.ledger import Ledger


def _agent(tmp_path, *, facts, ledger) -> RudraAgent:
    return RudraAgent(
        context=AgentContext(project_path=tmp_path, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=SimpleNamespace(subagents=SimpleNamespace(trace=None)),
        planner_callback=None,
        ledger=ledger,
        facts=facts,
    )


def _capture(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(main_agent, "record_label", lambda **kw: calls.append(kw))
    return calls


def test_emits_row_for_a_worked_run(tmp_path, monkeypatch):
    facts = FactStore()
    facts.record("port", "8080", "user said so", "asked")
    ledger = Ledger()
    ledger.add("build it")
    ledger.tasks[0].attempts = 2

    agent = _agent(tmp_path, facts=facts, ledger=ledger)
    agent._greenfield_at_start = True
    agent._run_succeeded = True

    calls = _capture(monkeypatch)
    agent._record_clarify_label()

    assert len(calls) == 1
    kw = calls[0]
    assert kw["question_id"] == "needs_clarification"
    assert kw["label"] is True  # a fact was asked
    assert kw["features"] == {
        "greenfield": True,
        "asked": True,
        "questions_n": kw["features"]["questions_n"],  # from live config, int
        "run_converged": True,
    }
    assert isinstance(kw["features"]["questions_n"], int)


def test_no_row_when_no_task_attempted(tmp_path, monkeypatch):
    facts = FactStore()
    ledger = Ledger()
    ledger.add("build it")  # attempts stays 0

    agent = _agent(tmp_path, facts=facts, ledger=ledger)
    calls = _capture(monkeypatch)
    agent._record_clarify_label()

    assert calls == []


def test_label_false_when_nothing_asked(tmp_path, monkeypatch):
    facts = FactStore()
    facts.record("framework", "flask", "detected from request", "inferred")
    ledger = Ledger()
    ledger.add("build it")
    ledger.tasks[0].attempts = 1

    agent = _agent(tmp_path, facts=facts, ledger=ledger)
    agent._run_succeeded = True
    calls = _capture(monkeypatch)
    agent._record_clarify_label()

    assert len(calls) == 1
    assert calls[0]["label"] is False
    assert calls[0]["features"]["asked"] is False
