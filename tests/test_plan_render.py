"""What the user sees before approving (Step 10c, C6.9)."""

from __future__ import annotations

from rudra.facts import FactStore
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.loop.plan_view import render_plan


def _ledger(*descriptions: str) -> Ledger:
    ledger = Ledger()
    for description in descriptions:
        ledger.add(description)
    return ledger


def test_every_task_appears_with_its_id():
    text = render_plan(_ledger("write the parser", "write tests"))
    assert "t1" in text and "write the parser" in text
    assert "t2" in text and "write tests" in text


def test_facts_appear_with_their_source():
    """The source is the difference between "you told me" and "I guessed"."""
    facts = FactStore()
    facts.record("language", "Rust", "the user asked for Rust", "asked")
    facts.record("layout", "src/parser.rs parsing", "keeps parsing testable", "inferred")

    text = render_plan(_ledger("write it"), facts)

    assert "language" in text and "Rust" in text and "asked" in text
    assert "layout" in text and "inferred" in text


def test_no_facts_renders_no_facts_section():
    text = render_plan(_ledger("write it"), FactStore())
    assert "write it" in text
    assert "facts" not in text.lower()


def test_no_facts_argument_at_all_is_fine():
    assert "write it" in render_plan(_ledger("write it"))


def test_an_empty_plan_says_so_rather_than_rendering_nothing():
    text = render_plan(Ledger())
    assert text.strip(), "an empty plan must still produce a sentence"
    assert "no task" in text.lower()


def _printed(ledger: Ledger, facts=None) -> str:
    """What a real console actually shows.

    Asserted against the rendered output rather than the markup string,
    because escaping deliberately changes that string: `[bold]` becomes
    `\\[bold]` on the way in and `[bold]` again on the way out. Checking
    the pre-render text would pass for the wrong reason -- and did, in
    this file's first draft.
    """
    from rich.console import Console

    console = Console(file=None, record=True, width=200)
    console.print(render_plan(ledger, facts))
    return console.export_text()


def test_markup_in_a_task_description_reaches_the_screen():
    """A1.48/A1.67's class: Rich eats [word] unless it is escaped."""
    assert "[bold]" in _printed(_ledger("handle [bold] markers in input"))


def test_markup_in_a_fact_value_reaches_the_screen():
    facts = FactStore()
    facts.record("style", "[dim]never[/dim]", "the user pasted it", "asked")
    assert "[dim]never[/dim]" in _printed(_ledger("write it"), facts)


def test_an_unescaped_renderer_would_fail_these_tests():
    """Proof the guard above can actually fail.

    A test that passes against both the fixed and the broken code is not
    a guard. This renders the same value without escaping and asserts the
    markup vanishes, which is exactly what the real defect looked like.
    """
    from rich.console import Console

    console = Console(file=None, record=True, width=200)
    console.print("style = [dim]never[/dim]")
    assert "[dim]never[/dim]" not in console.export_text()


def test_a_dropped_task_is_not_presented_as_work():
    """Dropped tasks are history, not plan."""
    ledger = _ledger("write it", "do not do this")
    ledger.tasks[1].status = TaskStatus.DROPPED

    text = render_plan(ledger)

    assert "write it" in text
    assert "do not do this" not in text
