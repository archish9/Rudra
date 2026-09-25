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
    task.convergence = "converging"

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


def test_concurrent_saves_do_not_race_on_the_temp_file(tmp_path):
    """A1.71, the same shape FactStore.save had.

    Measured on the fact store, whose tool is called once per fact; this
    one survived only because add_tasks takes a list and is usually a
    single call. The bug is identical, so the guard is too.
    """
    import threading

    ledger = Ledger()
    for index in range(20):
        ledger.add(f"task {index}")

    path = tmp_path / "ledger.json"
    errors: list[BaseException] = []

    def _save() -> None:
        try:
            for _ in range(25):
                ledger.save(path)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assert
            errors.append(exc)

    threads = [threading.Thread(target=_save) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent save raised {errors[0]!r}"
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"]
    assert [entry.name for entry in tmp_path.iterdir()] == ["ledger.json"]


def test_a_tasks_seconds_survive_a_save_and_load(tmp_path):
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.seconds = 12.5
    path = tmp_path / "ledger.json"
    ledger.save(path)

    assert Ledger.load(path).tasks[0].seconds == 12.5


def test_a_ledger_written_before_seconds_existed_still_loads(tmp_path):
    """Volatile file, but a run in flight during an upgrade must not crash."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "x", "status": "pending"}]}),
        encoding="utf-8",
    )
    assert Ledger.load(path).tasks[0].seconds == 0.0


def test_a_tasks_halts_survive_a_save_and_load(tmp_path):
    """OPEN-44: a guard halt is the one record that used to be erased.

    It lives beside `note` rather than in it because `note` is read by a
    MODEL -- consult_planner interpolates it verbatim and
    record_block_memory files it in the palace (CR-C4) -- while this is a
    record of what Rudra did.
    """
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.halts = ("'write_file' on '/DONE' repeated 3x -- stopping",)
    path = tmp_path / "ledger.json"
    ledger.save(path)

    assert Ledger.load(path).tasks[0].halts == ("'write_file' on '/DONE' repeated 3x -- stopping",)


def test_a_ledger_written_before_halts_existed_still_loads(tmp_path):
    """Volatile file, but a run in flight during an upgrade must not crash."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "x", "status": "pending"}]}),
        encoding="utf-8",
    )
    assert Ledger.load(path).tasks[0].halts == ()


def test_a_tasks_run_errors_survive_a_save_and_load(tmp_path):
    """OPEN-46 §5.3, and it is OPEN-44's failure re-run one field over.

    `task.note` is where a subagent that never ran was recorded, and every
    branch that finishes a task rewrites it -- so run14's exhaustion of the
    retry budget, which cost t8 outright, left nothing on disk that a later
    reader could count. A list, not a field that gets overwritten.
    """
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.run_errors = ("the coder could not run: Provider error ... after 4 attempt(s)",)
    path = tmp_path / "ledger.json"
    ledger.save(path)

    assert Ledger.load(path).tasks[0].run_errors == (
        "the coder could not run: Provider error ... after 4 attempt(s)",
    )


def test_a_ledger_written_before_run_errors_existed_still_loads(tmp_path):
    """Volatile file, but a run in flight during an upgrade must not crash."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "x", "status": "pending"}]}),
        encoding="utf-8",
    )
    assert Ledger.load(path).tasks[0].run_errors == ()


# --------------------------------------------------------------------------
# OPEN-158: a task added to unblock the run is worked NEXT
# --------------------------------------------------------------------------


def _blocked_at_t1_with_pending(count: int) -> Ledger:
    """Run 4989aefefacb's shape: t1 blocked, everything after it still pending."""
    ledger = Ledger()
    for index in range(count + 1):
        ledger.add(f"task {index + 1}")
    ledger.get("t1").status = TaskStatus.BLOCKED
    return ledger


def test_a_task_added_while_unblocking_is_the_next_pending():
    # t18's shape: 16 pending when the blocked consult added it, 17th in line.
    ledger = _blocked_at_t1_with_pending(16)
    with ledger.unblocking("t1"):
        fix = ledger.add("declare greenlet in requirements.txt")
    assert fix.id == "t18"
    assert ledger.next_pending() is fix
    assert ledger.resumable()[0] is fix


def test_tasks_added_while_unblocking_keep_their_own_order():
    # Across separate add calls in ONE consult too: the planner often adds,
    # reads, and adds again (run 4989aefefacb added t18, then t19).
    ledger = _blocked_at_t1_with_pending(3)
    with ledger.unblocking("t1"):
        first = ledger.add("fix the cause")
        second = ledger.add("then this")
    assert [task.id for task in ledger.resumable()] == [first.id, second.id, "t2", "t3", "t4"]


def test_a_task_added_while_unblocking_records_which_block_it_answers():
    # CLAUDE.md 8a: the order a run worked in must be readable from ledger.json.
    ledger = _blocked_at_t1_with_pending(1)
    with ledger.unblocking("t1"):
        fix = ledger.add("fix it")
    plain = ledger.add("later work")
    assert fix.unblocks == "t1"
    assert plain.unblocks == ""


def test_outside_a_blocked_consult_add_still_appends():
    ledger = _blocked_at_t1_with_pending(2)
    with ledger.unblocking("t1"):
        ledger.add("fix it")
    appended = ledger.add("an ordinary addition")
    assert ledger.tasks[-1] is appended
    assert ledger.resumable()[-1] is appended


def test_unblocking_is_cleared_even_when_the_consult_raises():
    ledger = _blocked_at_t1_with_pending(1)
    try:
        with ledger.unblocking("t1"):
            raise RuntimeError("provider down")
    except RuntimeError:
        pass
    appended = ledger.add("after the failed consult")
    assert ledger.tasks[-1] is appended
    assert appended.unblocks == ""


def test_ids_stay_unique_when_a_task_is_inserted_mid_list():
    ledger = _blocked_at_t1_with_pending(2)
    with ledger.unblocking("t1"):
        ledger.add("fix it")
    ledger.add("more")
    ids = [task.id for task in ledger.tasks]
    assert len(ids) == len(set(ids))
    assert ledger.get("t4").description == "fix it"


def test_with_nothing_pending_an_unblocking_task_is_appended():
    ledger = Ledger()
    ledger.add("only")
    ledger.get("t1").status = TaskStatus.BLOCKED
    with ledger.unblocking("t1"):
        fix = ledger.add("fix it")
    assert ledger.tasks[-1] is fix
    assert ledger.next_pending() is fix


def test_unblocks_round_trips_and_is_optional(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = _blocked_at_t1_with_pending(1)
    with ledger.unblocking("t1"):
        ledger.add("fix it")
    ledger.save(path)
    reloaded = Ledger.load(path)
    assert [task.id for task in reloaded.tasks] == ["t1", "t3", "t2"]
    assert reloaded.get("t3").unblocks == "t1"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "x", "status": "pending"}]}),
        encoding="utf-8",
    )
    assert Ledger.load(path).tasks[0].unblocks == ""
