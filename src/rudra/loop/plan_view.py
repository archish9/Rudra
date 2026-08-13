"""The plan, as the user sees it before approving (C6.9).

Pure: takes a ledger and a fact store, returns a string. Printing is the
caller's job, which is what makes this testable without a console.

Everything user-supplied is escaped. A task description or fact value
containing `[bold]` must appear on screen, and Rich would otherwise parse
it as a style tag and print nothing -- the defect A1.48 recorded for
config errors and A1.67 for the ledger trace. This is a third surface
with the same exposure, escaped at the point of rendering.
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from rudra.loop.ledger import Ledger, TaskStatus

# What a user is being asked to approve: work not yet attempted. A
# dropped task is history, and a done one cannot be un-approved.
_PRESENTABLE = frozenset({TaskStatus.PENDING, TaskStatus.IN_PROGRESS})


def render_plan(ledger: Ledger, facts: Any = None) -> str:
    """The facts and the tasks, ready to print."""
    lines = ["[bold]Plan[/bold]"]

    entries = list(facts.items()) if facts is not None and facts.facts else []
    if entries:
        lines.append("  [dim]facts:[/dim]")
        lines.extend(
            f"    {escape(key)} = {escape(fact.value)} [dim]({escape(fact.source)})[/dim]"
            for key, fact in entries
        )

    tasks = [task for task in ledger.tasks if task.status in _PRESENTABLE]
    if not tasks:
        lines.append("  [yellow]No tasks were declared.[/yellow]")
        return "\n".join(lines)

    lines.append("  [dim]tasks:[/dim]")
    lines.extend(f"    {task.id}  {escape(task.description)}" for task in tasks)
    return "\n".join(lines)


__all__ = ["render_plan"]
