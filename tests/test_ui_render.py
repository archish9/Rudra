"""One state, one frame. Pure, so the cursor and the checkboxes are
ordinary assertions rather than a screenshot.

Everything user- or model-supplied is escaped before it reaches Rich: a
label containing `[bold]` must appear on screen, not be parsed as a style
tag (A1.48, A1.76).
"""

from __future__ import annotations

from rudra.ui.render import frame
from rudra.ui.select import Choice, initial, press

CHOICES = (
    Choice(value="cli", label="CLI", description="run todo add"),
    Choice(value="tui", label="TUI", description="interactive terminal"),
)


def test_the_cursor_marks_the_current_row():
    lines = frame(initial(CHOICES))
    assert "❯" in lines[0]
    assert "❯" not in lines[1]


def test_the_cursor_moves_with_the_state():
    lines = frame(press(initial(CHOICES), "down"))
    assert "❯" not in lines[0]
    assert "❯" in lines[1]


def test_every_label_and_description_appears():
    text = "\n".join(frame(initial(CHOICES)))
    for fragment in ("CLI", "run todo add", "TUI", "interactive terminal"):
        assert fragment in text


def test_multi_select_shows_checkboxes():
    state = press(initial(CHOICES, multi=True), "space")
    text = "\n".join(frame(state))
    assert "◉" in text
    assert "◯" in text


def test_single_select_shows_no_checkboxes():
    text = "\n".join(frame(initial(CHOICES)))
    assert "◉" not in text
    assert "◯" not in text


def test_numbered_mode_numbers_every_row():
    lines = frame(initial(CHOICES), numbered=True)
    assert "1." in lines[0]
    assert "2." in lines[1]


def test_numbered_mode_still_marks_nothing_with_a_cursor():
    """The numbered driver cannot move a cursor -- the user types a digit
    -- so a cursor marker there would be a lie about what the keys do."""
    lines = frame(initial(CHOICES), numbered=True)
    assert "❯" not in "\n".join(lines)


def test_a_hotkey_is_shown_when_a_choice_declares_one():
    choices = (Choice(value="approve", label="Approve", description="", hotkey="a"),)
    assert "(a)" in frame(initial(choices))[0]


def test_markup_in_a_label_is_escaped():
    """A model-authored label reaching Rich unescaped loses everything in
    brackets -- the A1.48 defect on a fourth surface."""
    choices = (Choice(value="x", label="[bold]danger[/bold]", description=""),)
    assert "\\[bold]" in frame(initial(choices))[0]


def test_markup_in_a_description_is_escaped():
    choices = (Choice(value="x", label="ok", description="see [dim]this[/dim]"),)
    assert "\\[dim]" in frame(initial(choices))[0]


def test_no_choices_renders_no_rows():
    assert frame(initial(())) == []
