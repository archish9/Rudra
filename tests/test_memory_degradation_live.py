"""C8.6 against real breakage, not a raised exception in a fake.

The guard is only worth what it survives. Each case here is a failure a
user can actually reach: a corrupt store, an unwritable directory, a
palace whose path is occupied by something that is not a directory.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from rudra.memory.degrade import last_failure, reset_failures
from rudra.memory.entry import MemoryEntry
from rudra.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_failures()
    yield
    reset_failures()


def test_a_corrupt_palace_degrades_instead_of_raising(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="first", room="tasks", added_by="rudra"))

    sqlite = project / ".rudra" / "memory" / "palace" / "chroma.sqlite3"
    sqlite.write_bytes(b"not a database")

    broken = MemoryStore(project)
    assert broken.write(MemoryEntry(content="second", room="tasks", added_by="rudra")) is False
    assert broken.count() == 0
    assert broken.search("first") == []


def test_the_failure_is_reported_not_swallowed(tmp_path: Path) -> None:
    """S14.2: a mandatory subsystem that quietly does nothing is worse
    than one that fails."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("i am a file, not a directory")

    store = MemoryStore(project)
    store.write(MemoryEntry(content="x", room="tasks", added_by="rudra"))
    assert last_failure() is not None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_memory_dir_degrades(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    (project / ".rudra").mkdir(parents=True)
    (project / ".rudra" / "memory").mkdir()
    (project / ".rudra" / "memory").chmod(0o500)
    try:
        store = MemoryStore(project)
        assert store.write(MemoryEntry(content="x", room="tasks", added_by="rudra")) is False
        assert last_failure() is not None
    finally:
        (project / ".rudra" / "memory").chmod(0o700)


def test_a_degraded_store_never_raises_into_its_caller(tmp_path: Path) -> None:
    """The rule that matters most: a task that finished must not be undone
    by a bookkeeping write (loop/engine.py:352-366)."""
    project = tmp_path / "demo"
    project.mkdir()
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("not a directory")

    store = MemoryStore(project)
    store.write(MemoryEntry(content="x", room="tasks", added_by="rudra"))
    store.search("x")
    store.count()
    store.raw_metadatas()
