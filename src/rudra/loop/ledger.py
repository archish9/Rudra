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
    it produced, which is exactly the changed-file list 9a's stub scan
    needs.
    """

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    files_touched: tuple[str, ...] = ()
    last_signature: str | None = None
    note: str = ""


@dataclass
class Ledger:
    """Every task this run declared, in declaration order."""

    tasks: list[Task] = field(default_factory=list)

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

    def save(self, path: Path) -> None:
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
        payload = {"tasks": [{**asdict(task), "status": task.status.value} for task in self.tasks]}
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
            )
            for entry in payload.get("tasks", [])
        ]
        return cls(tasks=tasks)


__all__ = ["Ledger", "Task", "TaskStatus"]
