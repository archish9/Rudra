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

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from rudra.loop.ledger import Ledger, TaskStatus
from rudra.ui import Cancelled, Choice, Chosen, initial
from rudra.ui.prompt import ask as ask_selection

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


class PlanDecision(StrEnum):
    """What the user decided about the plan."""

    APPROVE = "approve"
    REVISE = "revise"
    CANCEL = "cancel"


@dataclass(frozen=True)
class PlanAnswer:
    """A decision, plus the words behind a revision."""

    decision: PlanDecision
    feedback: str = ""


_CANCEL = PlanAnswer(PlanDecision.CANCEL)


def auto_approve(console: Console) -> PlanAnswer:
    """Approve without asking. The default, so nothing pauses unbidden.

    Every caller that has not opted into a prompt -- `--auto`, the tests,
    any programmatic use -- gets this, which is why adding the gate
    changes no existing behaviour.
    """
    return PlanAnswer(PlanDecision.APPROVE)


_PLAN_CHOICES = (
    Choice(value="approve", label="Approve", description="run the plan", hotkey="a"),
    Choice(value="revise", label="Revise", description="say what should change", hotkey="r"),
    Choice(value="cancel", label="Cancel", description="change nothing", hotkey="c"),
)


def ask_approval(console: Console, *, reader: Callable[[], str] | None = None) -> PlanAnswer:
    """Ask the user to approve, revise, or cancel the plan.

    EOF and Ctrl-C are cancel, never approve (S10c.5): a plan must not
    execute because a pipe closed or a user gave up. That asymmetry is
    the one safety property this function has, and routing through the
    shared selector does not change it -- `Cancelled` is mapped HERE, per
    call site, precisely so it cannot be flattened into one shared
    default that happens to be permissive.

    The `a`/`r`/`c` hotkeys still work as single keystrokes, so nobody who
    knew them got slower.
    """
    console.print("\n[bold]Proceed?[/bold]")
    outcome = ask_selection(initial(_PLAN_CHOICES), console=console, reader=reader)

    if isinstance(outcome, Cancelled) or not isinstance(outcome, Chosen) or not outcome.values:
        console.print("[dim]No answer — cancelled.[/dim]")
        return _CANCEL

    # Every exit prints a permanent one-line record, because the inline
    # driver erases its rows on the way out and its own docstring says the
    # CALLER prints the record (ui/prompt.py::run_inline). Only the "no
    # answer" branch above used to print one, so an approve left the
    # scrollback holding a question with no answer -- and a gate resolved
    # by a stray keystroke (OPEN-72) looked like a gate nobody was shown.
    choice = outcome.values[0]
    if choice == "approve":
        console.print("[green]Approved[/green] [dim]— running the plan.[/dim]")
        return PlanAnswer(PlanDecision.APPROVE)
    if choice == "cancel":
        console.print("[dim]Cancelled — nothing was executed.[/dim]")
        return _CANCEL

    console.print("[dim]Revising.[/dim]")

    # Revise. An empty answer is a slip and costs one re-prompt; a second
    # empty answer is someone who does not want to revise after all.
    for _ in range(2):
        try:
            feedback = (
                reader()
                if reader is not None
                else Prompt.ask("[bold]What should change?[/bold]", default="")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return _CANCEL
        if feedback:
            return PlanAnswer(PlanDecision.REVISE, feedback)
        console.print("[dim]Say what should change, or press Ctrl-C to cancel.[/dim]")
    return _CANCEL


__all__ = ["PlanAnswer", "PlanDecision", "ask_approval", "auto_approve", "render_plan"]
