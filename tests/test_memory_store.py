"""The adapter, against a real ChromaDB palace in tmp_path.

Never a mock. A mocked vector store proves nothing about embedder
identity, upsert semantics, or whether an explicit collection_name is
honoured -- which are the three things this module exists to get right.

These tests download an ~80 MB ONNX MiniLM model on first run and cache
it in ~/.cache/chroma. That is the cost of testing the real thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.memory.entry import MemoryEntry
from rudra.memory.store import CHUNK_CHARS, RUDRA_COLLECTION, MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    project = tmp_path / "demo-project"
    project.mkdir()
    return MemoryStore(project)


def test_the_palace_lands_under_rudra_memory_palace(store: MemoryStore, tmp_path: Path) -> None:
    """D14 / S14.5: project-scoped, and via rudra_paths, not a hand-built path."""
    store.write(MemoryEntry(content="hello", room="tasks", added_by="rudra"))
    assert (tmp_path / "demo-project" / ".rudra" / "memory" / "palace").is_dir()


def test_a_write_is_readable_back(store: MemoryStore) -> None:
    assert store.write(MemoryEntry(content="chose uv over pip", room="decisions", added_by="rudra"))
    assert store.count() == 1


def test_the_same_content_written_twice_is_one_drawer(store: MemoryStore) -> None:
    """Content-addressed ids (spec 4.3). miner.add_drawer keys on
    (source_file, chunk_index) instead, which would make this two."""
    entry = MemoryEntry(content="chose uv over pip", room="decisions", added_by="rudra")
    store.write(entry)
    store.write(entry)
    assert store.count() == 1


def test_different_content_in_the_same_room_is_two_drawers(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="first", room="tasks", added_by="rudra"))
    store.write(MemoryEntry(content="second", room="tasks", added_by="rudra"))
    assert store.count() == 2


def test_oversized_content_is_chunked_not_stored_whole(store: MemoryStore) -> None:
    """The library add_drawer will not split; mcp_server's version does.
    An unchunked 3 KB drawer embeds as one vector that matches nothing well."""
    store.write(MemoryEntry(content="x " * (CHUNK_CHARS * 2), room="decisions", added_by="rudra"))
    assert store.count() > 1


def test_the_collection_name_is_rudras_own(store: MemoryStore) -> None:
    assert RUDRA_COLLECTION == "rudra_memory"
    store.write(MemoryEntry(content="hello", room="tasks", added_by="rudra"))
    assert store.collection_name == RUDRA_COLLECTION


def test_added_by_is_stored_on_every_drawer(store: MemoryStore) -> None:
    """14c's `forget --added-by agent` depends on this being per-drawer."""
    store.write(MemoryEntry(content="from the model", room="preferences", added_by="agent"))
    metas = store.raw_metadatas()
    assert metas and all(m["added_by"] == "agent" for m in metas)


def test_the_wing_is_the_project_directory_name(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="hello", room="tasks", added_by="rudra"))
    metas = store.raw_metadatas()
    assert metas[0]["wing"] == "demo-project"


def test_a_project_name_with_no_usable_characters_fails_at_construction(tmp_path: Path) -> None:
    """Loud, and at construction -- not three tasks into a run."""
    from rudra.memory.taxonomy import TaxonomyError

    bad = tmp_path / "___"
    bad.mkdir()
    with pytest.raises(TaxonomyError):
        MemoryStore(bad)


def test_search_finds_a_written_memory(store: MemoryStore) -> None:
    store.write(
        MemoryEntry(content="we chose uv over pip for speed", room="decisions", added_by="rudra")
    )
    hits = store.search("which package manager did we pick")
    assert hits
    assert "uv" in hits[0].content


def test_search_can_filter_by_room(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="uv over pip", room="decisions", added_by="rudra"))
    store.write(MemoryEntry(content="uv over pip", room="preferences", added_by="agent"))
    hits = store.search("uv", room="preferences")
    assert hits
    assert all(hit.room == "preferences" for hit in hits)


def test_search_carries_added_by_through(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="the model's opinion", room="preferences", added_by="agent"))
    hits = store.search("opinion")
    assert hits[0].added_by == "agent"


def test_search_on_an_empty_palace_returns_no_hits_and_does_not_raise(store: MemoryStore) -> None:
    assert store.search("anything") == []


def test_search_respects_the_limit(store: MemoryStore) -> None:
    for n in range(6):
        store.write(MemoryEntry(content=f"decision number {n}", room="decisions", added_by="rudra"))
    assert len(store.search("decision", limit=3)) <= 3


def test_list_entries_returns_every_drawer(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="first", room="tasks", added_by="rudra"))
    store.write(MemoryEntry(content="second", room="decisions", added_by="agent"))
    assert len(store.list_entries()) == 2


def test_list_entries_filters_by_room(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="first", room="tasks", added_by="rudra"))
    store.write(MemoryEntry(content="second", room="decisions", added_by="rudra"))
    assert [r.content for r in store.list_entries(room="tasks")] == ["first"]


def test_list_entries_filters_by_author(store: MemoryStore) -> None:
    """The filter 14c's `forget --added-by agent` is built on."""
    store.write(MemoryEntry(content="from rudra", room="tasks", added_by="rudra"))
    store.write(MemoryEntry(content="from the model", room="tasks", added_by="agent"))
    assert [r.content for r in store.list_entries(added_by="agent")] == ["from the model"]


def test_list_entries_carries_the_id_delete_needs(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="first", room="tasks", added_by="rudra"))
    assert store.list_entries()[0].id


def test_delete_removes_exactly_the_named_drawers(store: MemoryStore) -> None:
    store.write(MemoryEntry(content="keep me", room="tasks", added_by="rudra"))
    store.write(MemoryEntry(content="delete me", room="tasks", added_by="agent"))
    doomed = [r.id for r in store.list_entries(added_by="agent")]
    assert store.delete(doomed) == 1
    assert [r.content for r in store.list_entries()] == ["keep me"]


def test_delete_of_nothing_is_zero_not_an_error(store: MemoryStore) -> None:
    assert store.delete([]) == 0


def test_list_entries_on_a_broken_palace_degrades_to_empty(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("not a directory")
    assert MemoryStore(project).list_entries() == []
