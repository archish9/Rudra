"""Unfinished work is copied aside before a new run replaces it (OPEN-74).

`plan()` saves an empty ledger before its first model call, so the next
request typed into the REPL destroys the previous run's tasks before the
new plan exists to replace them. In the run this was filed on, fifteen
pending tasks became `{"request": "yes", "tasks": []}` five minutes later,
and the cancel path had already told the user `rudra --continue` would
pick them up.

The rule is NOT "stop overwriting the ledger" -- one run, one ledger is
right, and `run/` is volatile by D15. It is that work is not deleted
silently, and not before anything has replaced it.
"""

from __future__ import annotations

import json

from rudra.loop.engine import PREVIOUS_LEDGER, preserve_previous_ledger
from rudra.loop.ledger import Ledger, TaskStatus


def _ledger_with(tmp_path, *statuses):
    ledger = Ledger()
    for index, status in enumerate(statuses):
        task = ledger.add(f"task {index}")
        task.status = status
    path = tmp_path / "ledger.json"
    ledger.save(path, request="write a todo api")
    return path


def test_pending_work_is_copied_aside(tmp_path):
    path = _ledger_with(tmp_path, TaskStatus.PENDING, TaskStatus.PENDING, TaskStatus.DONE)

    assert preserve_previous_ledger(path) == 2

    saved = json.loads((tmp_path / PREVIOUS_LEDGER).read_text(encoding="utf-8"))
    assert saved["request"] == "write a todo api"
    assert len(saved["tasks"]) == 3


def test_a_finished_ledger_is_not_preserved(tmp_path):
    """`resumable()` is PENDING-only (A1.93). A ledger of DONE and BLOCKED
    tasks resumes to nothing, so copying it aside would be noise."""
    path = _ledger_with(tmp_path, TaskStatus.DONE, TaskStatus.BLOCKED)

    assert preserve_previous_ledger(path) == 0
    assert not (tmp_path / PREVIOUS_LEDGER).exists()


def test_no_ledger_yet_is_not_an_event(tmp_path):
    assert preserve_previous_ledger(tmp_path / "ledger.json") == 0


def test_unreadable_ledger_never_raises(tmp_path):
    """Bookkeeping must not end a run (write_usage_log's rule). A ledger
    this cannot parse is one the new run is about to replace anyway."""
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")

    assert preserve_previous_ledger(path) == 0


def test_the_copy_is_replaced_not_accumulated(tmp_path):
    """One previous ledger, not a directory of them. The recovery this
    supports is "the run I just lost", never an archive."""
    path = _ledger_with(tmp_path, TaskStatus.PENDING)
    preserve_previous_ledger(path)

    path.unlink()
    second = _ledger_with(tmp_path, TaskStatus.PENDING, TaskStatus.PENDING)
    assert preserve_previous_ledger(second) == 2

    saved = json.loads((tmp_path / PREVIOUS_LEDGER).read_text(encoding="utf-8"))
    assert len(saved["tasks"]) == 2
