"""The durable copy, and the way back.

Rudra owns both halves. `exporter.export_palace` resolves collection and
backend from ~/.mempalace/config.json (exporter.py:83), so against a user
with a custom collection_name it would export a collection Rudra never
wrote to and report success -- A1.87. Owning the writer means owning the
reader, and the round trip is then exact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.memory.entry import MemoryEntry
from rudra.memory.export import export_memory, import_memory
from rudra.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="we chose uv over pip", room="decisions", added_by="rudra"))
    store.write(MemoryEntry(content="the user prefers tabs", room="preferences", added_by="agent"))
    store.write(MemoryEntry(content="finished the parser", room="tasks", added_by="rudra"))
    return store


def test_export_writes_one_markdown_file_per_room(store: MemoryStore, tmp_path: Path) -> None:
    out = tmp_path / "export"
    stats = export_memory(store, out)
    assert stats["drawers"] == 3
    assert {p.name for p in out.glob("*.md")} == {"decisions.md", "preferences.md", "tasks.md"}


def test_an_exported_file_is_readable_by_a_human(store: MemoryStore, tmp_path: Path) -> None:
    out = tmp_path / "export"
    export_memory(store, out)
    text = (out / "decisions.md").read_text(encoding="utf-8")
    assert "we chose uv over pip" in text
    assert "rudra" in text


def test_an_empty_palace_exports_nothing_and_does_not_raise(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    assert export_memory(MemoryStore(project), tmp_path / "export")["drawers"] == 0


def test_the_round_trip_restores_every_drawer(store: MemoryStore, tmp_path: Path) -> None:
    out = tmp_path / "export"
    export_memory(store, out)

    fresh_project = tmp_path / "restored"
    fresh_project.mkdir()
    fresh = MemoryStore(fresh_project)
    stats = import_memory(fresh, out)

    assert stats["drawers"] == 3
    assert {r.content for r in fresh.list_entries()} == {
        "we chose uv over pip",
        "the user prefers tabs",
        "finished the parser",
    }


def test_the_round_trip_preserves_room_and_author(store: MemoryStore, tmp_path: Path) -> None:
    """A restore that forgets who wrote what cannot be purged selectively
    afterwards, which is the whole point of tagging added_by."""
    out = tmp_path / "export"
    export_memory(store, out)

    fresh_project = tmp_path / "restored"
    fresh_project.mkdir()
    fresh = MemoryStore(fresh_project)
    import_memory(fresh, out)

    by_content = {r.content: r for r in fresh.list_entries()}
    assert by_content["the user prefers tabs"].added_by == "agent"
    assert by_content["the user prefers tabs"].room == "preferences"
    assert by_content["we chose uv over pip"].room == "decisions"


def test_importing_twice_does_not_duplicate(store: MemoryStore, tmp_path: Path) -> None:
    """Drawer ids are content-addressed, so a re-import is an upsert."""
    out = tmp_path / "export"
    export_memory(store, out)
    import_memory(store, out)
    import_memory(store, out)
    assert store.count() == 3


def test_importing_from_a_missing_directory_is_zero_not_a_crash(
    store: MemoryStore, tmp_path: Path
) -> None:
    assert import_memory(store, tmp_path / "nope")["drawers"] == 0


def test_a_hand_edited_export_skips_the_bad_entry_and_keeps_the_rest(
    store: MemoryStore, tmp_path: Path
) -> None:
    """A durable text file is a thing people edit. One broken entry must
    not cost the whole restore."""
    out = tmp_path / "export"
    export_memory(store, out)
    path = out / "decisions.md"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n<!-- rudra-memory added_by=nobody filed_at= -->\nbad author\n",
        encoding="utf-8",
    )

    fresh_project = tmp_path / "restored"
    fresh_project.mkdir()
    fresh = MemoryStore(fresh_project)
    import_memory(fresh, out)
    contents = {r.content for r in fresh.list_entries()}
    assert "we chose uv over pip" in contents
    assert "bad author" not in contents
