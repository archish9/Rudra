"""triage wired into loop/engine.py::run_task (OPEN-178 / S6 Task 2).

Before the coder is dispatched, run_task reads the pending task with laya once
(``context.decide``) and acts on the result:

* confident-no ``needs_tests`` -> the tester subagent is NOT dispatched (the
  one subagent this feature removes);
* confident-hard ``complexity`` -> this task's fix budget is raised by the
  triage delta, never cut below the configured floor;
* abstain / unavailable / no engine -> byte-identical to today (tester runs,
  default budget).

The deterministic verify gate still runs regardless of ``skip_tests`` -- the
tester writes tests, it does not gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.decide.engine import Decision
from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_task
from rudra.loop.ledger import Ledger
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentResult
from rudra.verify.result import FAILED, NOT_APPLICABLE, PASSED, Finding, StageResult, VerifyReport


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


def passing_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="test", outcome=PASSED, blocking=True),
            StageResult(name="stubs", outcome=PASSED, blocking=True),
        ]
    )


def no_test_judgement_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="test", outcome=NOT_APPLICABLE, blocking=True, detail="no tests"),
            StageResult(name="stubs", outcome=PASSED, blocking=True),
        ]
    )


def failing_report(findings):
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="typecheck", outcome=FAILED, blocking=True, findings=findings),
        ]
    )


class _FakeEngine:
    """Returns the three triage Decisions; always available."""

    def __init__(self, decisions: dict[str, Decision]) -> None:
        self._decisions = decisions

    def available(self) -> bool:
        return True

    def decide(self, state, questions):
        return dict(self._decisions)


def _d(qid, kind, value, *, abstain=False, route="auto", backend="mlx") -> Decision:
    return Decision(
        question_id=qid,
        kind=kind,
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=0.9,
        backend=backend,
    )


def _engine(*, needs_tests, complexity=1.0, **kw) -> _FakeEngine:
    return _FakeEngine(
        {
            "needs_tests": _d("needs_tests", "noul", needs_tests, **kw),
            "complexity": _d("complexity", "score", complexity, **kw),
            "domain": _d("domain", "choice", "python", **kw),
        }
    )


@pytest.fixture
def context(tmp_path):
    return LoopContext(
        subagents=FakeSubagents(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(tmp_path),
    )


@pytest.fixture(autouse=True)
def default_fakes(monkeypatch):
    """A coder that always writes, a git snapshot that sees it, and a record of
    every subagent name dispatched + every verify_project call."""
    calls: list[str] = []
    verifies: list[int] = []

    async def coder(name, prompt, *, context, thread_id=None):
        calls.append(name)
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder)
    monkeypatch.setattr(engine, "git_snapshot", lambda ctx: frozenset())
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ("a.py",))
    return calls, verifies


async def run_one(context, description="write the thing"):
    ledger = Ledger()
    task = ledger.add(description)
    outcome = await run_task(task, ledger, context=context)
    return outcome, task, ledger


# --- skip the tester on confident-no ----------------------------------------


async def test_confident_no_needs_tests_skips_the_tester(monkeypatch, context, default_fakes):
    calls, _ = default_fakes
    context.decide = _engine(needs_tests=0.1)  # confident the task needs no tests
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: no_test_judgement_report())
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert "tester" not in calls, "triage should have skipped the tester"
    assert calls == ["coder"]


async def test_the_gate_still_runs_when_the_tester_is_skipped(monkeypatch, context, default_fakes):
    # skip_tests removes the tester, never the deterministic verify gate.
    context.decide = _engine(needs_tests=0.1)
    verifies = {"n": 0}

    def verify(*a, **k):
        verifies["n"] += 1
        return no_test_judgement_report()

    monkeypatch.setattr(engine, "verify_project", verify)
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert verifies["n"] >= 1, "the verify gate must run even when the tester is skipped"


# --- confident-yes / abstain / no engine -> tester still runs ---------------


async def test_confident_yes_needs_tests_still_runs_the_tester(monkeypatch, context, default_fakes):
    calls, _ = default_fakes
    context.decide = _engine(needs_tests=0.9)  # the task wants tests
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert calls == ["coder", "tester"]


async def test_abstain_triage_leaves_the_tester_running(monkeypatch, context, default_fakes):
    calls, _ = default_fakes
    context.decide = _engine(needs_tests=0.1, abstain=True, route="review")
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert calls == ["coder", "tester"]


async def test_no_engine_leaves_the_tester_running(monkeypatch, context, default_fakes):
    calls, _ = default_fakes
    assert context.decide is None  # the default
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert calls == ["coder", "tester"]


# --- complexity -> fix budget (raises only, never starves) ------------------


async def test_hard_complexity_raises_the_fix_budget(monkeypatch, context, default_fakes):
    # A gate that fails on a NEW line every attempt uses the whole budget, so
    # task.attempts at BLOCKED equals the ceiling. Hard complexity adds 2 to the
    # configured 3 -> 5 attempts before BLOCKED.
    context.decide = _engine(needs_tests=0.9, complexity=2.0)
    lines = iter(range(1, 50))
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    _, task, _ = await run_one(context)
    assert task.attempts == 5


async def test_normal_complexity_uses_the_configured_budget(monkeypatch, context, default_fakes):
    context.decide = _engine(needs_tests=0.9, complexity=1.0)
    lines = iter(range(1, 50))
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    _, task, _ = await run_one(context)
    assert task.attempts == 3


async def test_no_engine_uses_the_configured_budget(monkeypatch, context, default_fakes):
    assert context.decide is None
    lines = iter(range(1, 50))
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    _, task, _ = await run_one(context)
    assert task.attempts == 3
