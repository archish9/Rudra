"""The summary, where A1.25 dies."""

from __future__ import annotations

from rich.console import Console

from rudra.loop.engine import summarise
from rudra.loop.ledger import Ledger, TaskStatus


def ledger_with(*statuses):
    ledger = Ledger()
    for index, status in enumerate(statuses, start=1):
        task = ledger.add(f"task {index}")
        task.status = status
        if status is TaskStatus.BLOCKED:
            task.note = "no progress: the same failure twice"
    return ledger


def render(ledger):
    console = Console(record=True, width=100)
    result = summarise(ledger, console)
    return result, console.export_text()


def test_every_task_appears_in_the_output():
    # A1.25: a requested item must never be invisible. There is no filter
    # between what was declared and what is reported.
    ledger = ledger_with(
        TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED, TaskStatus.PENDING
    )
    _, text = render(ledger)
    for task in ledger.tasks:
        assert task.id in text


def test_the_counts_always_add_up():
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED)
    counts = ledger.counts()
    assert (
        counts["done"] + counts["blocked"] + counts["dropped"] + counts["pending"]
        == counts["requested"]
    )


def test_unattempted_tasks_are_named_when_the_run_stopped_early():
    # The case the user most needs: they would otherwise assume the rest
    # simply passed.
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.PENDING)
    _, text = render(ledger)
    assert "never attempted" in text


def test_a_blocked_task_makes_the_run_unsuccessful():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DONE, TaskStatus.BLOCKED))
    assert result.success is False


def test_all_done_is_a_success():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DONE))
    assert result.success is True


def test_dropping_everything_is_not_a_success():
    result, _ = render(ledger_with(TaskStatus.DROPPED, TaskStatus.DROPPED))
    assert result.success is False


def test_done_alongside_dropped_is_a_success():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DROPPED))
    assert result.success is True


def test_an_empty_ledger_is_not_a_success():
    result, _ = render(Ledger())
    assert result.success is False


def test_the_reason_is_shown_for_anything_not_done():
    _, text = render(ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED))
    assert "no progress" in text


def test_iterations_counts_attempted_tasks():
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.PENDING)
    for task in ledger.tasks:
        task.attempts = 1 if task.status is not TaskStatus.PENDING else 0
    result, _ = render(ledger)
    assert result.iterations == 2


def test_files_touched_are_reported():
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.DONE)
    ledger.tasks[0].files_touched = ("a.py", "b.py")
    ledger.tasks[1].files_touched = ("b.py", "c.py")
    result, _ = render(ledger)
    assert result.files_created == ["a.py", "b.py", "c.py"], "deduplicated and sorted"
