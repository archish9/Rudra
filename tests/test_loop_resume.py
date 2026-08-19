"""A killed run picks up where it stopped (Step 12c, C7.2).

S12.2: `--continue` resumes the LEDGER, not the LangGraph thread. What a
user loses to a provider failure is completed work, and the ledger is the
record of that.
"""

from __future__ import annotations

from rudra.loop.ledger import Ledger, TaskStatus


def test_a_fresh_ledger_has_no_request():
    assert Ledger().request == ""


def test_save_records_the_request(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    assert Ledger.load(path).request == "build a JSON parser"


def test_save_without_a_request_keeps_the_one_already_there(tmp_path):
    """Every mid-run save passes no request and must not erase it."""
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    reloaded = Ledger.load(path)
    reloaded.tasks[0].status = TaskStatus.DONE
    reloaded.save(path)

    assert Ledger.load(path).request == "build a JSON parser"


def test_saving_stamps_the_time(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("t")
    ledger.save(path, request="r")

    assert Ledger.load(path).saved_at


def test_a_ledger_written_before_this_change_still_loads(tmp_path):
    """No migration: an old file loads with an empty request (S10a.8)."""
    import json

    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "old", "status": "pending"}]}),
        encoding="utf-8",
    )

    ledger = Ledger.load(path)
    assert ledger.request == ""
    assert ledger.saved_at == ""
    assert len(ledger.tasks) == 1


def test_only_pending_tasks_are_resumable():
    """BLOCKED is not retried: it hit two identical failure signatures.

    Retrying it identically burns a run to reach the same place (C6.5a).
    """
    ledger = Ledger()
    done = ledger.add("already finished")
    pending = ledger.add("still to do")
    blocked = ledger.add("gave up")
    dropped = ledger.add("removed")

    done.status = TaskStatus.DONE
    blocked.status = TaskStatus.BLOCKED
    dropped.status = TaskStatus.DROPPED

    assert ledger.resumable() == (pending,)


def test_resumable_keeps_declaration_order():
    ledger = Ledger()
    first = ledger.add("one")
    second = ledger.add("two")
    assert ledger.resumable() == (first, second)


def test_attempts_survive_the_seam(tmp_path):
    """max_fix_attempts must still mean what it says across a resume."""
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    task = ledger.add("half-tried")
    task.attempts = 2
    ledger.save(path, request="r")

    assert Ledger.load(path).tasks[0].attempts == 2
