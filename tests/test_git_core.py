"""git/core against real repositories. git's presence was verified in C3.1."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.git import core
from tests.conftest_git import AutoGate, StrictAskGate, git, head_sha, make_repo


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


def test_is_repo_is_true_inside_a_repository(repo: Path, env: dict):
    assert core.is_repo(repo, **env) is True


def test_is_repo_is_false_in_a_plain_directory(tmp_path: Path, env: dict):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.is_repo(plain, **env) is False


def test_current_branch_reports_the_branch(repo: Path, env: dict):
    assert core.current_branch(repo, **env) == "main"


def test_current_branch_is_none_when_head_is_detached(repo: Path, env: dict):
    git(repo, "checkout", "-q", head_sha(repo))
    assert core.current_branch(repo, **env) is None


def test_is_clean_is_true_after_a_commit(repo: Path, env: dict):
    assert core.is_clean(repo, **env) is True


def test_is_clean_is_false_with_an_untracked_file(repo: Path, env: dict):
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert core.is_clean(repo, **env) is False


def test_is_clean_is_false_with_a_modified_tracked_file(repo: Path, env: dict):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert core.is_clean(repo, **env) is False


def test_status_reports_paths_and_codes(repo: Path, env: dict):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    entries = {entry.path: entry for entry in core.status(repo, **env)}
    assert entries["a.txt"].worktree == "M"
    assert entries["b.txt"].index == "?"


def test_status_handles_paths_containing_spaces(repo: Path, env: dict):
    (repo / "two words.txt").write_text("x", encoding="utf-8")
    assert "two words.txt" in {entry.path for entry in core.status(repo, **env)}


def test_status_reports_the_new_name_of_a_rename(repo: Path, env: dict):
    git(repo, "mv", "a.txt", "renamed.txt")
    assert "renamed.txt" in {entry.path for entry in core.status(repo, **env)}


def test_status_of_a_plain_directory_is_unknown_not_empty(tmp_path: Path, env: dict):
    """CR-E12: git failing and "nothing changed" are different answers.

    This returned [], so a caller could not tell a clean tree from a git
    that never ran -- and on that reading `rudra verify` scanned zero files
    and reported "all 5 stages ran clean" over an unexamined tree, while
    the loop read every attempt as "the coder wrote nothing". None is the
    answer the callers already handle for the not-a-repo case.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.status(plain, **env) is None


def test_diff_shows_working_tree_changes(repo: Path, env: dict):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = core.diff(repo, **env)
    assert "-one" in text
    assert "+changed" in text


def test_diff_is_capped_and_says_so(repo: Path, env: dict):
    (repo / "a.txt").write_text("\n".join(str(n) for n in range(500)) + "\n", encoding="utf-8")
    text = core.diff(repo, max_lines=20, **env)
    assert len(text.splitlines()) <= 25
    assert "truncated" in text.lower()


def test_diff_of_a_clean_tree_is_empty(repo: Path, env: dict):
    assert core.diff(repo, **env).strip() == ""


def test_diff_can_be_limited_to_one_path(repo: Path, env: dict):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    git(repo, "add", "b.txt")
    text = core.diff(repo, path="a.txt", **env)
    assert "a.txt" in text
    assert "b.txt" not in text


def test_log_returns_commits_newest_first(repo: Path, env: dict):
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    git(repo, "commit", "-qam", "second: with a colon")
    commits = core.log(repo, count=5, **env)
    assert [c.subject for c in commits] == ["second: with a colon", "first"]
    assert commits[0].author == "Test"
    assert len(commits[0].sha) == 40


def test_log_of_a_plain_directory_is_empty(tmp_path: Path, env: dict):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert core.log(plain, **env) == []


def test_branch_exists_distinguishes_present_from_absent(repo: Path, env: dict):
    assert core.branch_exists(repo, "main", **env) is True
    assert core.branch_exists(repo, "nope", **env) is False


def test_read_only_git_never_prompts_in_ask_mode(repo: Path, tmp_path: Path):
    """auto_branch fires three reads before the planner starts (S8.7)."""
    common = {"gate": StrictAskGate(tmp_path), "console": Console(), "cfg": build_config(repo)}
    assert core.is_repo(repo, **common) is True
    assert core.current_branch(repo, **common) == "main"
    assert core.is_clean(repo, **common) is True
    assert core.log(repo, **common)
    assert core.diff(repo, **common) == ""


def test_every_read_only_subcommand_is_one_rudra_composes(repo: Path, env: dict):
    """The bypass covers reads only. A writing subcommand must not be in it."""
    for writing in ("checkout", "commit", "push", "reset", "clean", "merge"):
        assert writing not in core.READ_ONLY_SUBCOMMANDS


def test_status_decodes_a_c_quoted_non_ascii_path(repo: Path, env: dict):
    """CR-E3: git C-quotes any path needing it, so `café.py` arrives as
    `"caf\\303\\251.py"`. Stripping the quotes alone yielded the literal
    backslash form, which does not exist on disk -- so `rudra verify` fed a
    nonexistent path to the syntax stage, and in the loop `git_snapshot`
    digested an absent file, making a real edit invisible and every attempt
    read as "the coder wrote nothing" (the A1.66 class).
    """
    (repo / "café.py").write_text("x = 1\n", encoding="utf-8")

    paths = {entry.path for entry in core.status(repo, **env)}

    assert "café.py" in paths
    assert (repo / "café.py").exists()
