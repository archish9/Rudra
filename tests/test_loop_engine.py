"""The fix loop: every terminal condition in the spec's §5 table."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_task
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentResult
from rudra.verify.result import (
    DENIED,
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


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


def failing_report(findings=(Finding("a.py", 3, "bad type"),)):
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="typecheck", outcome=FAILED, blocking=True, findings=findings),
        ]
    )


def escalating_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name="typecheck",
                outcome=DENIED,
                blocking=True,
                escalate=True,
                detail="<auto:shell-not-opted-in>",
            ),
        ]
    )


def no_test_judgement_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name="test",
                outcome=NOT_APPLICABLE,
                blocking=True,
                detail="no tests were collected",
            ),
            StageResult(name="stubs", outcome=PASSED, blocking=True),
        ]
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
    """A coder that always writes something, and a git snapshot that sees it."""
    calls: list[str] = []

    async def coder(name, prompt, *, context, thread_id=None):
        calls.append(name)
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder)
    monkeypatch.setattr(engine, "git_snapshot", lambda ctx: frozenset())
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ("a.py",))
    return calls


async def run_one(context, description="write the parser"):
    ledger = Ledger()
    task = ledger.add(description)
    outcome = await run_task(task, ledger, context=context)
    return outcome, task, ledger


async def test_a_passing_gate_marks_the_task_done(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    assert task.attempts == 1
    assert task.files_touched == ("a.py",)


async def test_an_escalating_gate_stops_the_whole_run(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: escalating_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.STOP_RUN
    assert task.status is not TaskStatus.DONE


async def test_a_subagent_error_stops_the_whole_run(monkeypatch, context):
    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="no provider package")

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.STOP_RUN


async def test_a_halted_subagent_is_only_a_failed_attempt(monkeypatch, context):
    reports = [failing_report(), passing_report()]

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, halted_reason="repeated 3x")

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert task.attempts == 2


async def test_the_same_failure_twice_blocks(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 2, "stop on the second identical signature, not the budget"
    assert "no progress" in task.note


async def test_a_changing_failure_uses_the_whole_budget(monkeypatch, context):
    lines = iter([1, 2, 3, 4, 5])
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 3
    assert "attempts exhausted" in task.note


async def test_a_coder_that_writes_nothing_is_a_failed_attempt(monkeypatch, context):
    # With no changed files the gate's syntax stage reports "0 files
    # parsed" and stubs "0 changed file(s) scanned", so a green report
    # would mark an untouched task DONE. Spec §5.
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.status is not TaskStatus.DONE
    assert "wrote nothing" in task.note


async def test_no_test_judgement_dispatches_the_tester_once(monkeypatch, context, default_fakes):
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert default_fakes == ["coder", "tester"]


async def test_the_tester_is_not_dispatched_twice(monkeypatch, context, default_fakes):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: no_test_judgement_report())
    outcome, _, _ = await run_one(context)
    assert default_fakes.count("tester") == 1
    assert outcome is Outcome.DONE, "a project with no tests still finishes"


async def test_the_retry_prompt_carries_the_blocker_verbatim(monkeypatch, context):
    prompts: list[str] = []

    async def recorder(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="ok", ok=True)

    lines = iter([7, 8, 9])
    monkeypatch.setattr(engine, "run_subagent", recorder)
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("models.py", next(lines), "incompatible type"),)),
    )
    await run_one(context)
    assert "models.py:7" in prompts[1]
    assert "incompatible type" in prompts[1]


async def test_each_attempt_gets_its_own_thread(monkeypatch, context):
    threads: list[str] = []

    async def recorder(name, prompt, *, context, thread_id=None):
        threads.append(thread_id)
        return SubagentResult(name=name, text="ok", ok=True)

    lines = iter([1, 2, 3])
    monkeypatch.setattr(engine, "run_subagent", recorder)
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "x"),)),
    )
    await run_one(context)
    assert len(set(threads)) == 3


async def test_a_budget_of_one_allows_no_retry(monkeypatch, context):
    context.cfg.agent.max_fix_attempts = 1
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 1


# --- A1.68: the reviewer must be told what changed ---


async def test_the_reviewer_is_told_which_files_the_run_touched(monkeypatch, context):
    """A1.68: git_diff shows tracked changes, and greenfield files are untracked.

    Measured across two Step 9c acceptance runs: a run that wrote
    parser.py and test_parser.py into a fresh repo got "No changes in the
    working tree" from the reviewer, while a run that edited a committed
    file produced a real finding. The reviewer worked exactly when a file
    was already tracked -- which is never, on the greenfield runs Rudra is
    most often pointed at.
    """
    prompts: list[str] = []

    async def fake(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="looks fine", ok=True)

    monkeypatch.setattr(engine, "run_subagent", fake)

    ledger = Ledger()
    done = ledger.add("write the parser")
    done.status = TaskStatus.DONE
    done.files_touched = ("parser.py", "src/lib.rs")

    await engine.review_once(context, ledger)

    assert prompts, "the reviewer must be dispatched"
    assert "parser.py" in prompts[0]
    assert "src/lib.rs" in prompts[0]


async def test_the_reviewer_prompt_lists_each_file_once(monkeypatch, context):
    """Two tasks touching one file is the norm, not the exception."""
    prompts: list[str] = []

    async def fake(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="", ok=True)

    monkeypatch.setattr(engine, "run_subagent", fake)

    ledger = Ledger()
    for description in ("write it", "fix it"):
        task = ledger.add(description)
        task.status = TaskStatus.DONE
        task.files_touched = ("src/main.rs",)

    await engine.review_once(context, ledger)

    assert prompts[0].count("src/main.rs") == 1


async def test_a_blocked_task_s_files_are_still_reviewed(monkeypatch, context):
    """Blocked work is exactly the work most worth a second opinion."""
    prompts: list[str] = []

    async def fake(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="", ok=True)

    monkeypatch.setattr(engine, "run_subagent", fake)

    ledger = Ledger()
    blocked = ledger.add("write the parser")
    blocked.status = TaskStatus.BLOCKED
    blocked.files_touched = ("half_done.py",)

    await engine.review_once(context, ledger)

    assert "half_done.py" in prompts[0]


async def test_the_reviewer_still_runs_when_nothing_was_recorded(monkeypatch, context):
    """files_touched is empty outside a git repo; the pass must not vanish."""
    prompts: list[str] = []

    async def fake(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="", ok=True)

    monkeypatch.setattr(engine, "run_subagent", fake)

    await engine.review_once(context, Ledger())

    assert prompts, "an empty file list must not skip the review"


async def test_a_finished_task_records_how_long_it_took(monkeypatch, context):
    """The per-task half of C9.6: a user asking "what was slow" is asking
    about a task, not about a role."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    _outcome, task, _ledger = await run_one(context)
    assert task.seconds > 0


async def test_a_blocked_task_records_the_time_it_burned(monkeypatch, context):
    """Three failed attempts cost the user three attempts' worth of wait."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ledger = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.seconds > 0


async def test_a_stopped_run_records_the_time_too(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: escalating_report())
    outcome, task, _ledger = await run_one(context)
    assert outcome is Outcome.STOP_RUN
    assert task.seconds > 0


async def test_seconds_survive_the_save_run_task_makes(monkeypatch, context):
    """run_task must assign before it saves, or the ledger on disk reports
    0.0 for a task that took a minute."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    _outcome, task, _ledger = await run_one(context)
    assert Ledger.load(context.paths.ledger_json).get(task.id).seconds == task.seconds
