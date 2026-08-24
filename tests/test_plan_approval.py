"""The plan gate. Driven through an injected reader, so these run with no
TTY -- the seam permissions/approval.py:84 established.

The safety property is the whole point of this file: EOF and Ctrl-C are
cancel, NEVER approve (S10c.5). A plan must not execute because a pipe
closed or a user gave up. Routing three call sites through one shared
selector is exactly where that asymmetry could get flattened into a
single permissive default, so it is asserted here per call site.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from rudra.loop import plan_view
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve


@pytest.fixture
def console():
    # StringIO, not /dev/null: that path does not exist on Windows, and
    # CLAUDE.md makes cross-platform support a correctness property.
    return Console(file=io.StringIO(), force_terminal=False)


def _reader(*answers):
    remaining = list(answers)
    return lambda: remaining.pop(0) if remaining else ""


def test_a_approves(console):
    assert ask_approval(console, reader=_reader("a")).decision is PlanDecision.APPROVE


def test_the_first_row_is_still_the_default(console):
    """Empty input approved before this change and must keep doing so."""
    assert ask_approval(console, reader=_reader("")).decision is PlanDecision.APPROVE


def test_c_cancels(console):
    assert ask_approval(console, reader=_reader("c")).decision is PlanDecision.CANCEL


def test_r_collects_feedback(console):
    answer = ask_approval(console, reader=_reader("r", "drop the tests task"))
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "drop the tests task"


def test_the_revision_words_reach_the_planner_verbatim(console):
    """C6.9: a revision re-enters breakdown with the user's own words. A
    paraphrase would silently replan against something they did not say."""
    words = "drop task two, and use argparse not click"
    assert ask_approval(console, reader=_reader("r", words)).feedback == words


def test_empty_feedback_is_asked_again(console):
    answer = ask_approval(console, reader=_reader("r", "   ", "split task two"))
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "split task two"


def test_empty_feedback_twice_is_a_cancel(console):
    assert ask_approval(console, reader=_reader("r", "", "")).decision is PlanDecision.CANCEL


def test_eof_at_the_choice_is_cancel_not_approve(console):
    def reader():
        raise EOFError

    assert ask_approval(console, reader=reader).decision is PlanDecision.CANCEL


def test_keyboard_interrupt_is_cancel_not_approve(console):
    def reader():
        raise KeyboardInterrupt

    assert ask_approval(console, reader=reader).decision is PlanDecision.CANCEL


def test_eof_while_collecting_feedback_is_cancel(console):
    answers = iter(["r"])

    def reader():
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    assert ask_approval(console, reader=reader).decision is PlanDecision.CANCEL


def test_auto_approve_never_prompts(console, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("auto_approve must not prompt")

    monkeypatch.setattr(plan_view, "ask_selection", explode)
    assert auto_approve(console).decision is PlanDecision.APPROVE
