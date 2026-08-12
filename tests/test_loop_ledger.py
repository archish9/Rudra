"""The task ledger: what it records and how it survives a crash."""

from __future__ import annotations

import json

from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths


def test_the_paths_module_owns_the_ledger_location(tmp_path):
    # Never build a .rudra/... path by hand (CLAUDE.md §3).
    assert rudra_paths(tmp_path).ledger_json == tmp_path / ".rudra" / "run" / "ledger.json"


def test_add_assigns_stable_sequential_ids():
    ledger = Ledger()
    first = ledger.add("write the parser")
    second = ledger.add("write its tests")
    assert (first.id, second.id) == ("t1", "t2")
    assert first.status is TaskStatus.PENDING


def test_get_finds_by_id_and_returns_none_otherwise():
    ledger = Ledger()
    ledger.add("a")
    assert ledger.get("t1").description == "a"
    assert ledger.get("t99") is None


def test_next_pending_returns_them_in_declaration_order():
    ledger = Ledger()
    ledger.add("a")
    ledger.add("b")
    assert ledger.next_pending().id == "t1"
    ledger.get("t1").status = TaskStatus.DONE
    assert ledger.next_pending().id == "t2"
    ledger.get("t2").status = TaskStatus.BLOCKED
    assert ledger.next_pending() is None


def test_a_dropped_task_is_not_pending():
    ledger = Ledger()
    ledger.add("a")
    ledger.get("t1").status = TaskStatus.DROPPED
    assert ledger.next_pending() is None


def test_counts_always_account_for_every_task():
    # The A1.25 invariant in its smallest form.
    ledger = Ledger()
    for description in ("a", "b", "c", "d"):
        ledger.add(description)
    ledger.get("t1").status = TaskStatus.DONE
    ledger.get("t2").status = TaskStatus.BLOCKED
    ledger.get("t3").status = TaskStatus.DROPPED
    counts = ledger.counts()
    assert counts["requested"] == 4
    assert counts["done"] + counts["blocked"] + counts["dropped"] + counts["pending"] == 4


def test_round_trip_preserves_every_field(tmp_path):
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.BLOCKED
    task.attempts = 3
    task.files_touched = ("a.py", "b.py")
    task.last_signature = "deadbeef"
    task.note = "no progress: the same failure twice"

    path = tmp_path / "ledger.json"
    ledger.save(path)
    loaded = Ledger.load(path)

    assert loaded.tasks == ledger.tasks
    assert loaded.get("t1").files_touched == ("a.py", "b.py")
    assert loaded.get("t1").status is TaskStatus.BLOCKED


def test_the_saved_file_is_readable_json(tmp_path):
    ledger = Ledger()
    ledger.add("a")
    path = tmp_path / "ledger.json"
    ledger.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tasks"][0]["id"] == "t1"
    assert payload["tasks"][0]["status"] == "pending"


def test_save_is_atomic(tmp_path, monkeypatch):
    # A crash mid-write must leave the previous ledger intact, not a
    # truncated one -- this file is the only record of how far a run got.
    path = tmp_path / "ledger.json"
    first = Ledger()
    first.add("survivor")
    first.save(path)

    import os

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    second = Ledger()
    second.add("never lands")
    try:
        second.save(path)
    except OSError:
        pass

    assert Ledger.load(path).get("t1").description == "survivor"


def test_loading_a_missing_file_gives_an_empty_ledger(tmp_path):
    assert Ledger.load(tmp_path / "absent.json").tasks == []


def test_add_after_load_keeps_ids_unique(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("a")
    ledger.add("b")
    ledger.save(path)

    reloaded = Ledger.load(path)
    third = reloaded.add("c")
    assert third.id == "t3"
