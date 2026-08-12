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

from rudra.loop.ledger import Ledger, TaskStatus

# Statuses a task can no longer be dropped out of: the gate has already
# ruled, and letting the model retract that would rewrite history.
_SETTLED = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED})


def _render(ledger: Ledger) -> str:
    if not ledger.tasks:
        return "The ledger has no tasks yet."
    return "\n".join(
        f"{task.id}  [{task.status.value}]  {task.description}" for task in ledger.tasks
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
        added = [ledger.add(text) for text in wanted]
        ledger.save(path)
        listed = ", ".join(f"{task.id} ({task.description})" for task in added)
        return f"Added {len(added)} task(s): {listed}"

    @tool
    def drop_task(task_id: str, reason: str) -> str:
        """Retract a task that should not be done after all.

        Use this when a task turned out to be unnecessary or wrong — not
        when it is merely hard. A dropped task is reported to the user
        along with your reason.

        You cannot drop a task that has already finished.

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
            return f"REJECTED: {task_id} is already {task.status.value}."
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
