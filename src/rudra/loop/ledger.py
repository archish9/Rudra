"""What the run is trying to do, and how far it got.

Supersedes the bare-filename checklist in .rudra/run/PLAN.md (C6.10). That
file was prose the orchestrator parsed, which is why a plan item could be
silently dropped between what was requested and what was reported (A1.25).
Here the agent writes through typed tools and Rudra reads a structured
record, so the two are no longer the same artefact.

Pure data plus one file. Imports nothing from Rudra, so its tests need no
model, no backend and no gate.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class TaskStatus(StrEnum):
    """Where one task got to.

    DONE and BLOCKED are written by loop/engine.py and by nothing else --
    no agent-facing tool can produce them, which is how D9's "no LLM judge
    decides termination" is enforced structurally rather than by asking.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    DROPPED = "dropped"


@dataclass
class Task:
    """One unit of work the agent declared.

    A task is work, not a filename (spec S9c.2): "write tests for the
    parser" has no filename until it is done. `files_touched` records what
    it produced, across EVERY attempt (OPEN-123) -- it only ever grows --
    which is exactly the changed-file list 9a's syntax stage and stub scan
    need.
    """

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    files_touched: tuple[str, ...] = ()
    last_signature: str | None = None
    note: str = ""
    # Every guard halt this task's subagents hit, in the order they
    # happened (OPEN-44). Beside `note` rather than in it, because the two
    # have different readers: `note` is interpolated verbatim into
    # consult_planner's prompt and filed in the palace by
    # record_block_memory (CR-C4), so a model reads it, while this is a
    # record of what RUDRA did -- and, critically, every branch that
    # rewrites `note` used to erase the halt with it, including the
    # passing one, which is the case a reader most needs it in.
    halts: tuple[str, ...] = ()
    # Every subagent invocation on this task that never ran at all, in the
    # order they happened (OPEN-46). Beside `halts` rather than in it,
    # because they are different events: a halt is an invocation Rudra
    # STOPPED, and this is one the provider never started -- a build
    # failure, or a retry budget that ran out.
    #
    # A list for exactly the reason `halts` is one. `note` was the only
    # place this landed and every branch that finishes a task rewrites it,
    # the passing one clearing it outright -- so run14's exhausted retry
    # budget, which cost task t8 outright, left nothing on disk anybody
    # could count afterwards. That is OPEN-44's failure one field over.
    run_errors: tuple[str, ...] = ()
    # Every gate whose only failures were packages nobody declared, one entry
    # per gate naming them, in order (OPEN-161). Each gave its attempt back,
    # so `attempts` alone no longer says how many gates the task drew; this
    # is the rest of that count, and a list for `halts`' reason.
    dependency_gates: tuple[str, ...] = ()
    # Whether the attempt that ran the fix budget out was converging, as
    # loop/bounds.py::convergence reads it: "converging", "not converging",
    # "unreadable", or "" when the task did not end that way (OPEN-160). A
    # field beside the note, whose second line says the same, because a note
    # is rewritten by every branch that finishes a task (CLAUDE.md 8a shape 4).
    convergence: str = ""
    # Wall clock this task consumed, in seconds (C9.6, Step 15a). Recorded
    # by loop/engine.py at every exit from run_task, including the failing
    # ones: a task that burned three attempts is the one a user most wants
    # the number for. `save` uses asdict, so nothing there needed changing.
    seconds: float = 0.0


@dataclass
class Ledger:
    """Every task this run declared, in declaration order."""

    tasks: list[Task] = field(default_factory=list)
    # The request this plan was built for (C7.2). Resume compares against
    # it: working an old plan against a new intent is worse than refusing.
    request: str = ""
    # When it was last written, ISO-8601. Shown in the resume summary so a
    # user can tell a ledger from this morning from one from last week.
    saved_at: str = ""
    # Has the over-decomposition guard already refused a list this run
    # (OPEN-90)? Deliberately NOT persisted -- `save` builds its payload
    # field by field, so this stays out of ledger.json -- because it is a
    # property of one run's conversation with the planner, not of the plan.
    #
    # It lives here rather than in create_ledger_tools' closure because
    # `breakdown` is re-entered on a block or an empty ledger and each
    # re-consult builds a fresh agent, so a closure flag would reset and
    # the guard could refuse for ever. A guard that refuses for ever turns
    # a bad plan into a run with no plan at all, which is worse than the
    # plan it refused.
    decomposition_refused: bool = False

    def add(self, description: str) -> Task:
        """Append a task and return it.

        Ids are `t1`, `t2`, ... -- short enough to quote in a prompt and
        stable for the run's lifetime.
        """
        task = Task(id=f"t{len(self.tasks) + 1}", description=description)
        self.tasks.append(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def next_pending(self) -> Task | None:
        return next((task for task in self.tasks if task.status is TaskStatus.PENDING), None)

    def resumable(self) -> tuple[Task, ...]:
        """The tasks a resume would work, in declaration order.

        PENDING only. BLOCKED hit two identical failure signatures
        (C6.5a) or spent its fix budget, and retrying it identically burns a
        run to reach the same place -- an exhausted task may have been
        converging, which its `convergence` field says, and the lever for
        that is `max_fix_attempts` on a fresh run (OPEN-154, OPEN-160); DONE
        and DROPPED are finished. IN_PROGRESS is deliberately
        excluded too: it means the process died mid-task, and the coder's
        partial work is already on disk for the next attempt to see.
        """
        return tuple(task for task in self.tasks if task.status is TaskStatus.PENDING)

    def counts(self) -> dict[str, int]:
        """Requested, and the terminal buckets. They always add up.

        `pending` is non-zero only when the run stopped early, which is
        precisely when the user most needs to see it (spec §6).
        """
        tally = {status.value: 0 for status in TaskStatus}
        for task in self.tasks:
            tally[task.status.value] += 1
        return {
            "requested": len(self.tasks),
            "done": tally[TaskStatus.DONE.value],
            "blocked": tally[TaskStatus.BLOCKED.value],
            "dropped": tally[TaskStatus.DROPPED.value],
            "pending": tally[TaskStatus.PENDING.value] + tally[TaskStatus.IN_PROGRESS.value],
        }

    def save(self, path: Path, request: str | None = None) -> None:
        """Write atomically: a private temp file, then os.replace.

        A crash mid-write must leave the previous ledger readable rather
        than a truncated one. This file is the only record of how far a run
        got, and it is written after every status change.

        The temp name is unique per writer (A1.71). A fixed `.tmp` is
        correct for one writer and wrong for two: deepagents runs a turn's
        tool calls concurrently, so the first os.replace consumes the file
        and the second raises FileNotFoundError. Measured on the fact
        store, whose tool is called once per fact; this one has the same
        shape and survived only because add_tasks takes a list.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # None means "whatever it already said". Every mid-run save passes
        # nothing, and erasing the request on each status change would
        # leave resume with nothing to compare against.
        if request is not None:
            self.request = request
        self.saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "request": self.request,
            "saved_at": self.saved_at,
            "tasks": [{**asdict(task), "status": task.status.value} for task in self.tasks],
        }
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path) -> Ledger:
        """Read a ledger, or an empty one when the file is absent."""
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        tasks = [
            Task(
                id=entry["id"],
                description=entry["description"],
                status=TaskStatus(entry["status"]),
                attempts=entry.get("attempts", 0),
                files_touched=tuple(entry.get("files_touched", ())),
                last_signature=entry.get("last_signature"),
                note=entry.get("note", ""),
                halts=tuple(entry.get("halts", ())),
                run_errors=tuple(entry.get("run_errors", ())),
                dependency_gates=tuple(entry.get("dependency_gates", ())),
                convergence=entry.get("convergence", ""),
                # .get, not [...]: a ledger written by an older Rudra is a
                # volatile file, but a run in flight during an upgrade
                # must not crash on it.
                seconds=entry.get("seconds", 0.0),
            )
            for entry in payload.get("tasks", [])
        ]
        return cls(
            tasks=tasks,
            request=payload.get("request", ""),
            saved_at=payload.get("saved_at", ""),
        )


__all__ = ["Ledger", "Task", "TaskStatus"]
