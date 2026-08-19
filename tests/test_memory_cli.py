"""The `rudra memory` surface.

CliRunner throughout, because a Typer command's exit code and its printed
output are both part of its contract -- and because A1.84 showed that a
CliRunner test is what catches a stderr-binding defect the library tests
cannot see.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.memory.entry import MemoryEntry
from rudra.memory.store import MemoryStore

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    project.mkdir()
    store = MemoryStore(project)
    store.write(MemoryEntry(content="we chose uv over pip", room="decisions", added_by="rudra"))
    store.write(MemoryEntry(content="the user prefers tabs", room="preferences", added_by="agent"))
    return project


def test_list_shows_what_was_recorded(project: Path) -> None:
    result = runner.invoke(app, ["memory", "list", "-d", str(project)])
    assert result.exit_code == 0
    assert "uv over pip" in result.stdout


def test_list_shows_who_recorded_each_entry(project: Path) -> None:
    """Without this the user cannot tell a measured fact from a model's
    opinion, which is the distinction `forget --added-by` acts on."""
    result = runner.invoke(app, ["memory", "list", "-d", str(project)])
    assert "agent" in result.stdout
    assert "rudra" in result.stdout


def test_list_can_filter_by_room(project: Path) -> None:
    result = runner.invoke(app, ["memory", "list", "-d", str(project), "--room", "preferences"])
    assert "prefers tabs" in result.stdout
    assert "uv over pip" not in result.stdout


def test_search_finds_by_meaning(project: Path) -> None:
    result = runner.invoke(app, ["memory", "search", "package manager", "-d", str(project)])
    assert result.exit_code == 0
    assert "uv" in result.stdout


def test_an_empty_palace_says_so_rather_than_printing_an_empty_table(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["memory", "list", "-d", str(empty)])
    assert result.exit_code == 0
    assert "nothing" in result.stdout.lower()


def test_forget_refuses_without_confirmation_on_a_non_tty(project: Path) -> None:
    """Destructive and non-interactive: refuse, the way mode="ask" exits 2
    rather than hanging on a prompt nobody can answer."""
    result = runner.invoke(app, ["memory", "forget", "--all", "-d", str(project)])
    assert result.exit_code != 0
    assert MemoryStore(project).count() == 2


def test_forget_all_with_yes_deletes_everything(project: Path) -> None:
    result = runner.invoke(app, ["memory", "forget", "--all", "--yes", "-d", str(project)])
    assert result.exit_code == 0
    assert MemoryStore(project).count() == 0


def test_forget_by_author_deletes_only_that_authors_entries(project: Path) -> None:
    """The purge the two-model acceptance made concrete: a 31B model
    duplicated the spine's task records, and this is how a user cleans that
    up without losing what Rudra measured."""
    result = runner.invoke(
        app, ["memory", "forget", "--added-by", "agent", "--yes", "-d", str(project)]
    )
    assert result.exit_code == 0
    assert [r.added_by for r in MemoryStore(project).list_entries()] == ["rudra"]


def test_forget_needs_a_target(project: Path) -> None:
    """Bare `forget` must not mean `forget --all`."""
    result = runner.invoke(app, ["memory", "forget", "--yes", "-d", str(project)])
    assert result.exit_code != 0
    assert MemoryStore(project).count() == 2


def test_export_then_import_round_trips_through_the_cli(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "exported"
    exported = runner.invoke(app, ["memory", "export", "-d", str(project), "--out", str(out)])
    assert exported.exit_code == 0

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    result = runner.invoke(app, ["memory", "import", str(out), "-d", str(fresh)])
    assert result.exit_code == 0
    assert MemoryStore(fresh).count() == 2
