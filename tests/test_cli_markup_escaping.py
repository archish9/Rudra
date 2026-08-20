"""User data printed through Rich is DATA, not markup (A1.48 / A1.67 / A1.91).

Three surfaces, one defect class:

* a config error naming `[skills]` (A1.48)
* a tool result carrying `[pending]` (A1.67, covered in test_trace_render.py)
* a **table cell** carrying a path like `/tmp/[draft]/proj` (A1.91)

The third is the one neither earlier row saw: both named `console.print`,
and `rich.table.Table` parses cell markup just the same. Measured with
bare Rich -- `Table().add_row("memory", "/tmp/[draft]/proj")` renders
`/tmp//proj` -- so the user is told a path that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config import reset_config


@pytest.fixture(autouse=True)
def _clean_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


def test_rich_tables_really_do_eat_markup():
    """The premise of A1.91, pinned so nobody has to re-measure it."""
    console = Console(record=True, width=120)
    table = Table()
    table.add_column("a")
    table.add_column("b")
    table.add_row("memory", "/tmp/[draft]/proj")
    console.print(table)
    assert "/tmp//proj" in console.export_text()


def test_a_config_error_names_the_section_it_is_about(tmp_path: Path):
    """A1.48: the user was told what was wrong and never where."""
    project = tmp_path / "proj"
    (project / ".rudra").mkdir(parents=True)
    (project / ".rudra" / "config.toml").write_text("[skills]\nx = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["config", "list", "--project-dir", str(project)])

    assert result.exit_code != 0
    assert "[skills]" in result.output


def test_doctor_does_not_swallow_a_bracketed_project_directory(tmp_path: Path):
    """A1.91's regression net, and deliberately broader than the two rows
    fixed in Step 15a: it fails for ANY doctor row that interpolates the
    project path unescaped, which is how the remaining sweep gets found."""
    project = tmp_path / "[draft] proj"
    project.mkdir()

    result = CliRunner().invoke(app, ["doctor", "--offline", "--project-dir", str(project)])

    assert "[draft] proj" in result.output


def test_config_list_does_not_swallow_a_bracketed_value(tmp_path: Path):
    """A1.91's remainder. Built from a real config.toml rather than a
    hand-made Config, so it exercises the path a user actually takes."""
    project = tmp_path / "proj"
    (project / ".rudra").mkdir(parents=True)
    (project / ".rudra" / "config.toml").write_text(
        '[model.default]\nmodel = "vendor/model[preview]"\n', encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["config", "list", "--project-dir", str(project)])

    assert result.exit_code == 0
    assert "[preview]" in result.output


def test_doctor_reports_whether_ripgrep_is_on_path(tmp_path: Path, monkeypatch):
    """C9.9's disposition: deepagents already uses rg when it is there, so
    the value left to add is letting a user see which path they are on."""
    import shutil

    project = tmp_path / "proj"
    project.mkdir()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    present = CliRunner().invoke(app, ["doctor", "--offline", "--project-dir", str(project)])
    assert "/usr/bin/rg" in present.output

    monkeypatch.setattr(shutil, "which", lambda name: None)
    absent = CliRunner().invoke(app, ["doctor", "--offline", "--project-dir", str(project)])
    assert "not on PATH" in absent.output
