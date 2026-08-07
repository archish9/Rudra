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

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


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


@requires_git
def test_gitignored_root_still_lists_its_own_files(tmp_path: Path) -> None:
    """A1.19 — a root that git ignores must still be listed.

    `git ls-files --cached --others --exclude-standard` exits **0 with empty
    stdout** here: the directory is inside a work tree, so git answers, and
    the answer is "nothing". That is a success, not a failure, so a fallback
    keyed on `is None` never fires and the directory reports as empty.
    """
    _init_repo(tmp_path)
    _write(tmp_path, ".gitignore", "playground/\n")
    _write(tmp_path, "playground/main.py")
    _write(tmp_path, "playground/src/mod.py")

    lines = project_tree(tmp_path / "playground").splitlines()

    assert "main.py" in lines
    assert "src/mod.py" in lines


def _assert_never_reads_content(tmp_path: Path, monkeypatch) -> None:
    """Shared body for the content-blindness guard.

    Guards both Path.read_text and Path.read_bytes (Finding 2) so a future
    regression that reads via either call trips the guard.

    The exemption is the **root .gitignore by exact path**, not by filename:
    `_gitignore_patterns` legitimately reads that one file, and nothing
    else. Exempting `self.name == ".gitignore"` would silently permit a
    future implementation that walks into nested .gitignore files (A1.21),
    which is a content read of a listed path.
    """
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py", "secret" * 1000)
    _write(tmp_path, "src/models.py")
    _write(tmp_path, "src/.gitignore", "*.tmp\n")

    root_gitignore = (tmp_path / ".gitignore").resolve()

    def _is_exempt(path: Path) -> bool:
        try:
            return path.resolve() == root_gitignore
        except OSError:
            return False

    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def guarded_read_text(self: Path, *args, **kwargs):
        if not _is_exempt(self):
            raise AssertionError(f"project_tree read file content via read_text: {self}")
        return real_read_text(self, *args, **kwargs)

    def guarded_read_bytes(self: Path, *args, **kwargs):
        if not _is_exempt(self):
            raise AssertionError(f"project_tree read file content via read_bytes: {self}")
        return real_read_bytes(self, *args, **kwargs)

    real_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if not _is_exempt(self):
            raise AssertionError(f"project_tree read file content via open: {self}")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "open", guarded_open)

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "src/models.py" in lines


def test_never_reads_the_content_of_a_listed_file_fallback(tmp_path: Path, monkeypatch) -> None:
    # No git repo, so _git_listing returns None and the wcmatch walk runs.
    _assert_never_reads_content(tmp_path, monkeypatch)


@requires_git
def test_never_reads_the_content_of_a_listed_file_git(tmp_path: Path, monkeypatch) -> None:
    # git ls-files answers directly; this must stay content-blind too —
    # it is the path that runs in every real repo.
    _init_repo(tmp_path)
    _assert_never_reads_content(tmp_path, monkeypatch)


def test_empty_or_missing_root(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert project_tree(empty) == "(empty project)"
    assert project_tree(tmp_path / "does_not_exist") == "(empty project)"


def test_build_output_dirs_are_never_listed(tmp_path: Path) -> None:
    """F1: without this, a Rust target/ fills the 300-entry budget and
    truncates away the source the planner needs to see."""
    _write(tmp_path, "Cargo.toml", "[package]")
    _write(tmp_path, "src/main.rs", "fn main() {}")
    _write(tmp_path, "target/debug/app", "binary")
    _write(tmp_path, "target/debug/deps/libfoo.rlib", "x")

    tree = project_tree(tmp_path)

    assert "src/main.rs" in tree
    assert "Cargo.toml" in tree
    assert "target" not in tree


def test_frontend_build_dirs_are_never_listed(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "src/app/app.component.ts", "export class App {}")
    _write(tmp_path, ".next/cache/x", "x")
    _write(tmp_path, "dist/main.js", "x")
    _write(tmp_path, ".angular/cache/y", "y")

    tree = project_tree(tmp_path)

    assert "src/app/app.component.ts" in tree
    assert ".next" not in tree
    assert "dist/" not in tree
    assert ".angular" not in tree


def test_skip_dirs_are_sourced_from_the_stack_registry() -> None:
    """Single source of truth: adding a stack must not require editing tree.py."""
    from rudra.filesystem.tree import _ALWAYS_SKIP_DIRS
    from rudra.stacks import ALL_SKIP_DIRS

    assert ALL_SKIP_DIRS <= _ALWAYS_SKIP_DIRS
