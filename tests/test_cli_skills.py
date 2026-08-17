"""Step 11c's user-facing surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config.loader import reset_config

GOOD = """---
name: {name}
description: {description}
---

# {name}
"""


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def _project(tmp_path: Path, body: str = "") -> Path:
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def _skill(root: Path, name: str, description: str = "Use when testing") -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        GOOD.format(name=name, description=description), encoding="utf-8"
    )


def test_list_shows_bundled_skills_and_their_enabled_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    result = CliRunner().invoke(app, ["skills", "list", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "brainstorming" in result.output
    assert "bundled" in result.output
    # A skill that ships disabled must still be listed, marked as such.
    assert "using-git-worktrees" in result.output


def test_list_shows_a_project_skill(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project = _project(tmp_path)
    _skill(project / ".rudra" / "skills", "deploy-checklist")

    result = CliRunner().invoke(app, ["skills", "list", "-d", str(project)])

    assert result.exit_code == 0, result.output
    assert "deploy-checklist" in result.output
    assert "project" in result.output


def test_validate_accepts_a_good_tree(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, "deploy-checklist")

    result = CliRunner().invoke(app, ["skills", "validate", str(root)])

    assert result.exit_code == 0, result.output


def test_validate_rejects_a_broken_skill_and_says_why(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    directory = root / "broken"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["skills", "validate", str(root)])

    assert result.exit_code == 1
    assert "broken" in result.output


def test_rebuild_reports_the_cache_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    result = CliRunner().invoke(app, ["skills", "rebuild", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "skills" in result.output


def test_rebuild_prunes_stale_keys(tmp_path: Path, monkeypatch) -> None:
    """Old renders accumulate silently; rebuild is where they are cleared."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stale = tmp_path / "cache" / "rudra" / "skills" / "deadbeefdeadbeef"
    stale.mkdir(parents=True)
    (stale / ".complete").write_text("", encoding="utf-8")

    result = CliRunner().invoke(app, ["skills", "rebuild", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0, result.output
    assert not stale.exists()


def test_rebuild_says_so_when_skills_are_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project = _project(tmp_path, "[skills]\nenabled = []\n")

    result = CliRunner().invoke(app, ["skills", "rebuild", "-d", str(project)])

    assert result.exit_code == 0, result.output
    assert "disabled" in result.output.lower()


def test_list_marks_an_invalid_skill_rather_than_claiming_it_loads(
    tmp_path: Path, monkeypatch
) -> None:
    """Caught by running the command, not by the tests above.

    An unparseable skill is skipped silently by SkillsMiddleware, so
    reporting it as in-prompt would make `list` lie in precisely the case
    it exists to explain.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project = _project(tmp_path)
    broken = project / ".rudra" / "skills" / "oops"
    broken.mkdir(parents=True)
    (broken / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["skills", "list", "-d", str(project)])

    assert result.exit_code == 0, result.output
    line = next(ln for ln in result.output.splitlines() if "oops" in ln)
    assert "invalid" in line
