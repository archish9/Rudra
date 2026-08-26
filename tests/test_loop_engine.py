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


async def test_one_transient_error_costs_an_attempt_and_not_the_run(monkeypatch, context):
    """OPEN-33: run `eb2e1e2e2b2c` ended `0 done · 11 never attempted` on one
    HTTP 500 from the provider, 693 seconds into task 1.

    `subagents/runner.py` catches every exception and reports it as
    `SubagentResult.error`, so a provider 500 and a broken config are the
    same value; the loop treated that value as fatal on the stated grounds
    that it "would recur on every remaining task". A 500 is the
    counterexample.
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

    assert outcome is Outcome.DONE
    assert task.attempts == 2, "the errored attempt is spent, not fatal"


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
