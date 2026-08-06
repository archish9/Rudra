"""Tests for rudra.filesystem.tree.project_tree.

project_tree replaces VirtualFileSystem.get_tree(), deleted in Step 2 (D7).
The contract: report file NAMES only, never read the content of a listed
file, and stay bounded by an explicit depth and entry cap.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rudra.filesystem.tree import project_tree

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not on PATH"
)


def _write(root: Path, rel: str, content: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")


@requires_git
def test_gitignored_paths_are_excluded(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py")
    _write(tmp_path, "build/artifact.o")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "build/artifact.o" not in lines


def test_fallback_honors_gitignore_without_a_repo(tmp_path: Path) -> None:
    # No `git init` at all, so _git_listing returns None and the wcmatch
    # walk runs. Same expected output as the git path above.
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py")
    _write(tmp_path, "build/artifact.o")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "build/artifact.o" not in lines


def test_max_depth_drops_deeper_paths(tmp_path: Path) -> None:
    _write(tmp_path, "a/b/shallow.txt")
    _write(tmp_path, "a/b/c/d/e/f/deep.txt")

    lines = project_tree(tmp_path, max_depth=3).splitlines()

    assert "a/b/shallow.txt" in lines
    assert "a/b/c/d/e/f/deep.txt" not in lines


def test_max_entries_truncates_and_reports_remainder(tmp_path: Path) -> None:
    for index in range(20):
        _write(tmp_path, f"file_{index:02d}.txt")

    lines = project_tree(tmp_path, max_entries=5).splitlines()

    assert len(lines) == 6
    assert lines[-1] == "… 15 more entries omitted (cap: 5)"


@requires_git
def test_builtin_skips_apply_even_when_git_tracks_them(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "app.py")
    _write(tmp_path, ".rudra/PLAN.md")
    _write(tmp_path, "node_modules/pkg/index.js")
    _git(tmp_path, "add", "-f", "app.py", ".rudra/PLAN.md", "node_modules/pkg/index.js")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert ".rudra/PLAN.md" not in lines
    assert "node_modules/pkg/index.js" not in lines


def test_never_reads_the_content_of_a_listed_file(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py", "secret" * 1000)
    _write(tmp_path, "src/models.py")

    real_read_text = Path.read_text

    def guarded(self: Path, *args, **kwargs):
        if self.name != ".gitignore":
            raise AssertionError(f"project_tree read file content: {self}")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "src/models.py" in lines


def test_empty_or_missing_root(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert project_tree(empty) == "(empty project)"
    assert project_tree(tmp_path / "does_not_exist") == "(empty project)"
