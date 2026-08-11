"""auto_branch: isolate the run, or explain why it did not."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.git import core
from tests.conftest_git import AutoGate, DenyShellGate, git, head_sha, make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def env(tmp_path: Path) -> dict:
    return {
        "gate": AutoGate(tmp_path),
        "console": Console(),
        "cfg": build_config(tmp_path),
    }


def test_slug_is_lowercased_and_hyphenated():
    assert core.branch_slug("Build a Flask App") == "build-a-flask-app"


def test_slug_collapses_punctuation_and_trims_edges():
    assert core.branch_slug("  fix: the __init__ bug!!  ") == "fix-the-init-bug"


def test_slug_is_capped():
    assert len(core.branch_slug("word " * 50)) <= 40


def test_slug_never_ends_with_a_hyphen_after_capping():
    # A cap landing mid-separator would produce "rudra/foo-", which git rejects.
    assert not core.branch_slug("a" * 39 + " bbbb").endswith("-")


def test_slug_falls_back_when_nothing_survives():
    assert core.branch_slug("!!!") == "task"


def test_slug_of_an_empty_task_falls_back():
    assert core.branch_slug("") == "task"


def test_auto_branch_creates_a_branch_from_a_clean_tree(repo: Path, env: dict):
    outcome = core.auto_branch(repo, "build a flask app", **env)
    assert outcome.branch == "rudra/build-a-flask-app"
    assert outcome.skipped_reason is None
    assert core.current_branch(repo, **env) == "rudra/build-a-flask-app"


def test_auto_branch_suffixes_a_name_already_in_use(repo: Path, env: dict):
    core.auto_branch(repo, "same task", **env)
    git(repo, "checkout", "-q", "main")
    outcome = core.auto_branch(repo, "same task", **env)
    assert outcome.branch == "rudra/same-task-2"


def test_auto_branch_skips_a_dirty_tree_and_says_why(repo: Path, env: dict):
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    outcome = core.auto_branch(repo, "task", **env)
    assert outcome.branch is None
    assert "uncommitted" in outcome.skipped_reason
    assert core.current_branch(repo, **env) == "main", "the branch must not change"


def test_auto_branch_skips_a_detached_head(repo: Path, env: dict):
    git(repo, "checkout", "-q", head_sha(repo))
    outcome = core.auto_branch(repo, "task", **env)
    assert outcome.branch is None
    assert "detached" in outcome.skipped_reason


def test_auto_branch_skips_a_plain_directory(tmp_path: Path, env: dict):
    plain = tmp_path / "plain"
    plain.mkdir()
    outcome = core.auto_branch(plain, "task", **env)
    assert outcome.branch is None
    assert "git repository" in outcome.skipped_reason


def test_auto_branch_reports_a_denial_rather_than_raising(repo: Path, tmp_path: Path):
    """A1.49: bare --auto denies execute, so the checkout is refused."""
    common = {
        "gate": DenyShellGate(tmp_path),
        "console": Console(),
        "cfg": build_config(repo),
    }
    outcome = core.auto_branch(repo, "task", **common)
    assert outcome.branch is None
    assert outcome.skipped_reason is not None
    assert core.current_branch(repo, **common) == "main"


def test_a_skipped_outcome_never_reports_a_branch(repo: Path, env: dict):
    """Exactly one field is set -- callers switch on `branch is None`."""
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    outcome = core.auto_branch(repo, "task", **env)
    assert (outcome.branch is None) != (outcome.skipped_reason is None)


def test_rudras_own_directory_does_not_count_as_a_dirty_tree(repo: Path, env: dict):
    """A1.55: ensure_layout runs before auto_branch, so .rudra/ is always there."""
    rudra_dir = repo / ".rudra"
    rudra_dir.mkdir()
    (rudra_dir / "config.toml").write_text("[agent]\nverbose = true\n", encoding="utf-8")

    assert core.is_clean(repo, **env) is True
    outcome = core.auto_branch(repo, "task", **env)
    assert outcome.branch == "rudra/task", outcome.skipped_reason


def test_the_users_own_untracked_file_still_blocks(repo: Path, env: dict):
    """The precondition is narrowed, not removed."""
    (repo / ".rudra").mkdir()
    (repo / ".rudra" / "config.toml").write_text("x", encoding="utf-8")
    (repo / "mine.txt").write_text("real work", encoding="utf-8")

    assert core.is_clean(repo, **env) is False
    assert core.auto_branch(repo, "task", **env).branch is None


def test_a_file_merely_starting_with_rudra_still_counts(repo: Path, env: dict):
    """Matched as a path component, not a string prefix."""
    (repo / ".rudra-notes.md").write_text("my notes", encoding="utf-8")
    assert core.is_clean(repo, **env) is False


def test_is_clean_is_false_in_a_plain_directory(tmp_path: Path, env: dict):
    """A failed git call must never read as permission to branch."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.is_clean(plain, **env) is False


def test_status_still_reports_rudras_directory(repo: Path, env: dict):
    """status() reports what git reports; only is_clean narrows."""
    (repo / ".rudra").mkdir()
    (repo / ".rudra" / "config.toml").write_text("x", encoding="utf-8")
    assert any(entry.path.startswith(".rudra") for entry in core.status(repo, **env))
