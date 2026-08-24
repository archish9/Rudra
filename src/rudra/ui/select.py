"""The selector's state machine. Pure, and imports nothing from Rudra.

`press` returns either the next state or a terminal result, which makes a
whole interaction a fold over keypresses:

    reduce(press, ["down", "down", "enter"], initial(choices))

That is what makes an arrow-key UI testable without a TTY, a pty, or a
mock -- the property `cli_repl.py` established for the REPL.

Keys are named, not raw bytes: "up", "down", "space", "enter", "escape",
"c-c", "eof", or a single character for a hotkey. Both drivers translate
their own input into these names, so neither can drift from the other.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

CANCEL_KEYS = frozenset({"escape", "c-c", "eof"})
"""Every way of giving up. All three mean Cancelled, never a selection --
each call site then maps that to its own safe default (spec 4.3)."""


@dataclass(frozen=True)
class Choice:
    """One selectable row."""

    value: str
    """What the caller gets back. Never shown."""

    label: str
    description: str = ""

    hotkey: str | None = None
    """A single character that selects this row outright. Case-sensitive:
    the file approval distinguishes `a` (approve) from `A` (always).
    `ask_user` leaves this None -- a letter there must not select."""


@dataclass(frozen=True)
class Chosen:
    """A terminal result: what the user picked, in choice order."""

    values: tuple[str, ...]


@dataclass(frozen=True)
class Cancelled:
    """A terminal result: the user gave up, or the input closed."""


@dataclass(frozen=True)
class SelectState:
    choices: tuple[Choice, ...]
    cursor: int = 0
    selected: frozenset[int] = frozenset()
    multi: bool = False


def initial(choices: tuple[Choice, ...], *, multi: bool = False) -> SelectState:
    """The state before any keypress."""
    return SelectState(choices=tuple(choices), cursor=0, selected=frozenset(), multi=multi)


def press(state: SelectState, key: str) -> SelectState | Chosen | Cancelled:
    """One keypress: the next state, or a terminal result.

    An unrecognised key returns the SAME object, so a caller can cheaply
    tell "nothing happened" from "state changed" with `is`.
    """
    if key in CANCEL_KEYS:
        return Cancelled()

    count = len(state.choices)
    if count == 0:
        # No rows to move through; only cancelling makes sense.
        return state

    if key == "up":
        return replace(state, cursor=(state.cursor - 1) % count)
    if key == "down":
        return replace(state, cursor=(state.cursor + 1) % count)

    if key == "space" and state.multi:
        selected = set(state.selected)
        selected.symmetric_difference_update({state.cursor})
        return replace(state, selected=frozenset(selected))

    if key == "enter":
        if state.multi:
            # sorted(): the answer must not depend on the order the user
            # happened to tick the boxes.
            return Chosen(tuple(state.choices[i].value for i in sorted(state.selected)))
        return Chosen((state.choices[state.cursor].value,))

    for choice in state.choices:
        if choice.hotkey is not None and choice.hotkey == key:
            return Chosen((choice.value,))

    return state


__all__ = [
    "CANCEL_KEYS",
    "Cancelled",
    "Choice",
    "Chosen",
    "SelectState",
    "initial",
    "press",
]
