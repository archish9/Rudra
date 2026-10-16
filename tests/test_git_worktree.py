"""git/core worktree primitives against real repositories."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.git import core
from tests.conftest_git import AutoGate, make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return make_repo(root)


@pytest.fixture
def env(tmp_path: Path) -> dict:
    return {
        "gate": AutoGate(tmp_path),
        "console": Console(),
        "cfg": build_config(tmp_path),
    }


def test_worktree_roundtrip(repo: Path, tmp_path: Path, env: dict):
    wt = tmp_path / "wt-1"
    assert core.worktree_add(repo, wt, **env) is True
    assert wt.is_dir()

    (wt / "a.py").write_text("x\n", encoding="utf-8")
    assert core.commit_all(wt, "add a.py", **env) is True

    result = core.merge(repo, "rudra-wt/wt-1", **env)
    assert result == core.MergeResult(ok=True, conflicted=False)
    assert (repo / "a.py").exists()

    assert core.worktree_remove(repo, wt, **env) is True
    assert not wt.exists()


def test_worktree_add_honours_explicit_branch(repo: Path, tmp_path: Path, env: dict):
    wt = tmp_path / "wt-2"
    assert core.worktree_add(repo, wt, branch="custom/b", **env) is True
    assert core.branch_exists(repo, "custom/b", **env)


def test_commit_all_returns_false_when_nothing_to_commit(repo: Path, tmp_path: Path, env: dict):
    wt = tmp_path / "wt-3"
    assert core.worktree_add(repo, wt, **env)
    assert core.commit_all(wt, "nothing", **env) is False


def test_merge_conflict_detected(repo: Path, tmp_path: Path, env: dict):
    wt = tmp_path / "wt-4"
    assert core.worktree_add(repo, wt, **env)

    (wt / "a.txt").write_text("from worktree\n", encoding="utf-8")
    assert core.commit_all(wt, "wt edit", **env)

    (repo / "a.txt").write_text("from main\n", encoding="utf-8")
    assert core.commit_all(repo, "main edit", **env)

    result = core.merge(repo, "rudra-wt/wt-4", **env)
    assert result.ok is False
    assert result.conflicted is True

    assert core.merge_abort(repo, **env) is True
    entries = core.status(repo, **env)
    assert entries is not None
    assert not [e for e in entries if "U" in (e.index, e.worktree)]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "from main\n"


def test_merge_failure_without_conflict_is_not_conflicted(repo: Path, env: dict):
    result = core.merge(repo, "no-such-ref", **env)
    assert result == core.MergeResult(ok=False, conflicted=False)


def test_new_subcommands_are_not_read_only():
    for name in ("worktree", "merge", "commit", "add"):
        assert name not in core.READ_ONLY_SUBCOMMANDS
