"""What `rudra doctor` says about memory.

The mempalace-config row is C8.1b's, and it is asked for by name: a user
with ~/.mempalace/config.json is having it ignored, and silently ignoring
someone's configuration is how a bug reproduces on one machine only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.memory.entry import MemoryEntry
from rudra.memory.store import MemoryStore

runner = CliRunner()


def test_doctor_reports_the_palace_and_its_contents(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    MemoryStore(project).write(MemoryEntry(content="x", room="tasks", added_by="rudra"))
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])
    assert result.exit_code == 0
    assert "memory" in result.stdout


def test_doctor_reports_an_empty_palace_without_calling_it_broken(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])
    assert result.exit_code == 0


def test_doctor_warns_that_a_user_global_mempalace_config_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8.1b asks for this row by name."""
    home = tmp_path / "home"
    (home / ".mempalace").mkdir(parents=True)
    (home / ".mempalace" / "config.json").write_text(json.dumps({"backend": "qdrant"}))
    monkeypatch.setattr(Path, "home", lambda: home)

    project = tmp_path / "demo"
    project.mkdir()
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])
    assert "ignored" in result.stdout.lower()


def test_doctor_stays_quiet_when_there_is_no_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warning nobody needs is noise that trains people to skip the table."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    project = tmp_path / "demo"
    project.mkdir()
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])
    assert "ignored" not in result.stdout.lower()


def test_doctor_reports_the_model_cache_state(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(project)])
    assert "model" in result.stdout.lower()
