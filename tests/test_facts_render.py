"""Rendering facts into a system prompt (Step 10a)."""

from __future__ import annotations

from rudra.facts import FactStore, facts_block


def test_an_empty_store_renders_nothing():
    """A greenfield run must add no section and spend no tokens."""
    assert facts_block(FactStore()) == ""


def test_no_store_at_all_renders_nothing():
    assert facts_block(None) == ""


def test_every_fact_appears_with_its_value_source_and_why():
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    block = facts_block(store)

    assert "## PROJECT FACTS" in block
    assert "language" in block
    assert "Rust" in block
    assert "inferred" in block
    assert "user said 'CLI in Rust'" in block
    assert block.endswith("\n")


def test_facts_render_in_insertion_order():
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.record("cli_framework", "clap", "stated", "inferred")
    block = facts_block(store)
    assert block.index("language") < block.index("cli_framework")


def test_a_value_containing_rich_markup_is_rendered_literally():
    """This block is printed through a Rich console in the live trace.

    A1.48 and A1.67 are both this defect: Rich parses [word] as a style
    tag and prints nothing. A fact value is user text and must survive.
    """
    store = FactStore()
    store.record("style", "[bold]never[/bold]", "the user pasted it", "asked")
    block = facts_block(store)
    assert "[bold]never[/bold]" in block
