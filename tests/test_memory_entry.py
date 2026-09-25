"""One memory, validated before it can reach a vector store.

Pure: no mempalace, no ChromaDB, no filesystem. Mirrors facts/store.py's
Fact, including that validation covers types and size, never content.
"""

from __future__ import annotations

import pytest

from rudra.memory.entry import MAX_CONTENT, EntryRejected, MemoryEntry


def test_a_valid_entry_round_trips() -> None:
    entry = MemoryEntry(content="chose uv over pip", room="decisions", added_by="rudra")
    assert entry.content == "chose uv over pip"
    assert entry.room == "decisions"
    assert entry.added_by == "rudra"
    assert entry.source == ""


def test_content_is_stripped() -> None:
    assert MemoryEntry(content="  x  ", room="tasks", added_by="rudra").content == "x"


def test_empty_content_is_rejected() -> None:
    with pytest.raises(EntryRejected):
        MemoryEntry(content="   ", room="tasks", added_by="rudra")


def test_oversized_content_is_rejected_with_both_numbers() -> None:
    with pytest.raises(EntryRejected) as exc:
        MemoryEntry(content="x" * (MAX_CONTENT + 1), room="tasks", added_by="rudra")
    assert str(MAX_CONTENT) in str(exc.value)
    assert str(MAX_CONTENT + 1) in str(exc.value)


def test_unknown_room_is_rejected_and_lists_the_real_ones() -> None:
    with pytest.raises(EntryRejected) as exc:
        MemoryEntry(content="x", room="thoughts", added_by="rudra")
    assert "decisions" in str(exc.value)


def test_unknown_added_by_is_rejected() -> None:
    with pytest.raises(EntryRejected):
        MemoryEntry(content="x", room="tasks", added_by="someone")


def test_the_entry_is_frozen() -> None:
    entry = MemoryEntry(content="x", room="tasks", added_by="rudra")
    with pytest.raises(Exception):
        entry.content = "y"  # type: ignore[misc]


# --- OPEN-153: content that must fit, made to fit ----------------------------


def test_fit_content_leaves_short_text_alone() -> None:
    from rudra.memory.entry import fit_content

    assert fit_content("Blocked: t1. Reason: x.") == "Blocked: t1. Reason: x."
    assert fit_content("y" * MAX_CONTENT) == "y" * MAX_CONTENT


def test_fit_content_keeps_both_ends_and_says_what_it_dropped() -> None:
    """The head names the task and the stage; the tail is the runner's own
    summary. The middle is what goes, and the count says how much."""
    from rudra.memory.entry import fit_content

    text = "HEAD-MARK " + "m" * 20_000 + " TAIL-MARK"
    fitted = fit_content(text)
    assert len(fitted) <= MAX_CONTENT
    assert fitted.startswith("HEAD-MARK")
    assert fitted.endswith("TAIL-MARK")
    marker = fitted.split("\n")[1]
    kept = len(fitted) - len(marker) - 2
    assert marker == f"[... {len(text) - kept} characters omitted ...]"
    MemoryEntry(content=fitted, room="blockers", added_by="rudra")


def test_fit_content_honours_a_small_limit() -> None:
    from rudra.memory.entry import fit_content

    fitted = fit_content("a" * 500 + "b" * 500, limit=100)
    assert len(fitted) <= 100
    assert fitted.startswith("a") and fitted.endswith("b")
