"""One state, one frame of lines. Pure: no console, no terminal.

Escaping happens HERE, at the point of rendering, for the reason
plan_view.py states: a label or description is user- or model-supplied,
and Rich parses `[bold]` as a style tag and prints nothing (A1.48). The
rule is not "escape model output" -- it is "escape anything with
brackets", so Rudra's own `(a)` hints go through the same path.
"""

from __future__ import annotations

from rich.markup import escape

from rudra.ui.select import SelectState

CURSOR = "❯"
CHECKED = "◉"
UNCHECKED = "◯"


def frame(state: SelectState, *, numbered: bool = False) -> list[str]:
    """The rows for this state, as Rich-markup lines.

    `numbered` renders `1.` `2.` instead of a cursor, for the fallback
    driver. No cursor marker there on purpose: that driver cannot move a
    cursor, so drawing one would misdescribe the keys.
    """
    lines: list[str] = []
    for index, choice in enumerate(state.choices):
        parts: list[str] = []

        if numbered:
            parts.append(f"  {index + 1}.")
        else:
            parts.append(f"  {CURSOR}" if index == state.cursor else "   ")

        if state.multi:
            parts.append(CHECKED if index in state.selected else UNCHECKED)

        label = escape(choice.label)
        parts.append(f"[bold]{label}[/bold]" if index == state.cursor and not numbered else label)

        if choice.hotkey is not None:
            parts.append(f"[dim]({escape(choice.hotkey)})[/dim]")
        if choice.description:
            parts.append(f"[dim]{escape(choice.description)}[/dim]")

        lines.append(" ".join(parts))
    return lines


__all__ = ["CHECKED", "CURSOR", "UNCHECKED", "frame"]
