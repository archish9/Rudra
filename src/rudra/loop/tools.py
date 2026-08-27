"""The agent's three ledger tools.

Typed arguments, so the model never emits JSON syntax. That is what lets
the ledger be JSON without reintroducing the parsing problem PLAN.md had:
the format the model writes and the format Rudra reads stopped being the
same artefact (C6.10).

There is deliberately no tool that sets DONE or BLOCKED. Only
loop/engine.py writes those, and DONE only when VerifyReport.passed --
which is how D9's "no LLM judge decides termination" is made structural
rather than a request in a prompt.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from rudra.loop.ledger import Ledger, Task, TaskStatus

# Statuses a task can no longer be dropped out of: the gate has already
# ruled, and letting the model retract that would rewrite history.
_SETTLED = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED})


def _render(ledger: Ledger) -> str:
    if not ledger.tasks:
        return "The ledger has no tasks yet."
    return "\n".join(
        f"{task.id}  [{task.status.value}]  {task.description}" for task in ledger.tasks
    )


def _normalised(text: str) -> str:
    """The whole of the duplicate check: collapse whitespace, casefold.

    Deliberately NOT fuzzy, and deliberately not stemmed. A near-duplicate
    that is genuinely different work would be silently dropped, and the
    failure mode of a false positive here is *work that never happens* --
    strictly worse than the wasted turn a false negative costs. Run7's case
    was byte-identical three times over, so exact-after-normalisation
    catches it (OPEN-43).
    """
    return " ".join(text.split()).casefold()


def _duplicate_refusal(text: str, clash: Task) -> str:
    """Name the collision and a move that works, or this is OPEN-24 again.

    A model told only "no" re-issues the identical call. The refusal names
    the task, the status it is in, and the one action that will succeed.
    """
    quoted = text if len(text) <= 60 else f"{text[:57]}..."
    return (
        f'REJECTED: "{quoted}" duplicates {clash.id}, which is already '
        f"{clash.status.value}. That task exists and will not be worked "
        f"twice, so an identical add_tasks will be refused identically. If "
        f"work is genuinely still outstanding, add a task with a different "
        f"description saying what is still wrong."
    )


def create_ledger_tools(ledger: Ledger, path: Path) -> list:
    """Tools bound to one run's ledger. Every mutation persists immediately.

    Args:
        ledger: The live Ledger the engine also reads. The same object, not
            a copy -- the agent and the loop must not diverge.
        path: Where to persist after each change.
    """

    @tool
    def add_tasks(descriptions: list[str]) -> str:
        """Add tasks to the ledger.

        Each description is one unit of work, in plain language — not a
        filename. Good: "write a CSV parser that handles quoted commas".
        Also good: "write tests for the parser". Bad: "parser.py".

        Call this once with every task you can foresee. You can call it
        again later if you discover more.

        Args:
            descriptions: One entry per task.

        Returns:
            The ids assigned, so you can refer to them later.
        """
        wanted = [text.strip() for text in descriptions if text and text.strip()]
        if not wanted:
            return "REJECTED: give at least one non-empty task description."

        # Every status, not only DONE. A duplicate of a PENDING task is
        # worked twice; a duplicate of a BLOCKED one is the case that ends
        # a run at MAX_BLOCKED_CONSULTS. DROPPED is the arguable exception
        # -- treated the same until a real run shows it blocking (OPEN-43).
        seen = {_normalised(task.description): task for task in ledger.tasks}
        added: list[Task] = []
        refused: list[str] = []
        for text in wanted:
            key = _normalised(text)
            clash = seen.get(key)
            if clash is not None:
                refused.append(_duplicate_refusal(text, clash))
                continue
            task = ledger.add(text)
            # Recorded before the next iteration, so two copies in ONE call
            # collide with each other and not only with the ledger.
            seen[key] = task
            added.append(task)

        if not added:
            return "\n".join(refused)
        # A batch carrying real work and one duplicate adds the work and
        # reports the duplicate: rejecting the whole call would lose the
        # rest, and the model has no way to retry only the good part.
        ledger.save(path)
        listed = ", ".join(f"{task.id} ({task.description})" for task in added)
        message = f"Added {len(added)} task(s): {listed}"
        return "\n".join([message, *refused]) if refused else message

    @tool
    def drop_task(task_id: str, reason: str) -> str:
        """Retract a task that should not be done after all.

        Use this when a task turned out to be unnecessary or wrong — not
        when it is merely hard. A dropped task is reported to the user
        along with your reason.

        Only a task that is still pending can be dropped. One that is
        already done, blocked or dropped has been settled and cannot be
        retracted -- to change course there, call add_tasks instead.

        Args:
            task_id: The id, e.g. "t2".
            reason: Why it should not be done. Required.

        Returns:
            Confirmation, or REJECTED and why.
        """
        if not reason.strip():
            return "REJECTED: dropping a task requires a reason."
        task = ledger.get(task_id)
        if task is None:
            known = ", ".join(item.id for item in ledger.tasks) or "none"
            return f"REJECTED: no task '{task_id}'. Known ids: {known}."
        if task.status in _SETTLED:
            # Name the move that works, or this is a dead end (OPEN-24). A
            # settled status is terminal -- nothing in engine.py moves a
            # task out of one -- so a model told only "no" re-issues the
            # identical call, which is what a planner did three times.
            return (
                f"REJECTED: {task_id} is already {task.status.value}. A settled "
                f"task stays settled and no more work will be spent on it, so an "
                f"identical drop_task will be refused identically. To take a "
                f"different approach, call add_tasks. drop_task only works on a "
                f"task that is still pending."
            )
        task.status = TaskStatus.DROPPED
        task.note = f"dropped: {reason.strip()}"
        ledger.save(path)
        return f"Dropped {task_id}."

    @tool
    def read_ledger() -> str:
        """Show every task and its current status.

        Returns:
            One line per task: id, status, description.
        """
        return _render(ledger)

    return [add_tasks, drop_task, read_ledger]


__all__ = ["create_ledger_tools"]
