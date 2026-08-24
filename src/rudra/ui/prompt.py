"""The two drivers, and the choice between them.

Both feed the SAME `press`, so the arrow-key path and the numbered path
cannot disagree about what a key means -- only about how the rows are
drawn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from rudra.ui.render import frame
from rudra.ui.select import Cancelled, Chosen, SelectState, press

MAX_ATTEMPTS = 5
"""Re-prompts before a numbered driver gives up and returns Cancelled.

A reader that never produces a usable answer -- a closed pipe returning
'' forever -- must terminate rather than spin. Cancelled and not a
default selection, because the safe answer is never the permissive one.
"""


def run_numbered(
    state: SelectState,
    *,
    console: Any,
    reader: Callable[[], str],
    max_attempts: int = MAX_ATTEMPTS,
) -> Chosen | Cancelled:
    """Print the rows, read a line, resolve it. No terminal control."""
    for _ in range(max_attempts):
        for line in frame(state, numbered=True):
            console.print(line)
        console.print(_hint(state), markup=False)

        try:
            raw = (reader() or "").strip()
        except (EOFError, KeyboardInterrupt):
            return Cancelled()

        # A hotkey letter resolves through the same `press` the arrow-key
        # driver uses, so `a` means the same thing in both.
        outcome = press(state, raw)
        if isinstance(outcome, (Chosen, Cancelled)):
            return outcome

        resolved = _resolve(state, raw)
        if resolved is not None:
            return resolved
        console.print("Not a valid choice — enter a number from the list.", markup=False)

    return Cancelled()


def _hint(state: SelectState) -> str:
    if state.multi:
        return "Choose one or more, comma-separated (enter for none):"
    return "Choose [1]:"


def _resolve(state: SelectState, raw: str) -> Chosen | None:
    """A typed line to a result, or None when it is not usable."""
    count = len(state.choices)

    if state.multi:
        if not raw:
            # "None of these" is a real answer to "select all that apply".
            return Chosen(())
        picked: set[int] = set()
        for piece in raw.split(","):
            stripped = piece.strip()
            if not stripped.isdigit():
                return None
            index = int(stripped) - 1
            if not 0 <= index < count:
                return None
            picked.add(index)
        return Chosen(tuple(state.choices[i].value for i in sorted(picked)))

    if not raw:
        # Single select defaults to the first row, matching the `(a)`
        # default the plan gate has always had.
        return Chosen((state.choices[0].value,)) if count else None
    if not raw.isdigit():
        return None
    index = int(raw) - 1
    if not 0 <= index < count:
        return None
    outcome = press(replace(state, cursor=index), "enter")
    return outcome if isinstance(outcome, Chosen) else None


__all__ = ["MAX_ATTEMPTS", "run_numbered"]
