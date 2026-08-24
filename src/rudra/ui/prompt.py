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


def run_inline(
    state: SelectState,
    *,
    console: Any,
    input: Any = None,  # noqa: A002 - prompt_toolkit's own parameter name
    output: Any = None,
) -> Chosen | Cancelled:
    """Draw the rows inline, read keys, return a result.

    `full_screen=False` on purpose: a full-screen Application clears the
    terminal, which would wipe the run trace the user is reading. The
    widget erases itself on exit and the CALLER prints a one-line record
    of what was chosen, so scrollback does not fill with dead menus.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    held: dict[str, Any] = {"state": state, "result": None}

    def _apply(key: str) -> None:
        outcome = press(held["state"], key)
        if isinstance(outcome, (Chosen, Cancelled)):
            held["result"] = outcome
            application.exit()
        else:
            held["state"] = outcome

    bindings = KeyBindings()
    for binding, name in (
        ("up", "up"),
        ("down", "down"),
        ("enter", "enter"),
        ("escape", "escape"),
        ("c-c", "c-c"),
        ("c-d", "eof"),
        ("space", "space"),
    ):
        bindings.add(binding)(lambda event, name=name: _apply(name))

    # Hotkeys are bound individually and case-sensitively: `a` and `A` are
    # different actions in the file-approval prompt.
    for choice in state.choices:
        if choice.hotkey is not None:
            bindings.add(choice.hotkey)(lambda event, key=choice.hotkey: _apply(key))

    def _text() -> str:
        # Rich markup is meaningless to prompt_toolkit, so the rows are
        # rendered plainly here. The permanent record the caller prints
        # afterwards is the styled one.
        return "\n".join(_plain(line) for line in frame(held["state"]))

    application = Application(
        layout=Layout(Window(FormattedTextControl(_text), always_hide_cursor=True)),
        key_bindings=bindings,
        full_screen=False,
        # The property spec 4.3 names: the widget erases itself on exit
        # and the CALLER prints the permanent one-line record. Without
        # this the menu stays on screen and scrollback fills with dead
        # ones -- the wall-of-text complaint by another route.
        erase_when_done=True,
        input=input,
        output=output,
    )
    application.run()
    return held["result"] if held["result"] is not None else Cancelled()


def _plain(line: str) -> str:
    """Strip Rich markup for prompt_toolkit's plain-text control."""
    from rich.text import Text

    return Text.from_markup(line).plain


def ask(
    state: SelectState,
    *,
    console: Any,
    reader: Callable[[], str] | None = None,
    input: Any = None,  # noqa: A002 - matches run_inline
    output: Any = None,
) -> Chosen | Cancelled:
    """Run the selector on whichever driver this environment supports.

    Capability, never platform (CLAUDE.md 1.8): the question is whether a
    terminal is actually there and whether an Application constructs, not
    which OS this is. An explicit `reader` forces the numbered driver,
    which is the seam that keeps the existing approval tests terminal-free.
    """
    if reader is not None:
        return run_numbered(state, console=console, reader=reader)
    if not _has_terminal():
        return run_numbered(state, console=console, reader=_stdin_reader)
    try:
        return run_inline(state, console=console, input=input, output=output)
    except Exception:  # noqa: BLE001 - any terminal that refuses gets the fallback
        return run_numbered(state, console=console, reader=_stdin_reader)


def _has_terminal() -> bool:
    import sys

    return bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )


def _stdin_reader() -> str:
    return input()


__all__ = ["MAX_ATTEMPTS", "ask", "run_inline", "run_numbered"]
