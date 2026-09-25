"""The fix loop: every terminal condition in the spec's §5 table."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_task
from rudra.loop.engine import changed_since as real_changed_since
from rudra.loop.engine import git_snapshot as real_git_snapshot
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentResult
from rudra.verify.pipeline import _parse_findings, syntax_stage
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
    # CR-C1: and specifically PENDING, not IN_PROGRESS. Ledger.resumable()
    # excludes IN_PROGRESS on purpose, so a task left there is the one task
    # `--continue` will never retry -- A1.93 on the STOP_RUN path. The
    # cancel handler already does this; the two paths must not disagree.
    assert task.status is TaskStatus.PENDING


async def test_a_stopped_run_leaves_its_task_resumable(monkeypatch, context):
    """CR-C1, measured where it bites: on disk, through Ledger.resumable()."""

    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="provider unreachable")

    monkeypatch.setattr(engine, "run_subagent", broken)
    outcome, task, ledger = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert [t.id for t in ledger.resumable()] == [task.id]


async def test_a_subagent_that_keeps_erroring_stops_the_whole_run(monkeypatch, context):
    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="no provider package")

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.STOP_RUN
    # The note must survive to the summary: it is the only place the user
    # learns WHY the run ended, and "3 attempts exhausted" is not a reason.
    assert "could not run" in task.note
    assert "no provider package" in task.note


async def test_one_transient_error_costs_neither_an_attempt_nor_the_run(monkeypatch, context):
    """OPEN-33: run `eb2e1e2e2b2c` ended `0 done · 11 never attempted` on one
    HTTP 500 from the provider, 693 seconds into task 1.

    `subagents/runner.py` catches every exception and reports it as
    `SubagentResult.error`, so a provider 500 and a broken config are the
    same value; the loop treated that value as fatal on the stated grounds
    that it "would recur on every remaining task". A 500 is the
    counterexample.

    **This test used to assert `attempts == 2` and was named "costs an
    attempt".** OPEN-46 §6 option C changed that half deliberately on
    2026-09-01: the run not ending is OPEN-33's finding and still holds; the
    attempt being spent was incidental to it and was the thing that cost
    run14's t8 its whole budget. Both halves are asserted below so a future
    change cannot trade one for the other.
    """
    results = [
        SubagentResult(name="coder", text="", ok=False, error="Error code: 500 - internal"),
        SubagentResult(name="coder", text="wrote it", ok=True),
    ]

    async def flaky(name, prompt, *, context, thread_id=None):
        return results.pop(0) if results else SubagentResult(name=name, text="", ok=True)

    monkeypatch.setattr(engine, "run_subagent", flaky)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    # OPEN-33's half: the 500 did not end the run.
    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    # OPEN-46 §6 option C's half: the coder never ran, so the task had ONE try.
    assert task.attempts == 1, "an invocation the provider never served is not a try"
    assert task.run_errors == ("the coder could not run: Error code: 500 - internal",)


async def test_errors_separated_by_a_success_do_not_accumulate(monkeypatch, context):
    """The budget counts CONSECUTIVE errors, so it measures "the provider is
    down" rather than "this run has been unlucky twice in an hour"."""
    from rudra.loop.engine import MAX_CONSECUTIVE_RUN_ERRORS

    script = []
    for _ in range(MAX_CONSECUTIVE_RUN_ERRORS + 2):
        script.append(SubagentResult(name="coder", text="", ok=False, error="Error code: 500"))
        script.append(SubagentResult(name="coder", text="wrote it", ok=True))

    async def alternating(name, prompt, *, context, thread_id=None):
        return script.pop(0) if script else SubagentResult(name=name, text="", ok=True)

    monkeypatch.setattr(engine, "run_subagent", alternating)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())

    ledger = Ledger()
    outcomes = []
    for index in range(MAX_CONSECUTIVE_RUN_ERRORS + 1):
        task = ledger.add(f"task {index}")
        outcomes.append(await run_task(task, ledger, context=context))

    assert all(outcome is Outcome.DONE for outcome in outcomes), outcomes


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


async def test_an_exhausted_task_is_one_continue_will_not_retry(monkeypatch, context):
    """OPEN-154 closed docs-only: `06-troubleshooting.md` tells a user that a
    task ending `N attempts exhausted` may have been converging, that
    `--continue` will not give it another attempt, and that the lever is
    `max_fix_attempts` on a fresh run. Every clause is pinned here, because a
    page restating code is how OPEN-152's sentence drifted (lesson 4).

    It also pins the phrase the page tells a reader to grep the debug log for:
    each retry's dispatch carries the gate it is answering."""
    lines = iter([1, 2, 3, 4, 5])
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    outcome, task, ledger = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.status is TaskStatus.BLOCKED
    assert task.note.startswith(f"{context.cfg.agent.max_fix_attempts} attempts exhausted")
    assert task not in ledger.resumable()
    assert "Your previous attempt did not pass verification" in engine._coder_prompt(task, "x")


async def test_a_coder_that_writes_nothing_is_a_failed_attempt(monkeypatch, context):
    """Spec §5, narrowed by OPEN-27 to what it always actually meant.

    The original comment stated the mechanism exactly: "with no changed files
    the gate's syntax stage reports '0 files parsed' and stubs '0 changed
    file(s) scanned', so a green report would mark an untouched task DONE".
    That is a report which is green because it JUDGED NOTHING -- and the stub
    was `passing_report()`, whose test stage passed, which is not that report.

    Since OPEN-27 the two are distinguished, so the stub has to be the one
    the description names. A vacuous gate still blocks, which is this test.
    A gate that genuinely ran the tests and passed does not, which is
    `test_an_empty_diff_passes_when_the_gate_is_green_and_tests_judged` --
    the case where an earlier task had already done this one's work.
    """
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: vacuous_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.status is not TaskStatus.DONE
    assert "wrote nothing" in task.note


async def test_a_coder_that_writes_nothing_outside_a_repo_is_a_failed_attempt(monkeypatch, context):
    """OPEN-13, end to end and without patching the answer.

    The test above monkeypatches `changed_since`, so it never exercised the
    guard's own condition. That condition was
    `before is not None and not files_touched`, and outside a git repository
    `before` was None -- so the guard never ran, the gate saw zero changed
    files and passed vacuously ("0 changed file(s) scanned"), and the task
    was marked DONE. Measured on the owner's 2026-08-24 run in
    `test-rudra`: two tasks `done`, `files_touched: []`, no file written.

    `context.project_path` is a bare tmp_path with no `.git`, and the real
    snapshot functions are restored over the autouse fakes.

    The gate is stubbed `vacuous_report()` rather than `passing_report()`,
    and that is a correction rather than a concession (OPEN-27). This
    scenario is a project with no files, and the REAL gate cannot return a
    passing test stage for one -- measured 2026-08-26 against
    `verify_project` on an empty directory:

        syntax     not_applicable  'no JavaScript files changed'
        lint       not_applicable  'no stack detected'
        typecheck  not_applicable  'no stack detected'
        test       not_applicable  'this project declares no test command'
        stubs      passed          '0 changed file(s) scanned'

    `passing_report()` described a world this test cannot be in. Since
    OPEN-27 an empty diff asks the gate instead of assuming, so the stub has
    to be one the gate could actually produce -- and with the faithful one
    the task still blocks, which is what this test is for.
    """
    monkeypatch.setattr(engine, "git_snapshot", real_git_snapshot)
    monkeypatch.setattr(engine, "changed_since", real_changed_since)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: vacuous_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.status is not TaskStatus.DONE
    assert "wrote nothing" in task.note


async def test_a_coder_that_writes_outside_a_repo_is_seen(monkeypatch, context):
    """The other direction: real work in a non-repo project must still pass."""
    monkeypatch.setattr(engine, "git_snapshot", real_git_snapshot)
    monkeypatch.setattr(engine, "changed_since", real_changed_since)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())

    project = context.project_path

    async def writing_coder(name, prompt, *, context, thread_id=None):
        # `context` here is the SubagentContext, not the LoopContext, so the
        # project path is closed over rather than read off it.
        (project / "app.py").write_text("x = 1\n", encoding="utf-8")
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", writing_coder)

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.files_touched == ("app.py",)


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


# --- OPEN-35: a reviewer that was cut short must say so -------------------


def recording_console(context):
    """A console the test can read back. The fixture's is quiet=True."""
    context.console = Console(record=True, width=200)
    return context.console


async def test_a_halted_reviewer_reports_why_instead_of_printing_nothing(monkeypatch, context):
    """OPEN-35's second half. `run_subagent` returns ok=False with a
    halted_reason and text="" when a guard fires before the reviewer has
    emitted prose. review_once read only `.text`, so run 36023bb8bdd1
    printed NOTHING about a reviewer that had been killed mid-read -- the
    "agent crashed without completing its task" the user saw was a guard
    firing into a console.print that never happened.

    The coder path already records this (engine.py, `result.halted_reason`
    -> task.note); the reviewer had no equivalent.
    """

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name,
            text="",
            ok=False,
            halted_reason="'read_file' on '/tests/integration/test_api.py' repeated 3x -- stopping",
        )

    monkeypatch.setattr(engine, "run_subagent", halting)
    console = recording_console(context)

    await engine.review_once(context, Ledger())

    printed = console.export_text()
    assert "Review incomplete" in printed
    assert "repeated 3x" in printed


async def test_a_reviewer_that_errored_reports_the_error(monkeypatch, context):
    """ok=False arrives two ways -- halted_reason for a guard, error for a
    build failure or a provider 500. Both used to print nothing."""

    async def failing(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="Error code: 500")

    monkeypatch.setattr(engine, "run_subagent", failing)
    console = recording_console(context)

    await engine.review_once(context, Ledger())

    assert "Error code: 500" in console.export_text()


async def test_partial_review_prose_is_printed_alongside_the_reason(monkeypatch, context):
    """A guard can fire after the reviewer has said something useful. Print
    both: the findings it managed, and why there are no more."""

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name,
            text="src/models.py mixes the model and the schema.",
            ok=False,
            halted_reason="80 tool calls in one invocation -- stopping.",
        )

    monkeypatch.setattr(engine, "run_subagent", halting)
    console = recording_console(context)

    await engine.review_once(context, Ledger())

    printed = console.export_text()
    assert "src/models.py mixes" in printed
    assert "Review incomplete" in printed


async def test_a_clean_review_prints_no_reason_line(monkeypatch, context):
    """The advisory pass is the common case and must stay quiet about
    machinery that did not fire."""

    async def clean(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="Findings: none.", ok=True)

    monkeypatch.setattr(engine, "run_subagent", clean)
    console = recording_console(context)

    await engine.review_once(context, Ledger())

    printed = console.export_text()
    assert "Findings: none." in printed
    assert "incomplete" not in printed


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


async def test_a_no_op_retry_note_says_what_the_coder_was_asked_to_fix(monkeypatch, context):
    """OPEN-23's third part. "the coder wrote nothing" is true and causally
    misleading -- it sends the next reader hunting a lazy coder. Measured
    2026-08-26: four consecutive tasks carried it while behaving correctly,
    having been handed a blocker in a file they did not own. The blocker is
    what makes the note diagnosable."""
    from rudra.loop.engine import _wrote_nothing_note

    assert _wrote_nothing_note("") == "the coder wrote nothing"
    note = _wrote_nothing_note("test failed: 27 run, 3 failed\n  tests/test_cli.py:119: boom")
    assert "wrote nothing" in note, "the BLOCKED path greps for this substring (engine.py:411)"
    assert "tests/test_cli.py:119" in note


# --------------------------------------------------------------------------
# OPEN-23: a task answers for what it broke, not for what it inherited
# --------------------------------------------------------------------------

CLI_FINDINGS = (Finding("tests/test_cli.py", 119, "AssertionError"),)
STORAGE_FINDINGS = (Finding("todo/storage.py", 96, "FileNotFoundError"),)


async def test_a_task_that_inherits_every_failure_passes(monkeypatch, context):
    """`t3` of run `ee29dd3ebf51`: it implemented load-from-file correctly and
    was blocked three times by `tests/test_cli.py`, a file written by an
    earlier task's tester against a CLI that task `t8` had not built yet."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    assert task.attempts == 1, "no attempt is spent on someone else's failure"


async def test_the_pass_says_why_and_names_the_failures(monkeypatch, context):
    """A silent pass on a red suite is worse than a block: the next reader has
    no way to learn the gate was failing."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})

    _, task, _ = await run_one(context)

    assert "introduced no failure" in task.note
    assert "tests/test_cli.py:119" in task.note


async def test_a_new_failure_still_blocks_even_with_a_baseline(monkeypatch, context):
    """The invariant OPEN-23 says must survive. `t2` of the same run: its
    findings mixed the pre-existing CLI failures with a real storage bug."""
    report = failing_report((*CLI_FINDINGS, *STORAGE_FINDINGS))
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: report)
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    # Two, not max_fix_attempts: the failure signature repeats identically,
    # so the no-progress rule stops it first (C6.5a). What matters here is
    # that a retry was spent at all -- the inherited case never spends one.
    assert task.attempts > 1


async def test_a_regression_in_an_untouched_file_still_blocks(monkeypatch, context):
    """Nothing consults files_touched, deliberately: in the measured run
    `tests/test_cli.py` failed THROUGH a change to `todo/storage.py`, so a
    rule keyed on which file a finding names would have passed it."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))
    context.failure_baseline = frozenset({"todo/storage.py:96"})

    outcome, _, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED


async def test_a_failing_gate_updates_what_the_next_task_inherits(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))

    await run_one(context)

    assert context.failure_baseline == frozenset({"tests/test_cli.py:119"})


async def test_a_passing_gate_clears_the_baseline(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})

    await run_one(context)

    assert context.failure_baseline == frozenset()


async def test_a_regression_introduced_on_attempt_one_is_not_inherited_by_attempt_two(
    monkeypatch, context
):
    """The reason the baseline is captured once per TASK and not per attempt.
    Refreshing it inside the fix loop would launder a regression the task
    itself introduced into an inheritance on the very next attempt, and the
    task would pass on exactly the failure it caused."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(STORAGE_FINDINGS))
    context.failure_baseline = frozenset()

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.attempts > 1, "the task was retried on its own regression"


async def test_with_no_baseline_a_failure_blocks_exactly_as_before(monkeypatch, context):
    """A fresh process -- the first task, or `--continue` -- has no baseline.
    The degraded mode must be the old blocking one, never the passing one."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))

    outcome, _, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED


# --------------------------------------------------------------------------
# OPEN-27: an empty diff is a failure only if nothing can confirm otherwise
# --------------------------------------------------------------------------


def vacuous_report():
    """Green, and green because it judged nothing.

    The OPEN-12/13 shape: a project with no files parses no syntax, collects
    no tests, and scans no stubs. Passing a task on this is how two tasks
    were once marked DONE having created no file.
    """
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=NOT_APPLICABLE, blocking=True),
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
def wrote_nothing(monkeypatch):
    """A coder that changes no file, which is what `t2`-`t5` of run3 did."""
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())


async def test_an_empty_diff_passes_when_the_gate_is_green_and_tests_judged(
    monkeypatch, context, wrote_nothing
):
    """Run `6ec092f525e7`: `t1` built add/list/complete/remove for real, so
    `t2` found its work done and wrote nothing -- correctly -- and was
    blocked for it. The project was complete and its tests passed."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    assert task.attempts == 1, "no attempt is spent re-asking for work that exists"


async def test_that_pass_says_it_wrote_nothing_and_why_that_was_fine(
    monkeypatch, context, wrote_nothing
):
    """This item's own lesson: three defects have now worn the same note.
    A reader must be able to tell which one they are looking at."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())

    _, task, _ = await run_one(context)

    assert "wrote nothing" in task.note
    assert "already" in task.note.lower()


async def test_a_green_but_vacuous_gate_still_blocks(monkeypatch, context, wrote_nothing):
    """OPEN-12/13, kept closed. A project with no files collects no tests, so
    "green" means the gate judged nothing -- which is not evidence of
    anything. Passing here is exactly the defect that guard was built for."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: vacuous_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert "wrote nothing" in task.note


async def test_the_vacuous_block_says_the_project_could_not_confirm_it(
    monkeypatch, context, wrote_nothing
):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: vacuous_report())

    _, task, _ = await run_one(context)

    assert "no tests" in task.note.lower()


async def test_an_empty_diff_with_a_failing_gate_still_blocks(monkeypatch, context, wrote_nothing):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED


async def test_an_empty_diff_riding_someone_elses_red_suite_still_passes(
    monkeypatch, context, wrote_nothing
):
    """OPEN-23's baseline composes with this one: a failure that predates the
    task is not evidence against it, here either."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})

    outcome, _, _ = await run_one(context)

    assert outcome is Outcome.DONE


async def test_an_empty_diff_that_broke_something_new_still_blocks(
    monkeypatch, context, wrote_nothing
):
    """Belt and braces. A coder cannot break a suite without writing a file,
    so this should be unreachable -- but the rule must not depend on that."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))

    outcome, _, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED


async def test_a_task_that_wrote_something_is_unaffected(monkeypatch, context):
    """The ordinary path keeps its own behaviour: files written, gate green,
    DONE with an empty note."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.files_touched == ("a.py",)
    assert task.note == ""


async def test_a_guard_halt_survives_a_task_that_then_passes(monkeypatch, context):
    """OPEN-44. `run_subagent` reported the halt correctly; `task.note` was
    the only place it landed, and the passing branch clears that field
    unconditionally (`task.note = "" if verdict is PASSED`). So a halt
    survived exactly when the task ALSO failed, and vanished in the case a
    reader most needs it -- run bf6be7525991 halted 6 of 6 coder
    invocations and its ledger records none of it.
    """

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name,
            text="",
            ok=False,
            halted_reason="'write_file' on '/DONE' repeated 3x -- stopping",
        )

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.note == "", "the passing note is unchanged -- a model reads that field"
    assert task.halts == ("'write_file' on '/DONE' repeated 3x -- stopping",)


async def test_a_guard_halt_survives_a_task_that_wrote_nothing(monkeypatch, context):
    """The other branch that overwrites the note: `_already_satisfied_note`
    on an empty diff the gate confirms needed no work (OPEN-27)."""

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name, text="", ok=False, halted_reason="80 tool calls in one invocation"
        )

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.halts == ("80 tool calls in one invocation",)


async def test_every_halted_attempt_is_recorded_not_just_the_last(monkeypatch, context):
    """A task gets `max_fix_attempts` coder invocations and each can halt."""
    reports = [failing_report(), passing_report()]
    reasons = iter(["first halt", "second halt"])

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, halted_reason=next(reasons))

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    _, task, _ = await run_one(context)

    assert task.halts == ("first halt", "second halt")


async def test_a_guard_halt_is_printed_when_it_fires(monkeypatch, context):
    """The terminal said nothing at all about six killed coders. One line,
    at the moment it happens -- the same report review_once already makes
    for the reviewer (OPEN-35)."""

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name, text="", ok=False, halted_reason="'write_file' on '/DONE' repeated 3x"
        )

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    console = recording_console(context)

    await run_one(context)

    printed = console.export_text()
    assert "coder" in printed
    assert "repeated 3x" in printed


async def test_a_halted_tester_is_recorded_too(monkeypatch, context):
    """The tester's result was discarded outright: `await run_subagent(...)`
    with nothing bound. A guard that kills the tester mid-run left no trace
    anywhere."""
    results = {
        "coder": SubagentResult(name="coder", text="wrote it", ok=True),
        "tester": SubagentResult(
            name="tester", text="", ok=False, halted_reason="3 consecutive tool failures"
        ),
    }
    reports = [no_test_judgement_report(), passing_report()]

    async def by_name(name, prompt, *, context, thread_id=None):
        return results[name]

    monkeypatch.setattr(engine, "run_subagent", by_name)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.halts == ("3 consecutive tool failures",)


async def test_a_clean_task_records_no_halt(monkeypatch, context):
    """The common case stays quiet: an empty tuple, and nothing printed."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    console = recording_console(context)

    _, task, _ = await run_one(context)

    assert task.halts == ()
    assert "stopped early" not in console.export_text()


# --- OPEN-46 §5.3: a subagent that never ran must leave a durable record -----


async def test_a_run_error_is_kept_in_a_list_not_only_in_the_note(monkeypatch, context):
    """The failure this repeats is OPEN-44's, one field over.

    `task.note` is the only place a run error has ever landed, and every
    branch that finishes a task rewrites it -- the passing one clears it
    outright. run14's t8 exhausted the retry budget twice and the only
    surviving evidence anywhere was a note that a later attempt overwrote.
    """

    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(
            name=name,
            text="",
            ok=False,
            error="Provider error from the coder model after 4 attempt(s): NotFoundError (404).",
        )

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    _, task, _ = await run_one(context)

    # Three attempts, three errors, each one kept.
    assert len(task.run_errors) == 3
    assert all("after 4 attempt(s)" in entry for entry in task.run_errors)


async def test_a_run_error_survives_the_task_finishing(monkeypatch, context):
    """The exact case `note` loses: the coder fails once, then succeeds,
    and the passing branch clears the note. The record must remain."""
    attempts = {"n": 0}

    async def flaky(name, prompt, *, context, thread_id=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return SubagentResult(name=name, text="", ok=False, error="provider gave up")
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", flaky)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    assert task.run_errors == ("the coder could not run: provider gave up",)


async def test_a_task_whose_subagents_all_ran_records_no_run_error(monkeypatch, context):
    """Zero is the honest answer on a healthy run, and it must be zero."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    _, task, _ = await run_one(context)
    assert task.run_errors == ()


async def test_a_tester_that_never_ran_is_recorded_too(monkeypatch, context):
    """The same silence one dispatch site over, and this one is worse.

    The coder's error branch at least writes `note`. The tester's result
    goes only to `_record_halt`, which returns early unless
    `halted_reason` is set -- so a tester whose model call exhausted the
    retry budget was recorded in NO field at all, and the task went on to
    pass with the gate reporting no test judgement.
    """
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    async def coder_ok_tester_dead(name, prompt, *, context, thread_id=None):
        if name == "tester":
            return SubagentResult(
                name=name,
                text="",
                ok=False,
                error="Provider error from the tester model after 4 attempt(s).",
            )
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder_ok_tester_dead)
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE, "a dead tester does not fail the task"
    assert task.run_errors == (
        "the tester could not run: Provider error from the tester model after 4 attempt(s).",
    )


async def test_a_tester_that_ran_records_no_run_error(monkeypatch, context):
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    _, task, _ = await run_one(context)
    assert task.run_errors == ()


# --- OPEN-46 §6 option C: an invocation the provider never served ----------
# is not a try the TASK had. run14's t8 spent all three of its attempts on a
# coder that never ran once -- "after 4 attempt(s): NotFoundError (404).
# Nothing was written." -- and the task was lost to a provider outage rather
# than to anything about the work. The retry budget is for a coder that
# produced a turn and got it wrong.


async def test_a_provider_failure_does_not_spend_a_task_attempt(monkeypatch, context):
    """The whole of option C. The coder never produced a turn, so the task
    has not had a try yet and must not be charged for one."""
    calls = {"n": 0}

    async def flaky(name, prompt, *, context, thread_id=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            return SubagentResult(name=name, text="", ok=False, error="provider gave up")
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", flaky)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    # Three dispatches, two of them never served. The task had ONE try.
    assert calls["n"] == 3
    assert task.attempts == 1
    # And both failures are still on the record -- C removes the charge, not
    # the evidence (OPEN-46 §5).
    assert len(task.run_errors) == 2


async def test_the_run_error_ceiling_still_bounds_the_loop(monkeypatch, context):
    """Not counting the attempt must not make the loop unbounded. The ceiling
    that stops it is MAX_CONSECUTIVE_RUN_ERRORS, which already existed --
    which is why option C is small."""

    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="provider gave up")

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert len(task.run_errors) == engine.MAX_CONSECUTIVE_RUN_ERRORS
    # Zero, not three: the coder never ran, so the task never tried.
    assert task.attempts == 0


async def test_a_task_stopped_by_a_dead_provider_resumes_with_its_budget(monkeypatch, context):
    """The knock-on, and it is the point. `_stop` leaves a STOP_RUN task
    PENDING so `--continue` picks it up (A1.93). Before C it came back having
    already burned its attempts on an endpoint that never answered."""

    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="provider gave up")

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    _, task, _ = await run_one(context)

    assert task.status is TaskStatus.PENDING
    assert task.attempts < context.cfg.agent.max_fix_attempts


async def test_a_real_attempt_is_still_charged(monkeypatch, context):
    """The other half. A coder that RAN and got it wrong spends its attempt;
    C must not turn the fix loop into an unbounded one.

    Two and not `max_fix_attempts`: the same gate failure twice in a row is
    the no-progress rule (C6.5a), which stops sooner than the budget does.
    That it stops there at all is what says the attempt was charged."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.attempts == 2


async def test_two_unserved_dispatches_never_share_a_thread(monkeypatch, context):
    """A thread id keyed on `task.attempts` alone would REPEAT once the
    attempt stops being spent, and the checkpointer would resume whatever
    partial state the failed invocation left. The dispatch counter is what
    keeps them apart."""
    seen: list[str] = []
    calls = {"n": 0}

    async def flaky(name, prompt, *, context, thread_id=None):
        calls["n"] += 1
        if name == "coder":
            seen.append(thread_id)
        if calls["n"] <= 2:
            return SubagentResult(name=name, text="", ok=False, error="provider gave up")
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", flaky)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    await run_one(context)

    assert len(seen) == 3
    assert len(set(seen)) == 3, f"thread ids repeated: {seen}"


# --------------------------------------------------------------------------
# OPEN-93 Option D: the blocker says when the TEST's path is what is wrong
# --------------------------------------------------------------------------


def _test_stage_blocker(tail: str, findings=()):
    return VerifyReport.from_stages(
        [
            StageResult(
                name="test",
                outcome=FAILED,
                blocking=True,
                findings=tuple(findings),
                output_tail=tail,
                detail="8 run, 8 failed, 0 skipped",
            )
        ]
    )


def test_the_blocker_names_the_tests_own_path_when_the_file_is_really_there(tmp_path):
    """Run `2cde3406f7d6` verbatim. The HTML was correct and complete on disk;
    the test looked for it at the machine's root. Attempt 3 read that blocker,
    saw nothing saying so, and rewrote 480 lines of already-correct HTML."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("<!DOCTYPE html>")
    (tmp_path / "tests").mkdir()
    tail = (
        "E   AssertionError: HTML file not found at /src/iphone15.html\n"
        "E   FileNotFoundError: [Errno 2] No such file or directory: "
        "'/src/iphone15.html'\n"
    )
    report = _test_stage_blocker(
        tail, [Finding(file="tests/test_iphone15.py", line=14, message="AssertionError")]
    )
    text = engine._blocker_text(report, project_path=tmp_path)

    assert text.startswith("test failed:")
    assert "/src/iphone15.html" in text
    assert "src/iphone15.html" in text
    assert "tests/test_iphone15.py" in text


def test_the_blocker_is_unchanged_when_the_path_does_not_resolve(tmp_path):
    """The note claims the file IS there under the relative spelling. When it
    is not, the failure is an ordinary missing file and saying otherwise
    would send the coder after a test that is right."""
    (tmp_path / "src").mkdir()
    tail = "E   FileNotFoundError: '/src/iphone15.html'\n"
    report = _test_stage_blocker(tail)
    assert engine._blocker_text(report, project_path=tmp_path) == engine._blocker_text(report)


def test_the_blocker_is_unchanged_for_a_real_machine_path(tmp_path):
    """`/usr/bin/env` names no directory this project has, so nothing is
    claimed about it -- `find_project_absolute_literals`' own rule."""
    (tmp_path / "src").mkdir()
    tail = "E   FileNotFoundError: '/usr/bin/env'\n"
    report = _test_stage_blocker(tail)
    assert engine._blocker_text(report, project_path=tmp_path) == engine._blocker_text(report)


def test_only_the_test_stage_gets_the_note(tmp_path):
    """A lint or typecheck failure quoting a path is not this defect."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("x")
    report = VerifyReport.from_stages(
        [
            StageResult(
                name="lint",
                outcome=FAILED,
                blocking=True,
                output_tail="bad path '/src/iphone15.html'",
            )
        ]
    )
    assert engine._blocker_text(report, project_path=tmp_path) == engine._blocker_text(report)


def test_the_note_is_appended_after_the_gates_own_words(tmp_path):
    """`_blocker_text` quotes the gate verbatim and a paraphrase is a worse
    input. The note is an addition, never a replacement."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("x")
    tail = "E   FileNotFoundError: '/src/iphone15.html'"
    report = _test_stage_blocker(tail)
    text = engine._blocker_text(report, project_path=tmp_path)
    assert text.index(tail) < text.index("Note:")


def test_no_project_path_leaves_the_blocker_alone(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("x")
    report = _test_stage_blocker("E   FileNotFoundError: '/src/iphone15.html'")
    assert "Note:" not in engine._blocker_text(report)


def test_the_blocker_note_fires_on_an_unquoted_pytest_message(tmp_path):
    """Run `2cde3406f7d6`'s tail quotes the path on the FileNotFoundError line
    and not on the AssertionError line. Both are the same defect, so the note
    reads prose rather than a language's string grammar."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("x")
    report = _test_stage_blocker(
        "",
        [
            Finding(
                file="tests/test_iphone15.py",
                line=14,
                message="AssertionError: HTML file not found at /src/iphone15.html",
            )
        ],
    )
    text = engine._blocker_text(report, project_path=tmp_path)
    assert "Note:" in text
    assert "/src/iphone15.html" in text


# --- OPEN-119: a test blocker's frames, with the exception dropped ----------
# Run `a04f89bd2ed6`'s t5 went BLOCKED after three attempts, `files_touched:
# []`, and its last words were "Let me check what the actual error is more
# carefully." The coder had been sent three frame lines and no exception:
# `_parse_findings` matches pytest's `path:line: in <func>` frames, and
# `_blocker_text` appended the tail only when there were NO findings. Every
# earlier pin here built its findings by hand, with the exception already in
# the message -- which pytest never prints on a frame line -- so these run the
# real parser over real pytest output.

# t5's gate output from `=== ERRORS ===` on, verbatim (verify.log).
_COLLECTION_ERROR_TAIL = """\
==================================== ERRORS ====================================
________________ ERROR collecting tests/unit/test_todo_model.py ________________
tests/unit/test_todo_model.py:5: in <module>
    from src.models import Todo
src/models.py:14: in <module>
    class Todo(Base):
src/models.py:19: in Todo
    id = Column(Integer, primary_key=True, index=True)
         ^^^^^^
E   NameError: name 'Column' is not defined
=========================== short test summary info ============================
ERROR tests/unit/test_todo_model.py - NameError: name 'Column' is not defined
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.11s ===============================
"""

# pytest 9's default long format for two failing tests, the shape of t8's
# first blocker (eight `tests/unit/test_todo_model.py:<n>: AttributeError`
# findings and no message). Produced by real pytest, trimmed to two failures.
_FAILED_TESTS_TAIL = """\
=================================== FAILURES ===================================
____________________________________ test_0 ____________________________________

    def test_0():
>       assert Todo.create_0() is not None
               ^^^^^^^^^^^^^
E       AttributeError: type object 'Todo' has no attribute 'create_0'

tests/unit/test_todo_model.py:5: AttributeError
____________________________________ test_1 ____________________________________

    def test_1():
>       assert Todo.create_1() is not None
               ^^^^^^^^^^^^^
E       AttributeError: type object 'Todo' has no attribute 'create_1'

tests/unit/test_todo_model.py:8: AttributeError
=========================== short test summary info ============================
FAILED tests/unit/test_todo_model.py::test_0 - AttributeError: type object 'T...
FAILED tests/unit/test_todo_model.py::test_1 - AttributeError: type object 'T...
============================== 2 failed in 0.03s ===============================
"""


def _gate_blocker(name: str, tail: str):
    """A failing stage exactly as `verify/pipeline.py` builds one: findings
    parsed from the same tail it carries."""
    return VerifyReport.from_stages(
        [
            StageResult(
                name=name,
                outcome=FAILED,
                blocking=True,
                findings=_parse_findings(tail),
                output_tail=tail,
                detail="1 run, 1 failed, 0 skipped",
            )
        ]
    )


def test_a_collection_error_blocker_carries_the_exception():
    report = _gate_blocker("test", _COLLECTION_ERROR_TAIL)
    assert report.blocker.findings, "the frames must parse, or this is not OPEN-119's shape"

    text = engine._blocker_text(report)

    assert text.startswith("test failed: 1 run, 1 failed, 0 skipped")
    assert "E   NameError: name 'Column' is not defined" in text


def test_a_failed_test_blocker_carries_every_exception_message():
    report = _gate_blocker("test", _FAILED_TESTS_TAIL)
    assert len(report.blocker.findings) == 2

    text = engine._blocker_text(report)

    assert "AttributeError: type object 'Todo' has no attribute 'create_0'" in text
    assert "AttributeError: type object 'Todo' has no attribute 'create_1'" in text


def test_a_test_blocker_quotes_each_frame_once():
    """The findings are parsed FROM the tail (`verify/pipeline.py`'s test
    stage), so listing them beside it would only say every frame twice."""
    text = engine._blocker_text(_gate_blocker("test", _COLLECTION_ERROR_TAIL))
    assert text.count("src/models.py:19: in Todo") == 1


def test_a_typecheck_blocker_still_lists_its_findings_alone():
    """mypy's finding line IS its message, so the tail would add only noise.
    OPEN-119 is about a test runner's frames, and nothing else changes."""
    tail = (
        'src/app.py:3: error: Name "x" is not defined  [name-defined]\n'
        "Found 1 error in 1 file (checked 1 source file)\n"
    )
    text = engine._blocker_text(_gate_blocker("typecheck", tail))
    assert 'src/app.py:3: error: Name "x" is not defined' in text
    assert "Found 1 error" not in text


# --- OPEN-98 option C: a tester that finished cleanly and tested nothing ----
# Run `2cde3406f7d6`'s tester wrote its test file, said "Wait, I made an error.
# The file path should be relative to the project root, not absolute. Let me
# check the project structure." -- and the turn ended, because that message
# carried no tool call. `subagent_done` recorded `ok: true`, no halt, no
# error. Its histogram was {"execute": 17, "ls": 2, "read_file": 2,
# "write_file": 1}: ZERO `run_tests`, the tool that is its whole job.
#
# `_record_halt` returns early unless a guard fired, and the error branch
# above needs `result.error`. Neither applied, so the invocation appears in
# NO field of the ledger. That is CLAUDE.md 8a failure shape 4 -- the record
# that is never written -- and it is why this item had to be reconstructed
# from 678 lines of JSONL.


async def test_a_tester_that_never_called_run_tests_is_recorded(monkeypatch, context):
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    async def tester_stops_mid_thought(name, prompt, *, context, thread_id=None):
        if name == "tester":
            # The measured histogram, verbatim from the archive.
            return SubagentResult(
                name=name,
                text="Wait, I made an error. Let me check the project structure.",
                ok=True,
                tools={"execute": 17, "ls": 2, "read_file": 2, "write_file": 1},
            )
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", tester_stops_mid_thought)
    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE, "an untested task is not a failed one"
    assert task.run_errors == ("the tester ended without calling run_tests",)


async def test_a_tester_that_called_run_tests_records_nothing(monkeypatch, context):
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    async def tester_did_its_job(name, prompt, *, context, thread_id=None):
        if name == "tester":
            return SubagentResult(
                name=name,
                text="8 tests, all passing.",
                ok=True,
                tools={"write_file": 1, "run_tests": 1},
            )
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", tester_did_its_job)
    _, task, _ = await run_one(context)

    assert task.run_errors == ()


async def test_a_result_carrying_no_histogram_records_nothing(monkeypatch, context):
    """ "Did not happen" and "was not tried" are different answers.

    `tools=None` is a result that measured nothing -- every stand-in in this
    suite, and any result built by hand. Reading it as "called no run_tests"
    would make this record fire on the test harness rather than on a run,
    which is TODO.md's second watched habit exactly.
    """
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    _, task, _ = await run_one(context)
    assert task.run_errors == ()


async def test_a_halted_tester_is_not_also_reported_as_untested(monkeypatch, context):
    """A halt already says the invocation was stopped, and `halts` carries
    it. Saying it twice in two vocabularies makes a reader count one event
    as two."""
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    async def tester_halted(name, prompt, *, context, thread_id=None):
        if name == "tester":
            return SubagentResult(
                name=name,
                text="",
                ok=False,
                halted_reason="80 tool calls in one invocation -- stopping.",
                tools={"glob": 80},
            )
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", tester_halted)
    _, task, _ = await run_one(context)

    assert task.run_errors == ()
    assert task.halts == ("80 tool calls in one invocation -- stopping.",)


async def test_the_untested_record_is_appended_beside_a_provider_failure(monkeypatch, context):
    """OPEN-46's rule: `run_errors` is append-only. Two different events on
    one task must both survive, in order."""
    from rudra.loop.engine import _record_run_error

    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    async def tester_stops_mid_thought(name, prompt, *, context, thread_id=None):
        if name == "tester":
            return SubagentResult(name=name, text="Let me check.", ok=True, tools={"ls": 2})
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", tester_stops_mid_thought)

    ledger = Ledger()
    task = ledger.add("write the parser")
    _record_run_error(task, "coder", "Error code: 500 - internal")
    await run_task(task, ledger, context=context)

    assert task.run_errors == (
        "the coder could not run: Error code: 500 - internal",
        "the tester ended without calling run_tests",
    )


# --- OPEN-120: the project's .venv is synced before the gate reads it -------


async def test_the_project_env_is_synced_before_every_gate_run(monkeypatch, context):
    order: list[str] = []

    def fake_ensure(project_path, **kwargs):
        order.append("sync")
        return "ok"

    def fake_verify(*args, **kwargs):
        order.append("verify")
        return passing_report()

    monkeypatch.setattr(engine, "ensure_project_env", fake_ensure)
    monkeypatch.setattr(engine, "verify_project", fake_verify)
    ledger = Ledger()
    task = ledger.add("write it")

    assert await run_task(task, ledger, context=context) is Outcome.DONE
    assert order[:2] == ["sync", "verify"]
    assert order.count("sync") == order.count("verify")


async def test_a_failed_dependency_install_reaches_the_blocker(monkeypatch, context):
    """The coder holds no shell. A bad pin it cannot see is a missing module
    it will try to fix in code for three attempts."""
    from rudra.testing.project_env import ProjectEnvState

    context.project_env = ProjectEnvState(
        failure="ERROR: No matching distribution found for flask==99"
    )
    monkeypatch.setattr(engine, "ensure_project_env", lambda *a, **k: "failed")
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    ledger = Ledger()
    task = ledger.add("write it")

    assert await run_task(task, ledger, context=context) is Outcome.BLOCKED
    assert "installing the project's declared dependencies" in task.note
    assert "flask==99" in task.note


async def test_a_sync_that_raises_never_ends_the_run(monkeypatch, context):
    def broken(*args, **kwargs):
        raise RuntimeError("a bug in the sync")

    monkeypatch.setattr(engine, "ensure_project_env", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    ledger = Ledger()
    task = ledger.add("write it")

    assert await run_task(task, ledger, context=context) is Outcome.DONE


def test_the_blocker_gains_nothing_when_no_install_failed(context):
    assert engine._env_sync_note(context) == ""


# --- OPEN-123: `files_touched` is every attempt's, and so is the gate's scope --


def _real_diff(monkeypatch):
    """The real snapshot and diff, and a .venv sync that does nothing: what the
    loop RECORDS is the question, so nothing may answer it for the test."""
    monkeypatch.setattr(engine, "git_snapshot", real_git_snapshot)
    monkeypatch.setattr(engine, "changed_since", real_changed_since)
    monkeypatch.setattr(engine, "ensure_project_env", lambda *a, **k: "ok")


def _scripted_subagents(monkeypatch, project, coder_writes, tester_writes=None):
    """A coder whose Nth dispatch writes `coder_writes[N-1]` -- `{path: text}`
    -- and nothing once the script runs out; a tester that writes
    `tester_writes`."""
    dispatched: list[str] = []

    def write(files):
        for relative, text in files.items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    async def subagent(name, prompt, *, context, thread_id=None):
        if name == "coder":
            dispatched.append(prompt)
            if len(dispatched) <= len(coder_writes):
                write(coder_writes[len(dispatched) - 1])
        elif name == "tester":
            write(tester_writes or {})
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", subagent)
    return dispatched


def _gate_sequence(monkeypatch, *reports):
    """A gate answering each call with the next report, the last one repeated."""
    scopes: list[tuple[str, ...]] = []

    def gate(project_path, *, changed_files, **kwargs):
        scopes.append(tuple(changed_files))
        return reports[min(len(scopes), len(reports)) - 1]

    monkeypatch.setattr(engine, "verify_project", gate)
    return scopes


async def test_an_earlier_attempt_s_files_survive_the_attempts_that_wrote_nothing(
    monkeypatch, context
):
    """Run `a04f89bd2ed6`'s t5, reproduced: the first attempt wrote two files,
    attempts 2 and 3 wrote nothing, and the ledger said `files_touched: []`
    with both files on disk -- the line CLAUDE.md 8a calls the highest-signal
    one in the folder, reporting the opposite of what happened. Each attempt
    ASSIGNED the field, so only the last attempt's diff survived."""
    _real_diff(monkeypatch)
    _scripted_subagents(
        monkeypatch,
        context.project_path,
        [{"tests/unit/__init__.py": "", "tests/unit/test_todo_model.py": "x = 1\n"}],
    )
    _gate_sequence(monkeypatch, failing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.attempts == 3
    assert task.files_touched == ("tests/unit/__init__.py", "tests/unit/test_todo_model.py")
    assert "wrote nothing" in task.note, "the empty-diff guard reads the attempt's own diff"


async def test_a_retry_s_gate_still_parses_a_file_an_earlier_attempt_broke(monkeypatch, context):
    """The gate is handed `files_touched`, and `changed_files` scopes the
    SYNTAX stage, not the stub scan alone. With the field overwritten, a file
    attempt 1 broke and attempt 2 did not touch was parsed by no stage:
    reproduced, the task went DONE with `def f(:` on disk."""
    _real_diff(monkeypatch)
    _scripted_subagents(
        monkeypatch,
        context.project_path,
        [{"broken.py": "def f(:\n", "ok.py": "x = 1\n"}, {"ok.py": "x = 2\n"}],
    )
    scopes: list[tuple[str, ...]] = []

    def gate(project_path, *, changed_files, **kwargs):
        scopes.append(tuple(changed_files))
        return VerifyReport.from_stages(
            [
                syntax_stage(project_path, None, changed_files, gate=None, console=None, cfg=None),
                StageResult(name="test", outcome=PASSED, blocking=True),
                StageResult(name="stubs", outcome=PASSED, blocking=True),
            ]
        )

    monkeypatch.setattr(engine, "verify_project", gate)

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.status is not TaskStatus.DONE
    assert scopes[1] == ("broken.py", "ok.py")


async def test_the_tester_s_files_join_the_task_s_rather_than_replace_them(monkeypatch, context):
    """The second assignment site: the tester's diff used to overwrite the
    field too, dropping what the coder's earlier attempts wrote."""
    _real_diff(monkeypatch)
    _scripted_subagents(
        monkeypatch,
        context.project_path,
        [{"a.py": "x = 1\n"}, {"b.py": "y = 2\n"}],
        tester_writes={"tests/test_b.py": "def test_b():\n    assert True\n"},
    )
    _gate_sequence(monkeypatch, failing_report(), no_test_judgement_report(), passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.files_touched == ("a.py", "b.py", "tests/test_b.py")


async def test_a_retry_that_wrote_nothing_and_passes_does_not_claim_the_work_was_in_place(
    monkeypatch, context
):
    """OPEN-27's note is true of a FIRST attempt that wrote nothing: an earlier
    task built this one's work. On a retry it is false -- this task's own
    previous gate did not accept it -- and it hid that."""
    _real_diff(monkeypatch)
    _scripted_subagents(monkeypatch, context.project_path, [{"a.py": "x = 1\n"}])
    _gate_sequence(monkeypatch, failing_report(), passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.attempts == 2
    assert task.files_touched == ("a.py",)
    assert "wrote nothing on a retry" in task.note
    assert "earlier attempt" in task.note
    assert "already in place" not in task.note


# --- OPEN-130: a coder that fails mid-stream still ran ----------------------
#
# Run a4196786280d's t9: dispatch 2 made 50 tool calls, edited
# tests/conftest.py -- the edit that fixed collection -- and then exhausted on
# 429. The loop recorded "the coder could not run", took no diff, and the next
# dispatch re-snapshotted, so the edit reached no `files_touched` and no gate:
# dispatch 4 then read as "wrote nothing".


def _failing_subagents(monkeypatch, project, script):
    """A coder whose Nth dispatch writes `script[N-1][0]` and then returns
    `script[N-1][1]` -- an error string, or None for a served turn. An optional
    third element is the invocation's tool histogram; without it, one
    `edit_file` per write."""
    dispatched: list[str] = []

    async def subagent(name, prompt, *, context, thread_id=None):
        dispatched.append(prompt)
        entry = script[len(dispatched) - 1] if len(dispatched) <= len(script) else ({}, None)
        writes, error, *histogram = entry
        for relative, text in writes.items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        calls = histogram[0] if histogram else ({"edit_file": len(writes)} if writes else {})
        if error:
            return SubagentResult(name=name, text="", ok=False, error=error, tools=calls)
        return SubagentResult(name=name, text="done", ok=True, tools=calls)

    monkeypatch.setattr(engine, "run_subagent", subagent)
    return dispatched


_EXHAUSTED = "Provider error from the model provider: ProviderUnavailable (429)."


async def test_a_mid_stream_failure_s_writes_reach_files_touched(monkeypatch, context):
    """Even when the run then stops on it: the folder a user sends must name
    every file on disk that the task changed (CLAUDE.md 8a shape 4)."""
    _real_diff(monkeypatch)
    _failing_subagents(
        monkeypatch,
        context.project_path,
        [({"tests/conftest.py": "import sys\n"}, _EXHAUSTED), ({}, _EXHAUSTED), ({}, _EXHAUSTED)],
    )
    _gate_sequence(monkeypatch, passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert task.files_touched == ("tests/conftest.py",)


async def test_the_next_served_attempt_is_judged_on_the_failed_invocation_s_writes(
    monkeypatch, context
):
    """t9's dispatch 4: served, wrote nothing itself, and must NOT read as an
    empty diff -- the gate has to judge the edit dispatch 2 made."""
    _real_diff(monkeypatch)
    _failing_subagents(
        monkeypatch, context.project_path, [({"a.py": "x = 1\n"}, _EXHAUSTED), ({}, None)]
    )
    scopes = _gate_sequence(monkeypatch, passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.attempts == 1
    assert scopes == [("a.py",)]
    assert "wrote nothing" not in task.note


async def test_a_failed_invocation_that_did_work_is_not_recorded_as_one_that_never_ran(
    monkeypatch, context
):
    """`could not run` is OPEN-46's sentence for an invocation that never
    started, and a reader goes hunting a dead provider. t9's ran 293.5 s."""
    _real_diff(monkeypatch)
    _failing_subagents(
        monkeypatch, context.project_path, [({"a.py": "x = 1\n"}, _EXHAUSTED), ({}, None)]
    )
    _gate_sequence(monkeypatch, passing_report())

    _, task, _ = await run_one(context)

    (sentence,) = task.run_errors
    assert "could not run" not in sentence
    assert sentence.startswith("the coder stopped mid-run after 1 tool call(s)")
    assert "a.py" in sentence
    assert _EXHAUSTED in sentence


async def test_a_second_failed_invocation_claims_only_what_it_changed(monkeypatch, context):
    """t9's dispatch 3 read two files and failed, after dispatch 2 had edited
    tests/conftest.py and failed. The gate is carried back to dispatch 2's
    snapshot; the sentence is about dispatch 3, and must not claim its edit."""
    _real_diff(monkeypatch)
    _failing_subagents(
        monkeypatch,
        context.project_path,
        [
            ({"a.py": "x = 1\n"}, _EXHAUSTED),
            ({}, _EXHAUSTED, {"read_file": 2}),
            ({}, None),
        ],
    )
    scopes = _gate_sequence(monkeypatch, passing_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert scopes == [("a.py",)]
    assert "wrote nothing" not in task.note, "the carry survives a second failure"
    first, second = task.run_errors
    assert first.startswith("the coder stopped mid-run after 1 tool call(s), having changed a.py")
    assert second.startswith(
        "the coder stopped mid-run after 2 tool call(s), having changed no file"
    )


async def test_a_failure_before_any_tool_call_keeps_the_never_ran_sentence(monkeypatch, context):
    """The OPEN-46 wording stays exactly where it is true."""
    _real_diff(monkeypatch)
    _failing_subagents(
        monkeypatch, context.project_path, [({}, "Error code: 500"), ({"a.py": "x\n"}, None)]
    )
    _gate_sequence(monkeypatch, passing_report())

    _, task, _ = await run_one(context)

    assert task.run_errors == ("the coder could not run: Error code: 500",)


async def test_a_tester_that_failed_mid_run_makes_no_claim_about_files(monkeypatch, context):
    """The tester's diff is not measured where its error is recorded, so the
    sentence says how far it got and nothing about what it changed."""
    ledger = Ledger()
    task = ledger.add("t")

    sentence = engine._record_run_error(task, "tester", "boom", tools={"write_file": 2})

    assert sentence == "the tester stopped mid-run after 2 tool call(s): boom"


# --- OPEN-131: an attempt that wrote nothing still updates the blocker -------
#
# Run a4196786280d: t2's attempts 2 and 3 were sent no failure text at all, and
# t9's dispatches 5-7 were told to fix `ModuleNotFoundError: No module named
# 'main'` after the gate had reported 33 `FixtureDef` errors. The empty-diff
# branch ran the gate and never assigned `blocker_text`.


async def test_a_retry_after_an_attempt_that_wrote_nothing_is_told_why(
    monkeypatch, context, wrote_nothing
):
    """t2: nothing written, gate failing -- the retry must say what failed."""
    prompts = _scripted_subagents(monkeypatch, context.project_path, [])
    _gate_sequence(monkeypatch, failing_report((Finding("models.py", 4, "gate failure"),)))

    await run_one(context)

    assert len(prompts) == 3
    assert "did not pass verification" in prompts[1]
    assert "models.py:4: gate failure" in prompts[1]


async def test_a_retry_is_told_the_latest_gate_not_an_older_one(monkeypatch, context):
    """t9: attempt 1 wrote and failed one way; attempt 2 wrote nothing and
    the gate failed another way. Attempt 3 must be sent the second.

    Three DISTINCT gates, because `_gate_sequence` repeats its last report:
    with two, attempts 2 and 3 draw the same gate, and the note below would
    name it whichever side of the assignment it was computed on."""
    _real_diff(monkeypatch)
    prompts = _scripted_subagents(monkeypatch, context.project_path, [{"a.py": "x = 1\n"}])
    _gate_sequence(
        monkeypatch,
        failing_report((Finding("tests/conftest.py", 8, "ModuleNotFoundError"),)),
        failing_report((Finding("plugin.py", 311, "FixtureDef has no unittest"),)),
        failing_report((Finding("plugin.py", 402, "a third failure"),)),
    )

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert "FixtureDef has no unittest" in prompts[2]
    assert "ModuleNotFoundError" not in prompts[2]
    # The note of an attempt that wrote nothing names what THAT attempt was
    # asked to fix -- attempt 3 was sent the second gate -- so it is computed
    # before the blocker moves on to the third.
    assert "wrote nothing on a retry" in task.note
    assert "FixtureDef has no unittest" in task.note
    assert "a third failure" not in task.note


# --- OPEN-135: an escalation stops the run whether or not the coder wrote ----
#
# The empty-diff branch ran the gate and asked only whether it passed; the
# `report.escalate` check lived on the other path alone. So a gate no model
# can fix -- a denied command, a missing tool, an internal error -- sent a
# coder that had correctly written nothing back for another try, and the task
# ended BLOCKED having spent every attempt, where the same gate after an
# attempt that wrote a file stops the run at once.


async def test_an_escalating_gate_stops_the_run_when_the_coder_wrote_nothing(
    monkeypatch, context, wrote_nothing
):
    """The gate `test_an_escalating_gate_stops_the_whole_run` stops on,
    reached by an attempt that changed no file."""
    prompts = _scripted_subagents(monkeypatch, context.project_path, [])
    _gate_sequence(monkeypatch, escalating_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert len(prompts) == 1
    # PENDING, so `--continue` retries it once the user has fixed what the
    # gate could not (A1.93) -- the same as the other path.
    assert task.status is TaskStatus.PENDING
    assert task.note == "typecheck failed: <auto:shell-not-opted-in>"


async def test_an_escalation_on_a_retry_that_wrote_nothing_stops_the_run(monkeypatch, context):
    """Attempt 1 wrote a file and failed a way a model can fix; attempt 2
    wrote nothing and the gate could not run. The run stops there, and
    attempt 1's file is still on the record."""
    _real_diff(monkeypatch)
    prompts = _scripted_subagents(monkeypatch, context.project_path, [{"a.py": "x = 1\n"}])
    _gate_sequence(monkeypatch, failing_report(), escalating_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert len(prompts) == 2
    assert task.files_touched == ("a.py",)


async def test_an_escalation_is_never_read_as_nothing_to_do(monkeypatch, context, wrote_nothing):
    """The check comes before any verdict is read. OPEN-132's plan reads a
    red gate as INHERITED in this branch, and an escalated gate has no test
    stage verdict to veto `_confirms_nothing_to_do` -- so, under that verdict,
    a task was marked DONE over a gate that never ran (its plan's code,
    applied alone, 2026-09-17). Forced here by the verdict alone.

    Two-sided, because placement is a separate claim from outcome: the plan
    says the check comes before `verdict_for`, and the record says this
    branch leaves `context.failure_baseline` untouched. `calls` staying
    empty is what catches the check moved AFTER `verdict_for` is called --
    the escalate return would happen either way, but `verdict_for` must
    never run on an escalated gate. The `INHERITED` return is what catches
    the check moved below `_confirms_nothing_to_do`, which would otherwise
    read an escalated gate as nothing to do and mark the task DONE.
    """
    calls: list[None] = []

    def verdict_for(report, *, inherited):
        calls.append(None)
        return engine.INHERITED

    monkeypatch.setattr(engine, "verdict_for", verdict_for)
    _scripted_subagents(monkeypatch, context.project_path, [])
    _gate_sequence(monkeypatch, escalating_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.STOP_RUN
    assert task.status is not TaskStatus.DONE
    assert calls == []


def _new_context(project_path):
    """The `context` fixture's own recipe, callable a second time so each
    path in `test_both_paths_report_an_escalation_the_same_way` gets a
    LoopContext of its own rather than one that carries the first run's
    `failure_baseline` and attempt count into the second."""
    project_path.mkdir(parents=True, exist_ok=True)
    return LoopContext(
        subagents=FakeSubagents(),
        project_path=project_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(project_path),
    )


async def test_both_paths_report_an_escalation_the_same_way(monkeypatch, tmp_path):
    """CLAUDE.md says the two branches -- the empty-diff path added by
    OPEN-135 and the main gate path it copied -- handle an escalation the
    same way, and the code backs that: `engine.py:869-871` and `:953-955`
    are byte-identical. But nothing pinned the CLAIM, only each branch's own
    behaviour, so an edit to one branch's note text (or its Outcome) would
    leave the suite green while the two diverged. This is the house rule at
    `CLAUDE.md` §3 (OPEN-64): a comment asserting two things are the same
    needs a test, or it becomes the reason nobody checks.

    The main path is reached with the autouse `default_fakes` coder, which
    writes `a.py` (as far as `changed_since` is concerned); the empty-diff
    path is reached by then switching `changed_since` to report nothing
    written, the same substitution `wrote_nothing` makes.
    """
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: escalating_report())

    main_context = _new_context(tmp_path / "main")
    main_outcome, main_task, _ = await run_one(main_context)

    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())
    empty_context = _new_context(tmp_path / "empty")
    empty_outcome, empty_task, _ = await run_one(empty_context)

    assert main_outcome is Outcome.STOP_RUN
    assert empty_outcome is Outcome.STOP_RUN
    assert main_outcome is empty_outcome
    assert main_task.note == empty_task.note
    assert main_task.status is empty_task.status


# --- OPEN-132: a task that changed nothing cannot own a failure --------------
#
# Run a4196786280d: t1 left pytest crashing before collection -- a failure with
# no location, so no key, so `verdict_for` can only call it REGRESSED. t2's
# work already existed; its coder read the model and wrote nothing, three
# times, and t2 went BLOCKED. Every later task would have. Owner, 2026-09-17,
# option 2: a task that changed no file cannot have caused the failure, so a
# gate that was already failing when it began is not its blocker.


def collection_crash_report(exception="ModuleNotFoundError: No module named 'main'"):
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name="test",
                outcome=FAILED,
                blocking=True,
                detail="the test command collected nothing, but 2 test file(s) are present",
                output_tail=f"E   {exception}\n",
            ),
        ]
    )


async def test_a_task_whose_work_exists_passes_over_a_crash_that_predates_it(
    monkeypatch, context, wrote_nothing
):
    """t2, reproduced: nothing changed, the gate was red before the task."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: collection_crash_report())
    context.baseline_failed = True

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.attempts == 1
    assert "already failing before this task began" in task.note
    assert "collected nothing" in task.note
    assert "gate passes" not in task.note


async def test_a_crash_still_blocks_a_task_that_changed_a_file(monkeypatch, context):
    """The protection `verdict_for` exists for: an unlocated failure a task
    COULD have caused stays its own, even on a retry that wrote nothing."""
    _real_diff(monkeypatch)
    _scripted_subagents(monkeypatch, context.project_path, [{"main.py": "import nope\n"}])
    _gate_sequence(monkeypatch, collection_crash_report())
    context.baseline_failed = True

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.files_touched == ("main.py",)


async def test_without_evidence_the_gate_was_already_red_a_crash_still_blocks(
    monkeypatch, context, wrote_nothing
):
    """A fresh process, or a gate that passed before this task: the degraded
    mode is the old blocking one (OPEN-23's rule)."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: collection_crash_report())

    outcome, _, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED


async def test_every_gate_run_records_whether_it_failed(monkeypatch, context):
    """What the NEXT task reads as `baseline_failed`, set beside
    `failure_baseline` at both gate sites."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: collection_crash_report())
    await run_one(context)
    assert context.baseline_failed is True

    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    await run_one(context)
    assert context.baseline_failed is False


async def test_an_empty_diff_over_inherited_failures_does_not_say_the_gate_passes(
    monkeypatch, context, wrote_nothing
):
    """Found while planning OPEN-132: the located half of the same branch
    marked this DONE -- correctly -- with "the gate passes over the whole
    project with its tests run", while tests/test_cli.py:119 failed."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report(CLI_FINDINGS))
    context.failure_baseline = frozenset({"tests/test_cli.py:119"})
    context.baseline_failed = True

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert "gate passes" not in task.note
    assert "tests/test_cli.py:119" in task.note


# --- OPEN-136: a run that stopped resumes with a fix budget ----------------
#
# `_stop` returns a STOP_RUN task to PENDING on purpose (A1.93), with its
# attempts spent. The loop's bound read those attempts, so `--continue`
# re-entered run_task, served nothing, and fell to the BLOCKED tail, whose
# keyword guard does not spare an escalation note: the user fixed the denial,
# resumed, and was told `3 attempts exhausted`. The budget is this run's now.


def _changing_failure(monkeypatch, *, then=None):
    """A gate whose findings move every call, so nothing blocks on the
    no-progress signature; `then` answers once the moving ones run out."""
    lines = iter(range(1, 99))

    def gate(*a, **k):
        if then is not None and next(lines) > 90:  # pragma: no cover - guard
            return then
        return failing_report((Finding("a.py", next(lines), "bad type"),))

    monkeypatch.setattr(engine, "verify_project", gate)


async def test_a_task_the_run_stopped_on_is_dispatched_again_on_resume(
    monkeypatch, context, default_fakes
):
    """The filed reproduction: two failing gates, an escalation on the last
    attempt, then run_task re-entered on the same task."""
    reports = [
        failing_report((Finding("a.py", 1, "bad type"),)),
        failing_report((Finding("a.py", 2, "bad type"),)),
        escalating_report(),
    ]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    ledger = Ledger()
    task = ledger.add("write the parser")
    first = await run_task(task, ledger, context=context)

    assert first is Outcome.STOP_RUN
    assert task.status is TaskStatus.PENDING, "A1.93 -- --continue must resume it"
    assert task.attempts == 3
    assert "<auto:shell-not-opted-in>" in task.note
    served_before = len(default_fakes)

    # The user grants the shell and resumes. The gate passes now.
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    resumed = await run_task(task, ledger, context=context)

    assert len(default_fakes) > served_before, "no coder was dispatched on the resume"
    assert resumed is Outcome.DONE
    assert "attempts exhausted" not in task.note


async def test_the_fix_budget_is_this_run_s_not_the_task_s_lifetime(monkeypatch, context):
    """A resumed task gets max_fix_attempts tries of its own. `task.attempts`
    keeps counting for the ledger, which is what §8a reads."""
    _changing_failure(monkeypatch)

    ledger = Ledger()
    task = ledger.add("write the parser")
    await run_task(task, ledger, context=context)
    assert task.attempts == 3

    task.status = TaskStatus.PENDING  # what _stop leaves behind
    await run_task(task, ledger, context=context)

    assert task.attempts == 6


async def test_a_failed_invocation_costs_the_resumed_run_no_budget_either(monkeypatch, context):
    """OPEN-46's give-back measured against the new ceiling: a dispatch the
    provider never served must not eat one of the resumed run's three."""
    served: list[str] = []
    results = [SubagentResult(name="coder", text="", ok=False, error="Error code: 500 - internal")]

    async def flaky(name, prompt, *, context, thread_id=None):
        served.append(name)
        return results.pop(0) if results else SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", flaky)
    _changing_failure(monkeypatch)

    ledger = Ledger()
    task = ledger.add("write the parser")
    task.attempts = 3  # a run stopped on it
    task.status = TaskStatus.PENDING

    await run_task(task, ledger, context=context)

    assert task.attempts == 6, "the 500 spent one of the resumed run's tries"
    assert served.count("coder") == 4


# --------------------------------------------------------------------------
# OPEN-142: a task whose own earlier attempt wrote its work is judged as the
# writing path judges it, not blocked for writing nothing
# --------------------------------------------------------------------------


def _diffs(monkeypatch, *diffs):
    """`changed_since` answers these, in order, then () for ever after."""
    queue = list(diffs)
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: queue.pop(0) if queue else ())


def _resumed(context):
    """What `--continue` starts from: the ledger on disk and a new process, so
    nothing `run_task` or the context held in memory crosses over."""
    ledger = Ledger.load(context.paths.ledger_json)
    (task,) = ledger.resumable()
    fresh = LoopContext(
        subagents=FakeSubagents(),
        project_path=context.project_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=context.paths,
    )
    return task, ledger, fresh


async def test_a_resumed_task_whose_own_work_is_in_place_gets_tests_not_a_block(
    monkeypatch, context, default_fakes
):
    """Run E, both invocations. E1's coder wrote t1's files and the gate
    escalated (no shell), so the run stopped with t1 PENDING. E2 resumed it,
    the coder correctly wrote nothing, and a green gate with no tests blocked
    it -- where the same files on the writing path get the tester."""
    _diffs(monkeypatch, ("config.py", "database.py"))
    reports = [escalating_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    ledger = Ledger()
    task = ledger.add("create the configuration and database setup")
    assert await run_task(task, ledger, context=context) is Outcome.STOP_RUN

    # --continue with the shell granted: the coder finds its work and writes nothing.
    task, ledger, fresh = _resumed(context)
    assert task.files_touched == ("config.py", "database.py")
    reports[:] = [no_test_judgement_report(), passing_report()]
    del default_fakes[:]
    resumed = await run_task(task, ledger, context=fresh)

    assert default_fakes == ["coder", "tester"]
    assert resumed is Outcome.DONE
    assert "wrote nothing" not in task.note


async def test_work_the_task_never_wrote_still_gets_no_tester_and_blocks(
    monkeypatch, context, default_fakes, wrote_nothing
):
    """OPEN-12/13 and OPEN-27, kept: with no `files_touched` there is no work
    of this task's to test, and a vacuous green is still not evidence."""
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: vacuous_report())

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert "tester" not in default_fakes
    assert task.files_touched == ()


async def test_a_retry_after_its_own_gate_declined_stays_on_the_empty_diff_path(
    monkeypatch, context, default_fakes
):
    """OPEN-123's case, kept: this call's gate already judged the task's files
    and failed them, so a retry that writes nothing is not handed the tester."""
    _diffs(monkeypatch, ("a.py",))
    reports = [failing_report(), vacuous_report(), vacuous_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert "tester" not in default_fakes
    assert task.attempts == 3


async def test_a_resume_over_a_failing_gate_is_sent_the_failure_not_blocked_on_it(
    monkeypatch, context, default_fakes
):
    """Found re-verifying this item's plan, whose first condition regressed it:
    a task stopped after a FAILING gate keeps `last_signature` in the ledger,
    and a resumed coder is sent no blocker. One that writes nothing must stay
    on the empty-diff path, which hands the next attempt the failure. Sent
    down the writing path it met the same signature and was BLOCKED as no
    progress on its first resumed attempt, having been told nothing."""
    prompts: list[str] = []

    async def coder(name, prompt, *, context, thread_id=None):
        default_fakes.append(name)
        prompts.append(prompt)
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder)
    # Attempt 1 writes and fails; attempt 2 writes and the gate escalates.
    _diffs(monkeypatch, ("a.py",), ("a.py",))
    reports = [failing_report(), escalating_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    ledger = Ledger()
    task = ledger.add("write the parser")
    assert await run_task(task, ledger, context=context) is Outcome.STOP_RUN

    task, ledger, fresh = _resumed(context)
    assert task.last_signature is not None
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    del default_fakes[:], prompts[:]
    outcome = await run_task(task, ledger, context=fresh)

    assert outcome is Outcome.BLOCKED
    assert default_fakes == ["coder"] * 3, "the resumed run's budget went unspent"
    assert "did not pass verification" in prompts[1]
    assert not task.note.startswith("no progress")


# --------------------------------------------------------------------------
# OPEN-152: what the reviewer is told about `git_diff`
#
# A1.68 shipped TWO halves the same day: `_review_prompt` names the files the
# run touched, and `git_tools._untracked_note` makes `git_diff` NAME untracked
# files instead of answering "No changes in the working tree". The prompt kept
# describing the behaviour its own sibling had just removed -- and nothing
# tested that the two agreed, which is CLAUDE.md lesson 4.
#
# Measured live: run G2 `eed59b91daca`'s reviewer read the sentence and called
# ls + read_file x7 -- zero `git_diff` -- in a repository where `main.py` (2
# insertions, 2 deletions) and `.rudra/AGENTS.md` were both tracked and
# modified. `git_diff` was the right tool and the prompt said it would show
# nothing.
# --------------------------------------------------------------------------


def _ledger_touching(*paths: str) -> Ledger:
    ledger = Ledger()
    for index, path in enumerate(paths, start=1):
        ledger.tasks.append(
            engine.Task(id=f"t{index}", description=f"task {index}", files_touched=(path,))
        )
    return ledger


def test_the_prompt_does_not_claim_git_diff_shows_nothing():
    """The sentence OPEN-152 was filed on. `git_diff` on a fresh repository
    lists the untracked files; it does not show nothing."""
    prompt = engine._review_prompt(_ledger_touching("app/service.py"))

    assert "will show nothing" not in prompt
    assert "show nothing" not in prompt


def test_the_prompt_names_both_tools_with_the_case_each_suits():
    prompt = engine._review_prompt(_ledger_touching("app/service.py"))

    assert "git_diff" in prompt, "the reviewer must still know the tool exists"
    assert "read_file" in prompt


def test_the_claim_about_git_diff_agrees_with_what_git_diff_does():
    """The pin lesson 4 asks for: the prompt's claim is checked against the
    real `_untracked_note`, not against a restatement of it. If upstream of
    this ever goes back to answering nothing for an all-untracked tree, this
    fails rather than the prompt silently becoming wrong again."""
    import inspect

    from rudra.tools import git_tools

    source = inspect.getsource(git_tools._untracked_note)
    assert "Untracked files (new, so no diff exists yet)" in source, (
        "git_diff no longer names untracked files -- re-read _review_prompt's claim"
    )

    prompt = engine._review_prompt(_ledger_touching("app/service.py"))
    assert "untracked" in prompt.lower(), (
        "the prompt must say what git_diff does with an untracked file, "
        "because that is the half A1.68 fixed and the prompt denied"
    )


def test_a_run_that_touched_nothing_gets_no_tool_guidance_at_all():
    """A1.68 half 1: the guidance hangs off the file list, and with no files
    there is nothing to guide. Unchanged by OPEN-152."""
    prompt = engine._review_prompt(Ledger())

    assert prompt == "Review the changes this run made and report any problems."
    assert "git_diff" not in prompt


def test_the_file_list_is_still_deduplicated_and_ordered(tmp_path):
    """A1.68 half 1, pinned while its sibling sentence changes."""
    ledger = Ledger()
    ledger.tasks.append(
        engine.Task(id="t1", description="one", files_touched=("app/service.py", "main.py"))
    )
    ledger.tasks.append(
        engine.Task(id="t2", description="two", files_touched=("main.py", "tests/test_x.py"))
    )

    prompt = engine._review_prompt(ledger)

    assert prompt.count("- main.py\n") == 1, "listed once, though two tasks touched it"
    assert (
        prompt.index("- app/service.py")
        < prompt.index("- main.py")
        < prompt.index("- tests/test_x.py")
    ), "first-touched order, not sorted"


def test_a_blocked_task_s_files_are_still_listed():
    """A1.68: half-finished work is what most deserves a second opinion."""
    ledger = Ledger()
    ledger.tasks.append(
        engine.Task(
            id="t1",
            description="one",
            status=TaskStatus.BLOCKED,
            files_touched=("app/half_done.py",),
        )
    )

    assert "- app/half_done.py" in engine._review_prompt(ledger)


# --- OPEN-161: a gate that failed only on an undeclared package ------------

_MISSING = """\
collected 0 items / 1 error

==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_main.py ______________________
{frame}: in <module>
    import {module}
E   ModuleNotFoundError: No module named '{module}'
=========================== short test summary info ============================
ERROR tests/test_main.py
=============================== 1 error in 0.13s ===============================
"""


def _missing_package_report(module: str, frame: str = "app/database.py:1"):
    """`frame` is where the import failed, and it is what C6.5a signs: two
    packages missing at one import line would read as no progress."""
    return _gate_blocker("test", _MISSING.format(module=module, frame=frame))


class _Notices:
    def __init__(self):
        self.seen: list[tuple[str, str]] = []

    def notice(self, payload, *, role, name="", **kwargs):
        self.seen.append((name, payload))


async def test_a_package_behind_a_package_does_not_exhaust_the_task(monkeypatch, context):
    """Run `4989aefefacb` t1, reproduced: a stub, then `No module named
    'sqlalchemy'`, then -- once the coder declared it -- `greenlet` behind it.
    Three gates on a budget of three, two of them spent on packages nobody
    had declared, and the task ended BLOCKED on the third without the coder
    ever being shown greenlet. Each gate whose only failure is a missing
    package gives its attempt back (OPEN-161, the owner's option A)."""
    from rudra.context.usage import RunUsage

    context.usage = RunUsage()
    context.subagents.trace = _Notices()
    _gate_sequence(
        monkeypatch,
        failing_report(),
        _missing_package_report("sqlalchemy"),
        _missing_package_report(
            "greenlet", frame=".venv/lib/python3.12/site-packages/sqlalchemy/util/concurrency.py:70"
        ),
        passing_report(),
    )

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.DONE
    assert task.attempts == 2, "four dispatches, two of them given back"
    assert task.dependency_gates == ("sqlalchemy", "greenlet")
    assert context.usage.dependency_gates == 2
    assert context.usage.as_log()["run"]["dependency_gates"] == 2
    names = [name for name, _ in context.subagents.trace.seen]
    assert names == [engine.DEPENDENCY_GATE_NOTICE] * 2
    assert "greenlet" in context.subagents.trace.seen[1][1]


async def test_the_dependency_give_back_is_capped_per_task(monkeypatch, context, default_fakes):
    """A coder inventing a new missing package on every attempt still ends:
    at most MAX_DEPENDENCY_GATES are given back, then the budget is spent as
    before."""
    lines = iter(range(1, 100))

    def gate(*args, **kwargs):
        line = next(lines)
        return _missing_package_report(f"pkg{line}", frame=f"app/database.py:{line}")

    monkeypatch.setattr(engine, "verify_project", gate)

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert len(default_fakes) == context.cfg.agent.max_fix_attempts + engine.MAX_DEPENDENCY_GATES
    assert len(task.dependency_gates) == engine.MAX_DEPENDENCY_GATES
    assert task.attempts == context.cfg.agent.max_fix_attempts


async def test_a_package_the_coder_did_not_declare_is_still_no_progress(
    monkeypatch, context, default_fakes
):
    """The plan's own guard -- give back only once the next attempt edits a
    dependency file -- was replaced by the owner, because it can never fire
    after the exhausting gate. What bounds a coder that ignores the package
    instead is C6.5a: the same gate twice is BLOCKED."""
    _gate_sequence(monkeypatch, _missing_package_report("sqlalchemy"))

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.note.startswith("no progress")
    assert len(default_fakes) == 2


async def test_a_module_the_project_holds_is_charged(monkeypatch, context, default_fakes):
    """OPEN-140's `No module named 'main'` is a path defect, not a package."""
    (context.project_path / "main.py").write_text("x = 1\n")
    lines = iter(range(100))
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: _missing_package_report("main", frame=f"app/database.py:{next(lines)}"),
    )

    outcome, task, _ = await run_one(context)

    assert outcome is Outcome.BLOCKED
    assert task.dependency_gates == ()
    assert len(default_fakes) == context.cfg.agent.max_fix_attempts


def test_dependency_gates_survive_a_ledger_round_trip(tmp_path):
    ledger = Ledger()
    task = ledger.add("one")
    task.dependency_gates = ("sqlalchemy", "greenlet, sqlalchemy[asyncio]")
    path = tmp_path / "ledger.json"
    ledger.save(path)

    assert Ledger.load(path).tasks[0].dependency_gates == task.dependency_gates


def test_the_troubleshooting_page_quotes_the_dependency_gate_as_it_is_printed():
    """`06-troubleshooting.md` tells a user what a refunded gate prints, where
    it is recorded, and how many are given back. Each is read off the code
    here rather than restated (lesson 4)."""
    from pathlib import Path

    page = (Path(__file__).parent.parent / "Documentation" / "06-troubleshooting.md").read_text()
    task = engine.Task(id="t1", description="x")
    printed = Console(record=True, width=400)
    context = LoopContext(
        subagents=FakeSubagents(),
        project_path=Path(__file__).parent,
        console=printed,
        cfg=FakeCfg(),
        paths=None,
    )

    assert engine._refund_dependency_gate(task, _missing_package_report("x"), context, 0)

    assert "the gate failed only on packages the project does not declare (" in page
    assert "the gate failed only on packages the project does not declare (x)" in (
        printed.export_text()
    )
    assert "`dependency_gates` in `ledger.json`" in page and task.dependency_gates == ("x",)
    assert engine.MAX_DEPENDENCY_GATES == 3 and "At most three are given back" in page
