"""A1.89: the loud half of C8.6, wired to a surface.

`degrade.last_failure()` recorded the first failure from the day it was
written and nothing ever read it, so a palace that could not be opened
reached the user as `0 memories` and a silent run -- which is the exact
outcome C8.6 exists to prevent.

Every case here breaks a real palace (a path occupied by a file) rather
than raising from a mock, because a mock proves the reporting path and
not the degradation that feeds it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from rudra.cli import app
from rudra.memory.degrade import reset_failures

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_failures()
    yield
    reset_failures()


def broken_project(tmp_path: Path) -> Path:
    """A project whose palace path is occupied by a regular file."""
    project = tmp_path / "demo"
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("i am a file, not a directory")
    return project


# --- doctor ---------------------------------------------------------------


def test_doctor_reports_a_broken_palace_instead_of_zero_memories(tmp_path: Path) -> None:
    """The defect verbatim: `count()` degrades to 0, so the row read
    `memory  ok  0 memories in …` for a store that cannot be opened."""
    project = broken_project(tmp_path)
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])

    assert "unreadable" in result.stdout
    assert "0 memories" not in result.stdout


def test_doctor_still_calls_an_empty_palace_ok(tmp_path: Path) -> None:
    """Empty is not broken, and conflating them would make the new row
    fire on every fresh project."""
    project = tmp_path / "empty"
    project.mkdir()
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])

    assert result.exit_code == 0
    assert "unreadable" not in result.stdout


# --- the run summary ------------------------------------------------------


def test_the_run_summary_names_a_memory_failure(tmp_path: Path) -> None:
    from rudra.loop.engine import summarise
    from rudra.loop.ledger import Ledger, TaskStatus
    from rudra.memory.entry import MemoryEntry
    from rudra.memory.store import MemoryStore

    store = MemoryStore(broken_project(tmp_path))
    store.write(MemoryEntry(content="x", room="tasks", added_by="rudra"))

    ledger = Ledger()
    ledger.add(["do the thing"])
    ledger.tasks[0].status = TaskStatus.DONE

    console = Console(record=True, width=200)
    summarise(ledger, console)

    printed = console.export_text()
    assert "memory" in printed.lower()
    assert "nothing was recorded" in printed.lower()


def test_a_healthy_run_summary_says_nothing_about_memory(tmp_path: Path) -> None:
    from rudra.loop.engine import summarise
    from rudra.loop.ledger import Ledger, TaskStatus

    ledger = Ledger()
    ledger.add(["do the thing"])
    ledger.tasks[0].status = TaskStatus.DONE

    console = Console(record=True, width=200)
    summarise(ledger, console)

    assert "nothing was recorded" not in console.export_text().lower()


def test_a_run_does_not_inherit_the_previous_runs_failure(tmp_path: Path) -> None:
    """The recorded failure is process-global, and the REPL builds a fresh
    agent per input -- so without a reset, turn two reports turn one's
    breakage over a palace that is fine."""
    from rich.console import Console as RichConsole

    from rudra.agent.main_agent import build_memory_store
    from rudra.config import get_config
    from rudra.memory.entry import MemoryEntry
    from rudra.memory.store import MemoryStore

    MemoryStore(broken_project(tmp_path)).write(
        MemoryEntry(content="x", room="tasks", added_by="rudra")
    )

    healthy = tmp_path / "healthy"
    healthy.mkdir()
    build_memory_store(healthy, get_config(healthy), RichConsole())

    from rudra.memory.degrade import last_failure

    assert last_failure() is None


# --- the memory commands --------------------------------------------------


def test_memory_list_reports_a_broken_palace(tmp_path: Path) -> None:
    """ "Nothing recorded for this project yet" is a false statement about a
    store that could not be read."""
    result = runner.invoke(app, ["memory", "list", "-d", str(broken_project(tmp_path))])

    assert result.exit_code == 1
    assert "unavailable" in result.stdout.lower()
    assert "Nothing recorded for this project yet" not in result.stdout


def test_memory_search_reports_a_broken_palace(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["memory", "search", "anything", "-d", str(broken_project(tmp_path))]
    )

    assert result.exit_code == 1
    assert "unavailable" in result.stdout.lower()


def test_memory_export_reports_a_broken_palace(tmp_path: Path) -> None:
    """An export that silently writes nothing is the worst case here: the
    user believes they have a durable copy."""
    result = runner.invoke(app, ["memory", "export", "-d", str(broken_project(tmp_path))])

    assert result.exit_code == 1
    assert "unavailable" in result.stdout.lower()


def test_memory_forget_reports_a_broken_palace(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["memory", "forget", "--all", "--yes", "-d", str(broken_project(tmp_path))]
    )

    assert result.exit_code == 1
    assert "unavailable" in result.stdout.lower()


def test_a_healthy_empty_palace_still_says_nothing_recorded(tmp_path: Path) -> None:
    """Empty and broken must not print the same thing, or the new message
    means nothing."""
    project = tmp_path / "empty"
    project.mkdir()
    result = runner.invoke(app, ["memory", "list", "-d", str(project)])

    assert result.exit_code == 0
    assert "Nothing recorded for this project yet" in result.stdout
