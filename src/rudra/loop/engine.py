"""The loop: dispatch work, verify it, fix it, and decide when to stop.

The split this module exists to enforce (spec S9c.1): the model decides
what work exists and what to do next; Python decides when a task is done
and when to stop. C6.1 asked for an agent that owns the todo list; D9
forbids an LLM deciding termination. Both hold here because the ledger
tools cannot write DONE and this module is the only thing that can.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from rich.console import Console

from rudra.context.usage import render_usage
from rudra.git.core import is_repo, status
from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.subagents import SubagentContext, run_subagent
from rudra.verify import verify_project
from rudra.verify.stubs import SKIP_DIRS, source_files


class Outcome(StrEnum):
    """What running one task told the caller to do next."""

    DONE = "done"
    BLOCKED = "blocked"
    STOP_RUN = "stop_run"


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
    return {
        entry.path: _digest(context.project_path / entry.path)
        for entry in entries
        if entry.path and not _is_build_output(entry.path)
    }


def changed_since(context: LoopContext, before: dict[str, str] | None) -> tuple[str, ...]:
    """What this attempt touched.

    Read from git rather than from the model. Asking the coder what it
    wrote invites a wrong answer at exactly the moment the answer matters,
    because it feeds 9a's stub scan.

    A path counts when it is new, gone, or its fingerprint moved. The last
    case is what makes a retry that rewrites an untracked file visible
    (A1.66).
    """
    if before is None:
        return source_files(context.project_path)
    after = git_snapshot(context)
    if after is None:  # pragma: no cover - a repo cannot stop being one mid-run
        return source_files(context.project_path)
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


def _verify(task: Task, context: LoopContext) -> Any:
    """Run the gate over what this task touched."""
    return verify_project(
        context.project_path,
        changed_files=task.files_touched,
        gate=context.subagents.gate,
        console=context.console,
        cfg=context.cfg,
    )


async def run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
    """Write, verify, fix, reverify -- until the gate passes or we stop.

    Only this function writes DONE, and only on VerifyReport.passed.
    """
    tested = False
    blocker_text = ""

    while task.attempts < context.cfg.agent.max_fix_attempts:
        task.attempts += 1
        task.status = TaskStatus.IN_PROGRESS
        ledger.save(context.paths.ledger_json)

        before = git_snapshot(context)
        result = await run_subagent(
            "coder",
            _coder_prompt(task, blocker_text),
            context=context.subagents,
            thread_id=f"{context.subagents.session_id}-{task.id}-a{task.attempts}",
        )

        if result.error:
            # Never ran: a build failure or a mid-stream exception. Both are
            # environment-class and would recur on every remaining task.
            task.note = f"the coder could not run: {result.error}"
            ledger.save(context.paths.ledger_json)
            return Outcome.STOP_RUN

        if result.halted_reason:
            # A guard fired. Recorded for the summary; the gate below is what
            # says how badly the attempt actually went.
            task.note = result.halted_reason

        task.files_touched = changed_since(context, before)
        if before is not None and not task.files_touched:
            task.note = "the coder wrote nothing"
            ledger.save(context.paths.ledger_json)
            continue

        report = _verify(task, context)

        # The tester writes tests; it does not re-do the task. Re-verifying
        # in place rather than looping is deliberate: `continue` here would
        # send the *coder* round again and spend an attempt on work that
        # already passed.
        if report.passed and not tested and tests_produced_no_judgement(report):
            tested = True
            await run_subagent(
                "tester",
                _tester_prompt(task),
                context=context.subagents,
                thread_id=f"{context.subagents.session_id}-{task.id}-tester",
            )
            task.files_touched = changed_since(context, before)
            report = _verify(task, context)

        if report.escalate:
            task.note = _blocker_text(report)
            ledger.save(context.paths.ledger_json)
            return Outcome.STOP_RUN

        if report.passed:
            task.status = TaskStatus.DONE
            task.note = ""
            ledger.save(context.paths.ledger_json)
            return Outcome.DONE

        signature = failure_signature(report)
        blocker_text = _blocker_text(report)
        if signature is not None and signature == task.last_signature:
            task.status = TaskStatus.BLOCKED
            task.note = "no progress: the same failure twice"
            ledger.save(context.paths.ledger_json)
            return Outcome.BLOCKED
        task.last_signature = signature

    task.status = TaskStatus.BLOCKED
    if "wrote nothing" not in task.note:
        task.note = f"{task.attempts} attempts exhausted"
    ledger.save(context.paths.ledger_json)
    return Outcome.BLOCKED


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
        path.write_text(json.dumps(usage.as_dict(), indent=2), encoding="utf-8")
    except OSError:
        return


def summarise(ledger: Ledger, console: Console, usage: Any = None) -> Any:
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
    console.print(f"\n[bold]{headline}[/bold]\n")

    block = render_usage(usage)
    if block:
        console.print(block)
        console.print()

    for task in ledger.tasks:
        note = task.note
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            note = _NOT_ATTEMPTED
        console.print(f"  {_STATUS_MARK[task.status]} {task.id}  {task.description}")
        if note:
            console.print(f"      [dim]{note}[/dim]")

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
    ledger.save(context.paths.ledger_json)

    # Three stages, in order (C6.7, S10b.1): settle the facts, decide the
    # shape, then declare the work. Each is a separate agent with its own
    # tools, so a stage cannot do another stage's job. Only `breakdown` is
    # ever re-entered, and only by work() below (S10b.3).
    for stage in ("clarify", "architect", "breakdown"):
        await planner(ledger, request, stage=stage, reason="initial")

    return ledger


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

    while True:
        task = ledger.next_pending()
        if task is None:
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
            continue

        outcome = await run_task(task, ledger, context=context)
        ledger.save(context.paths.ledger_json)

        if outcome is Outcome.STOP_RUN:
            context.console.print(
                f"\n[bold red]Run stopped early.[/bold red] [dim]{task.note}[/dim]"
            )
            break
        if outcome is Outcome.BLOCKED:
            # Only a stall consults the planner -- never an ordinary success.
            consulted_on_empty = False
            await planner(ledger, request, stage="breakdown", reason="blocked", task=task)

    if any(task.status is TaskStatus.DONE for task in ledger.tasks):
        await review_once(context, ledger)
    run_usage = getattr(context, "usage", None)
    write_usage_log(context.paths.logs / "usage.json", run_usage)
    return summarise(ledger, context.console, run_usage)


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
    "changed_since",
    "git_snapshot",
    "plan",
    "review_once",
    "run_loop",
    "run_task",
    "summarise",
    "write_usage_log",
    "work",
]
