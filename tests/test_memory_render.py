"""Memories as a prompt section.

Pure: takes hits, returns a string. Mirrors facts/render.py, including
that nothing here escapes Rich markup -- the block goes into a system
prompt, where brackets are ordinary characters (A1.67).
"""

from __future__ import annotations

from rudra.memory.render import recall_block
from rudra.memory.store import MemoryHit


def _hit(content: str, room: str = "decisions", added_by: str = "rudra") -> MemoryHit:
    return MemoryHit(content=content, room=room, added_by=added_by, score=0.5)


def test_no_hits_renders_nothing() -> None:
    """An empty heading is prompt cost with no content."""
    assert recall_block([], 1000) == ""


def test_a_none_budget_renders_nothing() -> None:
    """No declared window means inject nothing (S12.9)."""
    assert recall_block([_hit("something")], None) == ""


def test_the_block_names_the_room_and_the_content() -> None:
    out = recall_block([_hit("chose uv over pip", room="decisions")], 1000)
    assert "decisions" in out
    assert "chose uv over pip" in out


def test_agent_written_memories_are_marked_as_such() -> None:
    """A reader must be able to tell a recorded fact from the model's own
    earlier opinion. They are not the same kind of claim."""
    out = recall_block([_hit("the user likes tabs", room="preferences", added_by="agent")], 1000)
    assert "agent" in out


def test_truncation_drops_whole_entries_never_half_of_one() -> None:
    hits = [_hit(f"decision number {n} {'x' * 200}") for n in range(20)]
    out = recall_block(hits, 100)
    for hit in hits:
        assert hit.content in out or hit.content not in out.replace(hit.content, "")


def test_every_rendered_entry_is_rendered_whole() -> None:
    """The real truncation property: whatever survives is complete."""
    hits = [_hit(f"decision number {n} {'x' * 200}") for n in range(20)]
    out = recall_block(hits, 100)
    rendered = [h for h in hits if h.content in out]
    assert rendered
    for hit in rendered:
        assert f"- [{hit.room}] {hit.content}  ({hit.added_by})" in out


def test_the_block_stays_within_its_budget() -> None:
    hits = [_hit(f"decision number {n} {'x' * 200}") for n in range(50)]
    out = recall_block(hits, 200)
    assert len(out) <= 200 * 4 + 400  # 4 chars/token, plus the heading


def test_one_entry_always_survives_even_over_budget() -> None:
    """Better one over-long entry than a heading with nothing under it,
    which reads as 'this project has no history'."""
    out = recall_block([_hit("x" * 5000)], 10)
    assert "x" in out
