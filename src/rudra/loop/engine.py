"""The loop: dispatch work, verify it, fix it, and decide when to stop.

The split this module exists to enforce (spec S9c.1): the model decides
what work exists and what to do next; Python decides when a task is done
and when to stop. C6.1 asked for an agent that owns the todo list; D9
forbids an LLM deciding termination. Both hold here because the ledger
tools cannot write DONE and this module is the only thing that can.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from rich.console import Console
from rich.markup import escape

from rudra.context.usage import SUSPENDED_NOTICE_SECONDS, render_usage
from rudra.git.core import is_repo, status
from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.regressions import INHERITED, PASSED, failure_keys, verdict_for
from rudra.memory.degrade import last_failure
from rudra.memory.entry import MemoryEntry
from rudra.subagents import SubagentContext, run_subagent
from rudra.verify import verify_project
from rudra.verify.stubs import SKIP_DIRS, source_files


class Outcome(StrEnum):
    """What running one task told the caller to do next."""

    DONE = "done"
    BLOCKED = "blocked"
    STOP_RUN = "stop_run"
    # The user pressed Ctrl-C (C9.3). Distinct from STOP_RUN because
    # nothing is wrong: the ledger is intact, the in-flight task is back
    # at PENDING, and `rudra --continue` picks up exactly here.
    CANCELLED = "cancelled"


@dataclass
class LoopContext:
    """Everything one run needs, assembled once.

    Bundled for the reason Gate and SubagentContext are: these must be the
    *same* objects across calls, the gate and its session grants
    especially.
    """

    subagents: SubagentContext
    project_path: Path
    console: Console
    cfg: Any
    paths: Any
    # The run's token tally. Optional: every 9c-era test builds a
    # LoopContext without one.
    usage: Any = None
    # The run's MemoryStore, or None when it could not be built. Shared by
    # reference with SubagentContext -- one run, one palace handle. Optional
    # for the reason `usage` is.
    memory: Any = None
    # What the gate was failing on when it last ran, as loop/regressions.py
    # keys (OPEN-23). Mutated by run_task, read by the next task, and
    # deliberately NOT persisted: a fresh process starts empty, every failure
    # then reads as new, and the loop behaves exactly as it did before this
    # field existed. The degraded mode is the old blocking one.
    failure_baseline: frozenset[str] = frozenset()
    # How many subagent runs in a row never produced a turn (OPEN-33).
    # Mutated by run_task, reset by any run that does produce one. Not
    # persisted, for the reason `failure_baseline` is not: a fresh process
    # starts at zero and gets its full budget, which is the safe direction.
    run_errors: int = 0


def _is_build_output(path: str) -> bool:
    """Is this path inside a directory nothing should ever attribute to the coder?

    One definition, shared with the gate (verify/stubs.py): every stack
    profile already declares its build-output dirs, and the loop must not
    keep a second opinion.
    """
    return any(part in SKIP_DIRS for part in PurePosixPath(path).parts)


def _digest(path: Path) -> str:
    """A cheap content fingerprint, or "" when the file cannot be read.

    Unreadable and absent both yield "", which is what a deletion should
    look like to the comparison below.
    """
    try:
        with path.open("rb") as handle:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def git_snapshot(context: LoopContext) -> dict[str, str] | None:
    """What git reports as changed, each path with a content fingerprint.

    None is a real answer the caller acts on: without git there is no way
    to tell what an attempt touched, so the gate falls back to scanning
    everything -- the same choice `rudra verify` makes.

    Two things here are A1.66, and they compound. **`all_untracked=True`**:
    plain porcelain collapses an untracked directory into one entry, so
    once a task has created `src/`, a later task writing `src/main.rs`
    adds no new entry. **The fingerprint**: a file that is untracked
    before and after an attempt appears identically in both listings even
    when its contents were rewritten -- which is exactly what a fix-loop
    retry does to code written in a greenfield repo. Under either one the
    diff comes back empty, the empty-diff guard reads that as "the coder
    wrote nothing", and real work is blocked. Measured on a greenfield
    Rust run: every task after the first blocked at 3 attempts, with its
    files on disk the whole time.

    Hashing is bounded by what git already reports and by the build-output
    pruning below, so this reads the changed files, not the project.
    """
    gate = context.subagents.gate
    if not is_repo(context.project_path, gate=gate, console=context.console, cfg=context.cfg):
        return None
    entries = status(
        context.project_path,
        gate=gate,
        console=context.console,
        cfg=context.cfg,
        all_untracked=True,
    )
    if entries is None:
        # git failed. None means "no snapshot", which run_task already
        # handles -- returning {} would make the empty-diff guard read every
        # attempt as "the coder wrote nothing" (CR-E12).
        return None
    return {
        entry.path: _digest(context.project_path / entry.path)
        for entry in entries
        if entry.path and not _is_build_output(entry.path)
    }


def tree_snapshot(context: LoopContext) -> dict[str, str]:
    """Every scannable source file in the project, each with a fingerprint.

    What `git_snapshot` is for a project that has no git. `source_files`
    already prunes build output and non-source suffixes (verify/stubs.py),
    so this reads the project's code, not the project -- the same bound
    `git_snapshot` gets from git plus `_is_build_output`.

    It is a whole-tree walk where the git path is a `git status` call, and
    that is the price of the answer: without it there is no answer at all,
    which is OPEN-13.
    """
    root = Path(context.project_path)
    return {path: _digest(root / path) for path in source_files(root)}


def attempt_snapshot(context: LoopContext) -> dict[str, str]:
    """The before/after fingerprint for one attempt. Never None.

    OPEN-13: `git_snapshot` returns None outside a repo, and the empty-diff
    guard in `run_task` was written as `before is not None and not
    files_touched` -- so in a project with no `.git` the guard never ran,
    an attempt that wrote nothing reached a gate with zero changed files,
    the gate passed it vacuously ("0 changed file(s) scanned"), and the
    task was marked DONE. Measured on the owner's 2026-08-24 run: two tasks
    `done`, `files_touched: []`, no file created.

    None was never a third state the caller wanted -- it was "ask the
    filesystem instead", which is what this does, once, in one place, so
    the before and the after cannot come from different sources.
    """
    snapshot = git_snapshot(context)
    return tree_snapshot(context) if snapshot is None else snapshot


def changed_since(context: LoopContext, before: dict[str, str] | None) -> tuple[str, ...]:
    """What this attempt touched.

    Read from git -- or from the tree, outside a repo -- rather than from
    the model. Asking the coder what it wrote invites a wrong answer at
    exactly the moment the answer matters, because it feeds 9a's stub scan.

    A path counts when it is new, gone, or its fingerprint moved. The last
    case is what makes a retry that rewrites an untracked file visible
    (A1.66).

    `before is None` still means "no snapshot was taken", and still answers
    with the whole project. Nothing in the loop passes it any more --
    `run_task` uses `attempt_snapshot`, which always has one -- but the
    contract is kept for callers outside it.
    """
    if before is None:
        return source_files(context.project_path)
    after = attempt_snapshot(context)
    touched = {path for path, digest in after.items() if before.get(path) != digest}
    touched |= {path for path in before if path not in after}
    return tuple(sorted(touched))


def _coder_prompt(task: Task, blocker_text: str = "") -> str:
    prompt = (
        f"Task {task.id}: {task.description}\n\n"
        "Write every file this task needs. Stop when the task is complete."
    )
    if blocker_text:
        prompt += (
            "\n\nYour previous attempt did not pass verification. "
            "Fix exactly this, then stop:\n\n" + blocker_text
        )
    return prompt


def _tester_prompt(task: Task) -> str:
    files = ", ".join(task.files_touched) or "the code this project contains"
    return (
        f"The project has no tests covering recent work on: {files}\n\n"
        f"That work was: {task.description}\n\n"
        "Write tests for it, run them, and report what happened."
    )


def _blocker_text(report: Any) -> str:
    """The gate's complaint, verbatim -- a paraphrase is a worse input."""
    blocker = report.blocker
    if blocker is None:  # pragma: no cover - only called on a failure
        return ""
    lines = [f"{blocker.name} failed: {blocker.detail}".rstrip(": ")]
    lines.extend(
        f"  {finding.file}:{finding.line}: {finding.message}" for finding in blocker.findings
    )
    if not blocker.findings and blocker.output_tail:
        lines.append(blocker.output_tail)
    return "\n".join(lines)


def _confirms_nothing_to_do(report: Any, verdict: str) -> bool:
    """Does this gate run prove the task did not need doing?

    Both halves are load-bearing.

    Green -- or green but for failures that predate the task (OPEN-23) --
    says the project is in the state the task wanted.
    `tests_produced_no_judgement` says whether "green" means anything at all:
    a project with no files parses no syntax and collects no tests, and that
    is exactly the shape OPEN-12/13 wrongly passed, marking two tasks DONE
    having created no file. A vacuous pass is not evidence, so it blocks.
    """
    return (verdict is PASSED or verdict is INHERITED) and not tests_produced_no_judgement(report)


def _already_satisfied_note(report: Any) -> str:
    """Why a task that wrote nothing is DONE (OPEN-27)."""
    tested = next((stage for stage in report.stages if stage.name == "test"), None)
    detail = f" ({tested.detail})" if tested is not None and tested.detail else ""
    return (
        "the coder wrote nothing, and nothing needed writing: this task's "
        "work was already in place, and the gate passes over the whole "
        f"project with its tests run{detail}."
    )


def _wrote_nothing_note(blocker_text: str, report: Any = None) -> str:
    """Why an attempt changed no file, and WHICH of the cases it is.

    **This exact string has been the visible symptom of three unrelated
    defects in three consecutive runs**: a stale test the coder was not
    allowed to fix (OPEN-23), a delegation to an agent that could not do the
    work (OPEN-26), and work an earlier task had already completed
    (OPEN-27). Every time it sent the reader after a lazy coder, and every
    time the coder was behaving correctly. So it now says which.

    The substring "wrote nothing" is load-bearing -- the BLOCKED path below
    greps for it to avoid overwriting this note with an attempt count.
    """
    if report is not None and tests_produced_no_judgement(report):
        return (
            "the coder wrote nothing, and the project cannot confirm whether "
            "it needed to: no tests were collected, so a green gate proves "
            "nothing here. Write the work, or add a test that covers it."
        )
    if blocker_text:
        return f"the coder wrote nothing on a retry. It had been asked to fix:\n\n{blocker_text}"
    if report is not None and report.blocker is not None:
        return f"the coder wrote nothing, and the gate is failing:\n\n{_blocker_text(report)}"
    return "the coder wrote nothing"


def _inherited_note(report: Any, inherited: frozenset[str]) -> str:
    """Why a task passed while the suite is red (OPEN-23).

    Names the failures rather than counting them, for the reason
    `_blocker_text` quotes the gate verbatim: the next reader of this ledger
    wants to know WHICH file is red and whether anything owns it, and a
    number answers neither.
    """
    still = sorted(failure_keys(report) & inherited)
    listed = "\n".join(f"  {key}" for key in still)
    return (
        "passed: this task introduced no failure.\n\n"
        f"{len(still)} failure(s) were already failing before it ran and still are:\n"
        f"{listed}\n\n"
        "They belong to whatever task owns those files, not to this one."
    )


async def _verify(task: Task, context: LoopContext) -> Any:
    """Run the gate over what this task touched.

    Off the event loop, because verify_project reaches subprocess.Popen
    (shell/runner.py) with `[tools] test_timeout` -- 600 seconds by
    default. `_cancel_on_sigint` installs its handler with
    `loop.add_signal_handler`, whose callback only runs when the loop
    regains control, so a synchronous call here meant that during a ten
    minute pytest the first Ctrl-C did nothing AND the second press's
    `os._exit` -- the escape hatch whose whole purpose is that a hung
    cancel must not need a kill from another terminal -- was deferred
    just as long (CR-C5).
    """
    return await asyncio.to_thread(
        verify_project,
        context.project_path,
        changed_files=task.files_touched,
        gate=context.subagents.gate,
        console=context.console,
        cfg=context.cfg,
    )


# Consecutive subagent runs that never happened before the run gives up
# (OPEN-33). A budget rather than a classifier: `subagents/runner.py`
# catches every exception and hands the loop a string, so "is this
# transient?" can only be answered by string-matching a provider's prose --
# which is a guess that breaks on the next provider. Whether the failure
# recurs is not a guess, and it is the thing the old code was asserting
# when it said an error "would recur on every remaining task".
#
# Three, because it must be small enough that a genuinely broken
# environment still stops in seconds. A build failure raises before any
# network call (llm/factory.py), so three of those cost nothing.
MAX_CONSECUTIVE_RUN_ERRORS = 3


def _record_halt(task: Task, result: Any, context: LoopContext) -> None:
    """Keep a subagent guard halt, and say it happened (OPEN-44).

    `task.note` used to be the only place this landed, and every branch
    that finishes a task rewrites that field -- the PASSING one clears it
    outright. So a halt survived exactly when the task ALSO failed, and
    vanished in the case a reader most needs it: run bf6be7525991 ended 6
    of 6 coder invocations on MAX_REPEATED_CALLS and its ledger, its
    terminal and its debug log all said nothing about any of them.

    `halts` is a separate field rather than a wider `note` for a reason
    the fix must not undo: `note` is interpolated verbatim into
    consult_planner's prompt and filed in the palace by
    record_block_memory (CR-C4), so it is read by a MODEL later. A halt is
    a fact about Rudra's machinery, not about the work.

    The console line is the same report review_once has made for the
    reviewer since OPEN-35 -- printed where it happens, because the run
    trace carries it at VERBOSE only (trace/render.py).
    """
    if not result.halted_reason:
        return
    task.halts = (*task.halts, result.halted_reason)
    context.console.print(
        f"[dim]{escape(result.name)} stopped early: {escape(result.halted_reason)}[/dim]"
    )


async def run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
    """Write, verify, fix, reverify -- until the gate passes or we stop.

    Only this function writes DONE, and only on VerifyReport.passed.
    """
    tested = False
    blocker_text = ""
    # What was already failing before this task ran (OPEN-23). Captured ONCE,
    # here, and deliberately not refreshed per attempt: a regression attempt 1
    # introduced must still be this task's own on attempt 2, and re-reading
    # the baseline inside the loop would launder it into an inheritance.
    inherited = context.failure_baseline
    # Across every attempt, not per attempt: "how long did this task take"
    # is the question, and a task that failed twice before passing cost
    # the user all three tries (C9.6).
    started = time.monotonic()

    def _stop(outcome: Outcome) -> Outcome:
        """Record the elapsed time, save, and return. Assignment BEFORE the
        save, or the ledger on disk reports 0.0 for a task that took a
        minute."""
        # Stored at full precision and rounded only where it is shown.
        # Rounding here made a task that finished in under 10 ms read as
        # 0.0 -- never true of real work, but it also meant the stored
        # number was a display decision rather than a measurement.
        task.seconds = time.monotonic() - started
        # A run that stops mid-task must leave that task PENDING, never
        # IN_PROGRESS: Ledger.resumable() excludes IN_PROGRESS on purpose
        # (ledger.py:91-100), so leaving it there makes `--continue` skip
        # the one task that never finished. This is A1.93 on the STOP_RUN
        # path -- the cancel handler below already does the same thing for
        # the same reason, and the two paths must not disagree.
        if outcome is Outcome.STOP_RUN and task.status is TaskStatus.IN_PROGRESS:
            task.status = TaskStatus.PENDING
        ledger.save(context.paths.ledger_json)
        return outcome

    while task.attempts < context.cfg.agent.max_fix_attempts:
        task.attempts += 1
        task.status = TaskStatus.IN_PROGRESS
        ledger.save(context.paths.ledger_json)

        before = attempt_snapshot(context)
        result = await run_subagent(
            "coder",
            _coder_prompt(task, blocker_text),
            context=context.subagents,
            thread_id=f"{context.subagents.session_id}-{task.id}-a{task.attempts}",
        )

        if result.error:
            # The coder never produced a turn: a build failure, or an
            # exception mid-stream. `runner.py` cannot tell those apart --
            # it catches every Exception and reports `str(exc)` -- so a
            # provider's HTTP 500 arrives here indistinguishable from a
            # missing package.
            #
            # This used to end the run outright, on the reasoning that both
            # are "environment-class and would recur on every remaining
            # task". A 500 is the counterexample, and it cost run
            # `eb2e1e2e2b2c` all eleven of its tasks 693 seconds in
            # (OPEN-33). So: spend the attempt, and stop only once the
            # errors have actually shown they recur.
            context.run_errors += 1
            task.note = f"the coder could not run: {result.error}"
            if context.run_errors >= MAX_CONSECUTIVE_RUN_ERRORS:
                return _stop(Outcome.STOP_RUN)
            ledger.save(context.paths.ledger_json)
            continue

        # A run that happened. Whatever else went wrong with it, the
        # provider is answering -- which is the only thing the counter above
        # is measuring.
        context.run_errors = 0

        if result.halted_reason:
            # A guard fired. Recorded for the summary; the gate below is what
            # says how badly the attempt actually went.
            task.note = result.halted_reason
            _record_halt(task, result, context)

        task.files_touched = changed_since(context, before)
        # No `before is not None` qualifier any more: `attempt_snapshot`
        # always has one, and the qualifier was what disabled this guard
        # outside a git repository (OPEN-13).
        if not task.files_touched:
            # An empty diff is not proof of failure -- it is an absence of
            # evidence. OPEN-27 measured five tasks where the honest reading
            # was "there was nothing to write": an earlier task had already
            # built this one's work, and the coder read the file, said so,
            # and stopped. Ask the gate rather than assuming.
            #
            # `changed_files` scopes the STUB SCAN only
            # (verify/__init__.py:68-70), so this is already a whole-project
            # verdict; the stub scan finds nothing, which is correct, because
            # nothing was written.
            report = await _verify(task, context)
            verdict = verdict_for(report, inherited=inherited)
            context.failure_baseline = failure_keys(report)
            if _confirms_nothing_to_do(report, verdict):
                task.status = TaskStatus.DONE
                task.note = _already_satisfied_note(report)
                outcome = _stop(Outcome.DONE)
                record_task_in_memory(context.paths, task)
                record_task_memory(context, task)
                return outcome
            task.note = _wrote_nothing_note(blocker_text, report)
            ledger.save(context.paths.ledger_json)
            continue

        report = await _verify(task, context)

        # The tester writes tests; it does not re-do the task. Re-verifying
        # in place rather than looping is deliberate: `continue` here would
        # send the *coder* round again and spend an attempt on work that
        # already passed.
        if report.passed and not tested and tests_produced_no_judgement(report):
            tested = True
            tester_result = await run_subagent(
                "tester",
                _tester_prompt(task),
                context=context.subagents,
                thread_id=f"{context.subagents.session_id}-{task.id}-tester",
            )
            _record_halt(task, tester_result, context)
            task.files_touched = changed_since(context, before)
            report = await _verify(task, context)

        if report.escalate:
            task.note = _blocker_text(report)
            return _stop(Outcome.STOP_RUN)

        # Every gate run updates what the NEXT task inherits, including a
        # failing one -- that is the whole point. Set before the branches
        # below so no early return can skip it.
        verdict = verdict_for(report, inherited=inherited)
        context.failure_baseline = failure_keys(report)

        if verdict is PASSED or verdict is INHERITED:
            task.status = TaskStatus.DONE
            # An inherited failure is not this task's blocker, but it is not
            # nothing either: the note is the only place a reader learns the
            # suite was already red when this task started, and why it passed
            # anyway (OPEN-23).
            task.note = "" if verdict is PASSED else _inherited_note(report, inherited)
            outcome = _stop(Outcome.DONE)
            record_task_in_memory(context.paths, task)
            record_task_memory(context, task)
            return outcome

        signature = failure_signature(report)
        blocker_text = _blocker_text(report)
        if signature is not None and signature == task.last_signature:
            task.status = TaskStatus.BLOCKED
            # The blocker, not just the shape of the failure. `task.note` is
            # what consult_planner interpolates verbatim when it asks for a
            # different approach (planner_agent.py), and what
            # record_block_memory files in the palace -- so overwriting it
            # with a content-free string meant the planner was asked to
            # re-plan around a failure it was told nothing about, and the
            # palace stored a bug with no bug in it (CR-C4).
            task.note = f"no progress: the same failure twice\n\n{blocker_text}"
            outcome = _stop(Outcome.BLOCKED)
            record_block_memory(context, task)
            return outcome
        task.last_signature = signature

    task.status = TaskStatus.BLOCKED
    if "wrote nothing" not in task.note and "could not run" not in task.note:
        # Same reason as above (CR-C4): the count is not a blocker. "could
        # not run" is excluded for the same reason "wrote nothing" is -- it
        # names what actually happened, and "3 attempts exhausted" with an
        # empty blocker names nothing (OPEN-33).
        task.note = f"{task.attempts} attempts exhausted\n\n{blocker_text}".rstrip()
    outcome = _stop(Outcome.BLOCKED)
    record_block_memory(context, task)
    return outcome


_STATUS_MARK = {
    TaskStatus.DONE: "[green]✓[/green]",
    TaskStatus.BLOCKED: "[red]✗[/red]",
    TaskStatus.DROPPED: "[yellow]–[/yellow]",
    TaskStatus.PENDING: "[dim]·[/dim]",
    TaskStatus.IN_PROGRESS: "[dim]·[/dim]",
}

_NOT_ATTEMPTED = "never attempted — the run stopped"


def _review_prompt(ledger: Ledger) -> str:
    """Tell the reviewer what this run touched, by name (A1.68).

    `git_diff` shows changes to *tracked* files, and on a greenfield run
    every file is new and untracked, so the reviewer was handed nothing
    and reported nothing -- silently, because an empty review prints
    nothing. It worked exactly when a file was already committed, which is
    the case Rudra is least often pointed at.

    The names come from the ledger rather than from a second git call: the
    loop already recorded them per task, and since A1.66 that record is
    file-level and correct. Blocked tasks are included deliberately --
    half-finished work is what most deserves a second opinion.
    """
    seen: dict[str, None] = {}
    for task in ledger.tasks:
        for path in task.files_touched:
            seen.setdefault(path, None)

    prompt = "Review the changes this run made and report any problems."
    if seen:
        listed = "\n".join(f"- {path}" for path in seen)
        prompt += (
            f"\n\nThese files were written or changed:\n{listed}\n\n"
            "Read them with read_file. git_diff shows changes to files git "
            "already tracks, so on a new project it will show nothing even "
            "though the files above exist."
        )
    return prompt


async def review_once(context: LoopContext, ledger: Ledger) -> None:
    """One advisory pass over everything that changed. Printed, never acted on.

    D9's split: the deterministic gate decides done-or-not; the reviewer
    comments on quality and gates nothing.
    """
    result = await run_subagent(
        "reviewer",
        _review_prompt(ledger),
        context=context.subagents,
        thread_id=f"{context.subagents.session_id}-review",
    )
    if result.text.strip():
        context.console.print("\n[bold]Review[/bold] [dim](advisory)[/dim]")
        context.console.print(result.text)

    # A guard fired, or the reviewer never produced a turn. Say so (OPEN-35).
    # `run_subagent` returns text="" when a guard fires before any prose, and
    # printing only `.text` meant run 36023bb8bdd1 said NOTHING about a
    # reviewer killed mid-read -- which read as a crash. The coder path has
    # recorded this since 9c (`result.halted_reason` -> task.note); this is
    # the same report, on the one path that had none. Still advisory: it
    # gates nothing and changes no exit code.
    reason = result.halted_reason or result.error
    if reason:
        context.console.print(f"\n[dim]Review incomplete: {escape(reason)}[/dim]")


_ARCHITECTURE_PROMPT = """You are updating a project's engineering memory.

Below is the running log of work completed on this project, and the current
Architecture Notes. Rewrite the Architecture Notes so they describe how this
project is built: its layout, its boundaries, and any decision a future
contributor would otherwise have to rediscover.

Write prose, not a changelog. Do not list the tasks back. Do not invent
anything the log does not support. Reply with the notes and nothing else.

## Current Architecture Notes
{notes}

## Session Log
{log}
"""


def record_task_in_memory(paths: Any, task: Task) -> None:
    """Append one completed task to AGENTS.md's Session Log (C7.3).

    Deterministic: the description is the task's own, and `files_touched`
    came from git rather than from the model (S9c). Nothing here may raise
    -- a task that genuinely finished must not be undone by a memory write.

    Absent AGENTS.md is a no-op, not a create: `_ensure_agents_md` owns
    creating it, and inventing one here would produce a memory file for a
    project that never ran a plan.
    """
    from rudra.context.agents_md import append_session_entry, format_entry, write_agents_md

    try:
        path = paths.agents_md
        if not path.is_file():
            return
        entry = format_entry(
            datetime.now(timezone.utc).date().isoformat(),
            task.id,
            task.description,
            tuple(task.files_touched),
        )
        # Atomic: AGENTS.md is durable and is the project's whole
        # cross-session memory, and the `except OSError` below cannot
        # restore a half-written one (CR-A5).
        write_agents_md(path, append_session_entry(path.read_text(encoding="utf-8"), entry))
    except OSError:
        return


def record_task_memory(context: Any, task: Task) -> None:
    """File one completed task in the palace (C8.3).

    The palace twin of record_task_in_memory, and deliberately adjacent to
    it: AGENTS.md is the recent window, capped at 20 (S12.4), and the
    palace is the unbounded history. Writing both from the same place is
    what makes C8.9's "must not diverge" structural rather than a promise.

    No try/except here on purpose -- MemoryStore.write is already
    @degrades-wrapped, and a second guard would swallow a Rudra bug as if
    it were a ChromaDB one.
    """
    store = getattr(context, "memory", None)
    if store is None:
        return
    files = ", ".join(task.files_touched) if task.files_touched else "no files changed"
    store.write(
        MemoryEntry(
            content=f"Completed: {task.description}. Files: {files}.",
            room="tasks",
            added_by="rudra",
        )
    )


def record_block_memory(context: Any, task: Task) -> None:
    """File one blocked task and its blocker (C8.3).

    Bug-to-fix pairs are what make this worth storing: the next run's
    planner sees what stopped the last one before it plans the same thing
    again.
    """
    store = getattr(context, "memory", None)
    if store is None:
        return
    store.write(
        MemoryEntry(
            content=f"Blocked: {task.description}. Reason: {task.note or 'unknown'}.",
            room="blockers",
            added_by="rudra",
        )
    )


def record_plan_memory(store: Any, facts: Any, tasks: Any) -> None:
    """File the approved plan's facts, each with its source (C8.3).

    Facts, not the task list: the tasks are this run's shape and mean
    nothing to the next request, which is why the ledger is volatile
    (D15). A fact carries why it is believed, and that survives.
    """
    if store is None or facts is None:
        return
    for key, fact in facts.items():
        store.write(
            MemoryEntry(
                content=f"{key} = {fact.value} ({fact.source}: {fact.why})",
                room="decisions",
                added_by="rudra",
            )
        )


async def summarise_architecture(context: LoopContext, ledger: Ledger) -> None:
    """Fold the run's Session Log into Architecture Notes. One model call.

    Runs once, at run end, and only when something reached DONE. Wrapped
    so any failure costs polish rather than the run: the deterministic
    entries are already on disk, which is why the Python half is done
    first.

    `context._model` is a test seam -- when absent the model comes from
    build_model("default"). Documented rather than hidden, because an
    undocumented seam is a trap for the next reader.
    """
    from rudra.context.agents_md import replace_section, section_body, write_agents_md

    if not any(task.status is TaskStatus.DONE for task in ledger.tasks):
        return

    try:
        path = context.paths.agents_md
        if not path.is_file():
            return

        text = path.read_text(encoding="utf-8")
        model = getattr(context, "_model", None)
        if model is None:
            from rudra.llm import build_model

            model = build_model("default", context.cfg)

        reply = await model.ainvoke(
            [
                {
                    "role": "user",
                    "content": _ARCHITECTURE_PROMPT.format(
                        notes=section_body(text, "Architecture Notes"),
                        log=section_body(text, "Session Log"),
                    ),
                }
            ]
        )
        notes = str(getattr(reply, "content", "")).strip()
        if notes:
            write_agents_md(path, replace_section(text, "Architecture Notes", notes))
    except Exception:  # noqa: BLE001 - memory is polish; a run must survive it
        context.console.print("[dim]Could not update AGENTS.md architecture notes.[/dim]")


def write_usage_log(path: Path, usage: Any) -> None:
    """Write the run's tally beside permissions.jsonl and verify.log.

    Volatile subtree (D15): regenerated every run, never worth committing.
    Nothing here may raise. A run that finished its work must not be
    reported as failed because its own bookkeeping could not be written --
    the same rule _maybe_auto_branch follows for a branch it cannot make.
    """
    if usage is None or not usage.roles():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(usage.as_log(), indent=2), encoding="utf-8")
    except OSError:
        return


def notice_if_suspended(trace: Any, usage: Any) -> None:
    """Say once, at run end, that the machine slept through part of it.

    Every clock Rudra keeps counts only time the process was running, so a
    suspended machine leaves them all honest and all disagreeing with the
    user's stopwatch. run10 was 4888 seconds of wall clock over 1243
    seconds of counted time, and three sessions read the difference as a
    missing instrument before `pmset -g log` put macOS Deep Idle sleep in
    exactly the three gaps (OPEN-53).

    A NOTICE rather than a print, on OPEN-44's and OPEN-45's precedent: it
    is something that happened TO the run rather than something a model
    did, so it renders at VERBOSE and reaches the debug log at every
    level. `role="rudra"` for the reason the palace uses that name -- it
    marks what Rudra observed itself, as against what an agent said.

    Nothing here may raise, the rule `write_usage_log` follows: a run that
    finished its work must not be reported failed over its own
    bookkeeping (C7.5).
    """
    if trace is None or usage is None:
        return
    try:
        suspended = usage.suspended_seconds(wall_now=time.time(), mono_now=time.monotonic())
        if suspended < SUSPENDED_NOTICE_SECONDS:
            return
        wall = time.time() - usage.started_wall
        trace.notice(
            f"the machine was suspended for {suspended:.1f}s of this run's "
            f"{wall:.1f}s wall clock; every reported duration excludes it",
            role="rudra",
            name="suspended",
        )
    except Exception:  # noqa: BLE001 -- observability never ends a run
        return


def summarise(ledger: Ledger, console: Console, usage: Any = None, cancelled: bool = False) -> Any:
    """Print every task and return the run's result.

    A1.25 died here: there is no filter between what was declared and what
    is reported, and a task that was never attempted says so rather than
    vanishing.
    """
    # Local import: main_agent imports run_loop from this module, so a
    # module-level import would be a cycle. Same pattern build_backend
    # already uses for its backends.
    from rudra.agent.main_agent import AgentResult

    counts = ledger.counts()
    headline = (
        f"Tasks: {counts['requested']} requested · {counts['done']} done · "
        f"{counts['blocked']} blocked · {counts['dropped']} dropped"
    )
    if counts["pending"]:
        headline += f" · {counts['pending']} never attempted"
    if cancelled:
        headline = f"Cancelled — {headline}"
    console.print(f"\n[bold]{headline}[/bold]\n")
    if cancelled:
        # Said here rather than at the signal, because this is where the
        # user learns how much was left: the ledger a cancel leaves is the
        # ledger --continue already knows how to work.
        console.print("[yellow]Stopped by you. Resume with:[/yellow] rudra --continue\n")

    block = render_usage(usage)
    if block:
        console.print(block)
        console.print()

    # A1.89: `degrades` keeps a run alive through a broken palace, and this
    # is the other half of C8.6 -- a mandatory subsystem that quietly did
    # nothing is worse than one that failed. Printed after the tasks are
    # counted and before they are listed, because it explains an empty
    # `rudra memory list` the user is about to be surprised by.
    failure = last_failure()
    if failure:
        console.print("[yellow]Long-term memory failed this run — nothing was recorded.[/yellow]")
        console.print(f"[dim]{failure}[/dim]\n")

    for task in ledger.tasks:
        note = task.note
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            note = _NOT_ATTEMPTED
        # escape(): a task description is model-written and a bracketed
        # word in it would be parsed as a style tag (A1.91's class).
        took = f"  [dim]({task.seconds:.1f}s)[/dim]" if task.seconds >= 0.05 else ""
        console.print(f"  {_STATUS_MARK[task.status]} {task.id}  {escape(task.description)}{took}")
        if note:
            console.print(f"      [dim]{escape(str(note))}[/dim]")

    files: list[str] = []
    for task in ledger.tasks:
        files.extend(task.files_touched)

    success = (
        counts["requested"] > 0
        and counts["done"] > 0
        and not (counts["blocked"] or counts["pending"])
    )
    return AgentResult(
        success=success,
        message=headline,
        files_created=sorted(set(files)),
        files_modified=[],
        iterations=sum(1 for task in ledger.tasks if task.attempts > 0),
        usage=usage,
    )


async def plan(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Ledger:
    """Run the three planning stages and return the ledger they filled.

    Touches nothing in the project: no coder runs here, which is what
    makes `--plan` honest and what lets RudraAgent put an approval gate
    between this and work() (C6.9).

    `ledger` must be the SAME object the planner's tools were bound to --
    otherwise the tasks it adds are invisible. Defaults to a fresh one
    only so tests can drive planning without wiring an agent.
    """
    ledger = ledger if ledger is not None else Ledger()
    # The one save that knows the request, so record it here (C7.2).
    # Every later save passes nothing and keeps it. Without this the field
    # existed and was always "", which a live run found and no unit test
    # could -- they called save(request=...) directly.
    ledger.save(context.paths.ledger_json, request=request)

    # Three stages, in order (C6.7, S10b.1): settle the facts, decide the
    # shape, then declare the work. Each is a separate agent with its own
    # tools, so a stage cannot do another stage's job. Only `breakdown` is
    # ever re-entered, and only by work() below (S10b.3).
    for stage in ("clarify", "architect", "breakdown"):
        await planner(ledger, request, stage=stage, reason="initial")

    return ledger


# How many times a run may block a task and ask the planner to try
# something else before giving up. Small on purpose: each cycle costs
# max_fix_attempts coder invocations plus a planner call, and a planner that
# has not found a working approach in this many tries is not going to
# (CR-C7).
MAX_BLOCKED_CONSULTS = 5


def _stale_failure_list(keys: frozenset[str]) -> str:
    """The unowned failures, one per line, sorted (OPEN-23).

    Sorted rather than in whatever order a set iterates: this text reaches a
    model AND a ledger a human reads, and two runs of the same red suite
    should produce the same lines in the same order.
    """
    return "\n".join(f"  {key}" for key in sorted(keys))


async def work(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger,
) -> Any:
    """Run every pending task to a verdict, review once, and report.

    Consults only the `breakdown` stage, and only on a stall: the facts
    and the architecture were settled by plan(), and a user may since
    have approved them.
    """
    consulted_on_empty = False
    consulted_on_stale = False
    blocked_consults = 0
    cancelled = False

    while True:
        task = ledger.next_pending()
        if task is None:
            # Asked BEFORE "is anything missing?", and that order is the
            # point (OPEN-23). A red suite with an empty ledger is not a
            # question about the plan -- the plan may be complete and the
            # suite still failing on tests no task ever owned. A planner
            # reading a finished plan answers "nothing missing" and the run
            # ends, which is how a red suite used to leave no trace.
            #
            # Here rather than after the loop so a task it adds is actually
            # worked: `continue` re-enters with the new task pending.
            if context.failure_baseline and not consulted_on_stale:
                consulted_on_stale = True
                await planner(
                    ledger,
                    request,
                    stage="breakdown",
                    reason="stale_failures",
                    feedback=_stale_failure_list(context.failure_baseline),
                )
                continue
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
            continue

        try:
            outcome = await run_task(task, ledger, context=context)
        except asyncio.CancelledError:
            # A cooperative stop, not a teardown. The task goes back to
            # PENDING because Ledger.resumable() excludes IN_PROGRESS on
            # purpose (ledger.py:91-100) -- leaving it there is A1.93,
            # where `--continue` silently skips the one task the user
            # actually interrupted.
            if task.status is TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.PENDING
            task.note = "cancelled by the user"
            ledger.save(context.paths.ledger_json)
            cancelled = True
            break
        ledger.save(context.paths.ledger_json)

        if outcome is Outcome.STOP_RUN:
            context.console.print(
                f"\n[bold red]Run stopped early.[/bold red] [dim]{task.note}[/dim]"
            )
            break
        if outcome is Outcome.BLOCKED:
            # Only a stall consults the planner -- never an ordinary success.
            blocked_consults += 1
            if blocked_consults > MAX_BLOCKED_CONSULTS:
                # Bounded. `consulted_on_empty` was the only loop bound and
                # every block reset it, so a planner that answered each
                # "take a DIFFERENT approach" with add_tasks produced a task
                # that blocked, which consulted it again, indefinitely --
                # max_fix_attempts coder calls plus a planner call per
                # cycle, with Ctrl-C as the user's only exit (CR-C7).
                context.console.print(
                    f"\n[bold red]Stopping.[/bold red] [dim]{blocked_consults - 1} tasks "
                    f"blocked and re-planning is not making progress.[/dim]"
                )
                break
            consulted_on_empty = False
            await planner(ledger, request, stage="breakdown", reason="blocked", task=task)

    run_usage = getattr(context, "usage", None)

    # Both of these are model calls, and somebody who just pressed Ctrl-C
    # is not waiting through two more inferences to be told they succeeded
    # in stopping. The usage log is still written: a cancelled run still
    # cost tokens and wall clock, which is exactly when that is worth
    # knowing.
    if not cancelled:
        if any(task.status is TaskStatus.DONE for task in ledger.tasks):
            await review_once(context, ledger)
        await summarise_architecture(context, ledger)

    notice_if_suspended(getattr(context.subagents, "trace", None), run_usage)
    write_usage_log(context.paths.logs / "usage.json", run_usage)
    return summarise(ledger, context.console, run_usage, cancelled=cancelled)


async def run_loop(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Any:
    """Plan, work, verify, and stop. The whole run.

    Kept as the composition of plan() and work() rather than replaced by
    them: it is what every 9c and 10b test drives, and a caller with no
    interest in the seam should not have to know one exists. The seam is
    used by RudraAgent, which puts the approval gate between the two
    (C6.9).

    `planner` is an awaitable called as
    `planner(ledger, request, stage=..., reason=..., task=...)`; it adds
    or drops tasks and records facts through its tools and returns
    nothing. Injected rather than constructed here so the loop is
    testable without a model.

    `ledger` must be the SAME object the planner's tools were bound to --
    otherwise the tasks it adds are invisible here. Defaults to a fresh
    one only so tests can drive the loop without wiring an agent.
    """
    filled = await plan(request, context=context, planner=planner, ledger=ledger)
    return await work(request, context=context, planner=planner, ledger=filled)


__all__ = [
    "LoopContext",
    "Outcome",
    "attempt_snapshot",
    "changed_since",
    "git_snapshot",
    "tree_snapshot",
    "plan",
    "review_once",
    "run_loop",
    "run_task",
    "record_task_in_memory",
    "record_task_memory",
    "record_block_memory",
    "record_plan_memory",
    "summarise",
    "summarise_architecture",
    "notice_if_suspended",
    "write_usage_log",
    "work",
]
