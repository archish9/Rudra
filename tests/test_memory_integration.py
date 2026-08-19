"""C8.8 — the whole subsystem against a real palace, never a mock.

Each test here crosses at least two module boundaries. The unit suites
already prove each piece; this proves they compose, which is the failure
mode a suite of green units is least able to see.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rudra.memory.entry import MemoryEntry
from rudra.memory.export import export_memory, import_memory
from rudra.memory.store import MemoryStore
from rudra.tools.memory_tools import create_memory_tools


def test_a_tool_write_is_visible_to_the_cli_read_path(tmp_path: Path) -> None:
    """The two halves users touch: a model records, a human lists."""
    project = tmp_path / "demo"
    project.mkdir()
    tools = {t.name: t for t in create_memory_tools(project)}
    tools["remember"].invoke({"content": "the user prefers uv", "room": "preferences"})

    rows = MemoryStore(project).list_entries()
    assert [r.added_by for r in rows] == ["agent"]
    assert "uv" in rows[0].content


def test_a_full_lifecycle_survives_export_wipe_and_restore(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    for n in range(5):
        store.write(MemoryEntry(content=f"decision {n}", room="decisions", added_by="rudra"))

    out = tmp_path / "export"
    export_memory(store, out)

    store.delete([r.id for r in store.list_entries()])
    assert store.count() == 0

    import_memory(store, out)
    assert store.count() == 5
    assert {r.content for r in store.list_entries()} == {f"decision {n}" for n in range(5)}


def test_search_still_works_after_a_restore(tmp_path: Path) -> None:
    """A restore that loses the vectors would list correctly and search
    like an empty store -- the failure a count-only assertion misses."""
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(
        MemoryEntry(content="we picked uv over pip for speed", room="decisions", added_by="rudra")
    )

    out = tmp_path / "export"
    export_memory(store, out)
    store.delete([r.id for r in store.list_entries()])
    import_memory(store, out)

    hits = store.search("which package manager")
    assert hits
    assert "uv" in hits[0].content


def test_a_hostile_global_config_changes_nothing_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8.1b across write, search, export and import rather than one call."""
    home = tmp_path / "home"
    (home / ".mempalace").mkdir(parents=True)
    (home / ".mempalace" / "config.json").write_text(
        json.dumps({"collection_name": "someone_else", "backend": "qdrant"})
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)

    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="isolated", room="tasks", added_by="rudra"))

    out = tmp_path / "export"
    export_memory(store, out)
    store.delete([r.id for r in store.list_entries()])
    import_memory(store, out)

    assert store.count() == 1
    assert store.search("isolated")


def test_two_projects_keep_separate_memories(tmp_path: Path) -> None:
    """D14/S14.5: project-scoped means project-scoped."""
    for name, content in (("alpha", "alpha decision"), ("beta", "beta decision")):
        project = tmp_path / name
        project.mkdir()
        MemoryStore(project).write(MemoryEntry(content=content, room="decisions", added_by="rudra"))

    assert [r.content for r in MemoryStore(tmp_path / "alpha").list_entries()] == ["alpha decision"]
