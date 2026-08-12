"""The loop: dispatch work, verify it, fix it, and decide when to stop.

The split this module exists to enforce (spec S9c.1): the model decides
what work exists and what to do next; Python decides when a task is done
and when to stop. C6.1 asked for an agent that owns the todo list; D9
forbids an LLM deciding termination. Both hold here because the ledger
tools cannot write DONE and this module is the only thing that can.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.git.core import is_repo, status
from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.subagents import SubagentContext, run_subagent
from rudra.verify import verify_project
from rudra.verify.stubs import source_files


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


def git_snapshot(context: LoopContext) -> frozenset[str] | None:
    """Paths git currently reports as changed, or None outside a repo.

    None is a real answer the caller acts on: without git there is no way
    to tell what an attempt touched, so the gate falls back to scanning
    everything -- the same choice `rudra verify` makes.
    """
    gate = context.subagents.gate
    if not is_repo(context.project_path, gate=gate, console=context.console, cfg=context.cfg):
        return None
    entries = status(context.project_path, gate=gate, console=context.console, cfg=context.cfg)
    return frozenset(entry.path for entry in entries if entry.path)


def changed_since(context: LoopContext, before: frozenset[str] | None) -> tuple[str, ...]:
    """What this attempt touched.

    Read from git rather than from the model. Asking the coder what it
    wrote invites a wrong answer at exactly the moment the answer matters,
    because it feeds 9a's stub scan.
    """
    if before is None:
        return source_files(context.project_path)
    after = git_snapshot(context)
    if after is None:  # pragma: no cover - a repo cannot stop being one mid-run
        return source_files(context.project_path)
    return tuple(sorted(after - before))


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


__all__ = ["LoopContext", "Outcome", "changed_since", "git_snapshot", "run_task"]
