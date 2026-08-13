"""The approval prompt (Step 10c, C6.9).

Driven with a patched Prompt.ask, so these run with no TTY -- the
property that makes the whole gate testable.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from rudra.loop import plan_view
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve


@pytest.fixture
def console():
    return Console(quiet=True)


def _answers(monkeypatch, *replies):
    remaining = list(replies)

    def fake_ask(*_args, **_kwargs):
        if not remaining:
            raise AssertionError("prompted more times than the test scripted")
        reply = remaining.pop(0)
        if isinstance(reply, type) and issubclass(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(plan_view.Prompt, "ask", fake_ask)
    return remaining


def test_a_approves(monkeypatch, console):
    _answers(monkeypatch, "a")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.APPROVE
    assert answer.feedback == ""


def test_c_cancels(monkeypatch, console):
    _answers(monkeypatch, "c")
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_r_collects_feedback(monkeypatch, console):
    _answers(monkeypatch, "r", "drop the tests task, I have my own")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "drop the tests task, I have my own"


def test_empty_feedback_is_asked_again(monkeypatch, console):
    remaining = _answers(monkeypatch, "r", "   ", "actually split task two")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "actually split task two"
    assert remaining == [], "the empty answer must have cost a re-prompt"


def test_empty_feedback_twice_is_a_cancel(monkeypatch, console):
    """One slip is a slip; two is someone who does not want to revise."""
    _answers(monkeypatch, "r", "", "")
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_eof_at_the_choice_is_cancel_not_approve(monkeypatch, console):
    """S10c.5. A plan must not run because a pipe closed."""
    _answers(monkeypatch, EOFError)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_eof_while_collecting_feedback_is_cancel(monkeypatch, console):
    _answers(monkeypatch, "r", EOFError)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_keyboard_interrupt_is_cancel(monkeypatch, console):
    _answers(monkeypatch, KeyboardInterrupt)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_auto_approve_never_prompts(monkeypatch, console):
    def explode(*_args, **_kwargs):
        raise AssertionError("auto approval must not prompt")

    monkeypatch.setattr(plan_view.Prompt, "ask", explode)
    assert auto_approve(console).decision is PlanDecision.APPROVE


def test_the_prompt_shows_which_key_does_what(monkeypatch):
    """A1.76: Rich ate [a], [r] and [c], leaving "pprove evise ancel".

    Asserted against the text the user is actually shown, for the reason
    test_plan_render.py does: checking the pre-render string would pass
    against the broken version, since "[a]pprove" contains "[a]pprove".
    """
    from rich.console import Console

    shown = Console(file=None, record=True, width=200)
    captured: dict = {}

    def fake_ask(prompt, **_kwargs):
        captured["prompt"] = prompt
        shown.print(prompt)
        return "a"

    monkeypatch.setattr(plan_view.Prompt, "ask", fake_ask)
    ask_approval(Console(quiet=True))

    rendered = shown.export_text()
    assert "[a]pprove" in rendered, rendered
    assert "[r]evise" in rendered, rendered
    assert "[c]ancel" in rendered, rendered
