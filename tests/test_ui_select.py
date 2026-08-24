"""The selector's state machine: pure, and therefore actually testable.

An arrow-key UI is normally close to untestable. `press` returning either
the next state or a terminal result makes a whole interaction a fold over
keypresses, with no terminal involved -- the same split that made
cli_repl.py the one REPL module with real tests.
"""

from __future__ import annotations

from functools import reduce

from rudra.ui.select import Cancelled, Choice, Chosen, initial, press

CHOICES = (
    Choice(value="cli", label="CLI", description="run todo add"),
    Choice(value="tui", label="TUI", description="interactive terminal"),
    Choice(value="gui", label="GUI", description="desktop app"),
)


def _fold(keys, *, multi=False, choices=CHOICES):
    return reduce(press, keys, initial(choices, multi=multi))


def test_enter_on_the_first_choice_returns_it():
    assert _fold(["enter"]) == Chosen(("cli",))


def test_down_moves_the_cursor():
    assert _fold(["down", "enter"]) == Chosen(("tui",))


def test_up_from_the_top_wraps_to_the_bottom():
    """Wrapping, because a cursor that silently stops feels broken."""
    assert _fold(["up", "enter"]) == Chosen(("gui",))


def test_down_from_the_bottom_wraps_to_the_top():
    assert _fold(["down", "down", "down", "enter"]) == Chosen(("cli",))


def test_escape_is_cancelled_not_a_choice():
    """The safety property. Every call site maps Cancelled to its own safe
    default, and none of them map it to approve."""
    assert _fold(["down", "escape"]) == Cancelled()


def test_ctrl_c_and_eof_are_also_cancelled():
    assert _fold(["c-c"]) == Cancelled()
    assert _fold(["eof"]) == Cancelled()


def test_space_toggles_in_multi_select():
    assert _fold(["space", "down", "space", "enter"], multi=True) == Chosen(("cli", "tui"))


def test_space_toggles_off_again():
    assert _fold(["space", "space", "enter"], multi=True) == Chosen(())


def test_multi_select_returns_choices_in_list_order_not_click_order():
    """Stable output: the fact value must not depend on which order the
    user happened to tick the boxes."""
    assert _fold(["down", "down", "space", "up", "up", "space", "enter"], multi=True) == Chosen(
        ("cli", "gui")
    )


def test_space_does_nothing_in_single_select():
    assert _fold(["space", "enter"]) == Chosen(("cli",))


def test_a_hotkey_chooses_immediately():
    choices = (
        Choice(value="approve", label="Approve", description="", hotkey="a"),
        Choice(value="reject", label="Reject", description="", hotkey="r"),
    )
    assert _fold(["r"], choices=choices) == Chosen(("reject",))


def test_hotkeys_are_case_sensitive():
    """The file approval already distinguishes `a` (approve) from `A`
    (always this file); collapsing them would silently widen a grant."""
    choices = (
        Choice(value="approve", label="Approve", description="", hotkey="a"),
        Choice(value="always", label="Always", description="", hotkey="A"),
    )
    assert _fold(["a"], choices=choices) == Chosen(("approve",))
    assert _fold(["A"], choices=choices) == Chosen(("always",))


def test_an_unknown_key_leaves_the_state_untouched():
    state = initial(CHOICES)
    assert press(state, "q") is state


def test_a_hotkey_is_ignored_when_no_choice_declares_one():
    """ask_user builds choices with hotkey=None, so a stray letter there
    must not select anything."""
    state = initial(CHOICES)
    assert press(state, "a") is state
