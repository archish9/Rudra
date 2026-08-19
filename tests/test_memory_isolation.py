"""C8.1b, executable.

A user with ~/.mempalace/config.json must not be able to redirect Rudra's
reads or writes. Two of the three settings that could do it cannot be
overridden by environment variables at all:

  collection_name  config.json only, no env var exists (config.py:413-415)
  backend          config.json BEFORE env (config.py:425-432)

So the defence is explicit arguments, and this is the test that proves
they are actually passed. It is also the test that would have caught
exporter.export_palace calling get_collection(palace_path) with neither
(exporter.py:83) -- see spec 5.2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rudra.memory.entry import MemoryEntry
from rudra.memory.store import RUDRA_COLLECTION, MemoryStore


@pytest.fixture
def hostile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user-global MemPalace config that would break everything if read."""
    home = tmp_path / "home"
    (home / ".mempalace").mkdir(parents=True)
    (home / ".mempalace" / "config.json").write_text(
        json.dumps(
            {
                "collection_name": "someone_elses_collection",
                "backend": "qdrant",
                "palace_path": str(tmp_path / "someone-elses-palace"),
                "embedding_model": "embeddinggemma",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_writes_go_to_rudras_collection_not_the_users(hostile_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="isolated", room="tasks", added_by="rudra"))
    assert store.collection_name == RUDRA_COLLECTION
    assert store.count() == 1


def test_the_palace_is_the_projects_not_the_users(hostile_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="isolated", room="tasks", added_by="rudra"))
    assert (project / ".rudra" / "memory" / "palace").is_dir()
    assert not (tmp_path / "someone-elses-palace").exists()


def test_search_reads_the_same_collection_it_wrote(hostile_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(
        MemoryEntry(content="isolated memory about widgets", room="tasks", added_by="rudra")
    )
    assert store.search("widgets")


def test_the_users_global_config_is_left_untouched(hostile_home: Path, tmp_path: Path) -> None:
    """C8.1c: Rudra reads nothing there and writes nothing there."""
    before = (hostile_home / ".mempalace" / "config.json").read_text(encoding="utf-8")
    project = tmp_path / "demo"
    project.mkdir()
    MemoryStore(project).write(MemoryEntry(content="x", room="tasks", added_by="rudra"))
    after = (hostile_home / ".mempalace" / "config.json").read_text(encoding="utf-8")
    assert before == after


# The one user-global path Rudra cannot scope away, measured in the C8.1c
# audit. mine_palace_lock hardcodes os.path.expanduser("~")/.mempalace/locks
# (palace.py:1313) -- no config key, no env var -- and ChromaCollection's
# write methods acquire it themselves, so an ordinary upsert creates it.
#
# Accepted rather than fought: the file is keyed by a sha256 of the resolved
# palace path, so it is already per-palace and cannot collide across
# projects; it holds a lock holder's pid, not memory content; and a
# cross-process write lock has to live somewhere stable by definition.
ALLOWED_GLOBAL_PATHS = {"locks"}


def test_only_the_lock_dir_appears_in_the_users_mempalace_dir(
    hostile_home: Path, tmp_path: Path
) -> None:
    """C8.1c: entity_registry.json, hook_state/ and diary state/ stay dormant.

    Asserting an exact set rather than "nothing new" on purpose. If a future
    mempalace starts writing an entity registry or a diary state file on the
    write path, this fails and names it, which is the whole point of the
    audit -- a laxer assertion would let that land silently.
    """
    before = {p.name for p in (hostile_home / ".mempalace").iterdir()}
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="x", room="decisions", added_by="rudra"))
    store.search("x")
    after = {p.name for p in (hostile_home / ".mempalace").iterdir()}
    assert after - before <= ALLOWED_GLOBAL_PATHS, (
        f"mempalace wrote something new to the user's home: {after - before}"
    )


def test_the_lock_file_is_keyed_per_palace_not_shared(hostile_home: Path, tmp_path: Path) -> None:
    """Two projects must not contend on one lock.

    This is what makes ALLOWED_GLOBAL_PATHS acceptable rather than merely
    tolerated: the shared directory holds a distinct file per palace.
    """
    for name in ("alpha", "beta"):
        project = tmp_path / name
        project.mkdir()
        MemoryStore(project).write(MemoryEntry(content="x", room="tasks", added_by="rudra"))

    locks = {p.name for p in (hostile_home / ".mempalace" / "locks").iterdir()}
    assert len(locks) >= 2, f"expected one lock per palace, got {locks}"
