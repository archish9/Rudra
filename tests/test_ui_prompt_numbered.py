"""The fallback driver: type a digit, or a hotkey letter.

Reached when there is no TTY, and the only driver a test can drive with a
plain callable -- which is the seam permissions/approval.py:84 already
uses for the same reason.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from rudra.ui.prompt import run_numbered
from rudra.ui.select import Cancelled, Choice, Chosen, initial

CHOICES = (
    Choice(value="cli", label="CLI", description=""),
    Choice(value="tui", label="TUI", description=""),
    Choice(value="gui", label="GUI", description=""),
)


@pytest.fixture
def console():
    # StringIO, not /dev/null: that path does not exist on Windows, and
    # CLAUDE.md makes cross-platform support a correctness property.
    return Console(file=io.StringIO(), force_terminal=False)


def _reader(*answers):
    remaining = list(answers)
    return lambda: remaining.pop(0) if remaining else ""


def test_a_digit_selects_that_row(console):
    assert run_numbered(initial(CHOICES), console=console, reader=_reader("2")) == Chosen(("tui",))


def test_the_first_row_is_the_default_on_empty_input(console):
    assert run_numbered(initial(CHOICES), console=console, reader=_reader("")) == Chosen(("cli",))


def test_a_hotkey_letter_works_here_too(console):
    choices = (
        Choice(value="approve", label="Approve", description="", hotkey="a"),
        Choice(value="reject", label="Reject", description="", hotkey="r"),
    )
    result = run_numbered(initial(choices), console=console, reader=_reader("r"))
    assert result == Chosen(("reject",))


def test_an_out_of_range_digit_reprompts(console):
    """Rejecting the input rather than the run: the user simply typed a
    number that is not on screen."""
    result = run_numbered(initial(CHOICES), console=console, reader=_reader("9", "1"))
    assert result == Chosen(("cli",))


def test_garbage_reprompts(console):
    result = run_numbered(initial(CHOICES), console=console, reader=_reader("zzz", "3"))
    assert result == Chosen(("gui",))


def test_multi_select_takes_a_comma_list(console):
    result = run_numbered(initial(CHOICES, multi=True), console=console, reader=_reader("1,3"))
    assert result == Chosen(("cli", "gui"))


def test_multi_select_ignores_spacing_and_orders_by_row(console):
    result = run_numbered(initial(CHOICES, multi=True), console=console, reader=_reader(" 3 , 1 "))
    assert result == Chosen(("cli", "gui"))


def test_multi_select_accepts_nothing_as_a_valid_answer(console):
    """'None of these' is a real answer to 'select all that apply'."""
    result = run_numbered(initial(CHOICES, multi=True), console=console, reader=_reader(""))
    assert result == Chosen(())


def test_eof_from_the_reader_is_cancelled(console):
    def reader():
        raise EOFError

    assert run_numbered(initial(CHOICES), console=console, reader=reader) == Cancelled()


def test_keyboard_interrupt_from_the_reader_is_cancelled(console):
    def reader():
        raise KeyboardInterrupt

    assert run_numbered(initial(CHOICES), console=console, reader=reader) == Cancelled()


def test_a_reader_that_never_answers_gives_up_rather_than_looping(console):
    """An unattended pipe returning '' forever must terminate. Single
    select defaults on empty, so drive it with garbage instead."""
    result = run_numbered(initial(CHOICES), console=console, reader=lambda: "nope", max_attempts=3)
    assert result == Cancelled()
