"""The summary, where A1.25 dies."""

from __future__ import annotations

import json

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


def test_summarise_puts_the_usage_on_the_result():
    from rich.console import Console

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import summarise
    from rudra.loop.ledger import Ledger

    usage = RunUsage()
    usage.record("coder", input_tokens=100, output_tokens=10)

    result = summarise(Ledger(), Console(quiet=True), usage)
    assert result.usage is usage


def test_summarise_without_usage_still_works():
    """Every caller predating C7.5 passes two arguments."""
    from rich.console import Console

    from rudra.loop.engine import summarise
    from rudra.loop.ledger import Ledger

    assert summarise(Ledger(), Console(quiet=True)).usage is None


def test_work_writes_the_usage_log(tmp_path):
    """usage.json lands in D15's volatile subtree beside the other logs."""
    import json

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import write_usage_log

    usage = RunUsage()
    usage.record("coder", input_tokens=100, output_tokens=10)
    target = tmp_path / "logs" / "usage.json"

    write_usage_log(target, usage)

    assert json.loads(target.read_text(encoding="utf-8"))["roles"]["coder"]["input_tokens"] == 100


def test_writing_the_usage_log_never_ends_a_run(tmp_path):
    """A completed run must not fail on its own bookkeeping.

    The tally must be NON-EMPTY, or write_usage_log returns before it
    attempts anything and the test passes without exercising the guard.
    """
    from rudra.context.usage import RunUsage
    from rudra.loop.engine import write_usage_log

    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)

    # A directory where the file should be: write_text raises
    # IsADirectoryError, an OSError, which the guard must swallow.
    target = tmp_path / "usage.json"
    target.mkdir()

    write_usage_log(target, usage)  # must not raise


def test_an_empty_tally_is_still_written(tmp_path):
    """Reversed by OPEN-79. Skipping an empty tally predates OPEN-53's
    `run` block; since that exists a run where no role made a call still
    has a true wall clock and a `suspended_seconds` -- which is exactly
    the number that answers "why did this take so long" for a run that
    died before it called anything.

    Nothing is written only when there is no tally at all."""
    from rudra.context.usage import RunUsage
    from rudra.loop.engine import write_usage_log

    target = tmp_path / "usage.json"
    write_usage_log(target, RunUsage())
    assert "run" in json.loads(target.read_text(encoding="utf-8"))

    missing = tmp_path / "none.json"
    write_usage_log(missing, None)
    assert not missing.exists()


# --- the two clocks reach the log and the trace (OPEN-53) -------------------


def test_the_usage_log_carries_the_run_block(tmp_path):
    """run10 was 4888s of wall clock over 1243s of counted time and nothing
    on disk said so, which cost three sessions before `pmset -g log` named
    the cause. The file now carries both clocks and their difference."""
    import json

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import write_usage_log

    usage = RunUsage(started_wall=1000.0, started_mono=50.0)
    usage.record("coder", input_tokens=100, output_tokens=10)
    target = tmp_path / "logs" / "usage.json"

    write_usage_log(target, usage)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["roles"]["coder"]["input_tokens"] == 100
    assert written["run"]["suspended_seconds"] >= 0.0
    assert set(written) == {"roles", "run"}


def test_a_suspended_run_says_so_in_the_trace(tmp_path):
    """NOTICE, on the OPEN-44/OPEN-45 precedent: it is a thing RUDRA did
    (or had done to it), not a thing the model did, so it renders at
    VERBOSE and reaches the debug log at every level."""
    import time

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import notice_if_suspended

    emitted = []

    class Sink:
        def notice(self, payload, *, role, name="", **kwargs):
            emitted.append((payload, role, name))

    usage = RunUsage(started_wall=time.time() - 4888.0, started_mono=time.monotonic() - 1243.0)

    notice_if_suspended(Sink(), usage)

    assert len(emitted) == 1
    payload, role, name = emitted[0]
    assert name == "suspended"
    assert role == "rudra"
    assert "3645" in payload


def test_a_run_that_never_slept_says_nothing(tmp_path):
    from rudra.context.usage import RunUsage
    from rudra.loop.engine import notice_if_suspended

    emitted = []

    class Sink:
        def notice(self, payload, *, role, name="", **kwargs):
            emitted.append(payload)

    notice_if_suspended(Sink(), RunUsage())

    assert emitted == []


def test_reporting_suspension_never_ends_a_run(tmp_path):
    """C7.5's rule, which every run-end writer here follows: a finished run
    must not be reported failed because its own bookkeeping raised."""
    import time

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import notice_if_suspended

    class Sink:
        def notice(self, payload, *, role, name="", **kwargs):
            raise RuntimeError("the sink is gone")

    usage = RunUsage(started_wall=time.time() - 4888.0, started_mono=time.monotonic() - 1243.0)

    notice_if_suspended(Sink(), usage)  # must not raise
    notice_if_suspended(None, usage)
    notice_if_suspended(Sink(), None)
