"""The arrow-key driver, driven by real key bytes through a real
Application.

prompt_toolkit ships a pipe input and a DummyOutput for exactly this, so
this path gets genuine coverage rather than "tested by hand once".
"""

from __future__ import annotations

import io

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from rudra.ui.prompt import ask, run_inline
from rudra.ui.select import Cancelled, Choice, Chosen, initial

CHOICES = (
    Choice(value="cli", label="CLI", description=""),
    Choice(value="tui", label="TUI", description=""),
)

DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
ESCAPE = "\x1b"
SPACE = " "


@pytest.fixture
def console():
    # StringIO, not /dev/null: that path does not exist on Windows, and
    # CLAUDE.md makes cross-platform support a correctness property.
    return Console(file=io.StringIO(), force_terminal=False)


def _drive(keys: str, state=None, console=None):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        return run_inline(
            state or initial(CHOICES), console=console, input=pipe, output=DummyOutput()
        )


def test_enter_selects_the_first_row(console):
    assert _drive(ENTER, console=console) == Chosen(("cli",))


def test_arrow_down_then_enter_selects_the_second(console):
    assert _drive(DOWN + ENTER, console=console) == Chosen(("tui",))


def test_arrow_up_wraps(console):
    assert _drive(UP + ENTER, console=console) == Chosen(("tui",))


def test_escape_cancels(console):
    assert _drive(ESCAPE + ENTER, console=console) == Cancelled()


def test_space_toggles_in_multi_select(console):
    state = initial(CHOICES, multi=True)
    assert _drive(SPACE + ENTER, state=state, console=console) == Chosen(("cli",))


def test_a_hotkey_selects_without_arrowing(console):
    """The property that keeps the file-approval prompt fast: `a` is one
    keystroke, arrow-then-enter is three."""
    choices = (
        Choice(value="approve", label="Approve", description="", hotkey="a"),
        Choice(value="reject", label="Reject", description="", hotkey="r"),
    )
    result = _drive("r", state=initial(choices), console=console)
    assert result == Chosen(("reject",))


def test_ask_uses_the_numbered_driver_when_a_reader_is_given(console):
    """The seam that keeps permissions/approval.py's existing tests
    working: an injected reader forces the terminal-free path."""
    result = ask(initial(CHOICES), console=console, reader=lambda: "2")
    assert result == Chosen(("tui",))


def test_ask_falls_back_when_there_is_no_tty(console, monkeypatch):
    """Capability, not platform: the question is whether a terminal is
    there, never which OS this is (CLAUDE.md 1.8)."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    result = ask(initial(CHOICES), console=console, reader=lambda: "1")
    assert result == Chosen(("cli",))
