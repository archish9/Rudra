"""What the loop believes an attempt touched (A1.66).

Against real repositories, because the defect this file exists for is
invisible to a mock: `git status --porcelain` collapses an untracked
directory into a single entry, so a second write *inside* that directory
produces no new entry and `changed_since` returns nothing.

Measured live on 2026-08-13 (Step 10a acceptance): task 1 created
`Cargo.toml` and `src/`, tasks 2, 3, 6 and 8 each wrote into `src/`, and
every one of them was blocked with "the coder wrote nothing" while
`src/main.rs` sat on disk. The gate never saw those files, so the fix
loop never saw their compile errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.loop.engine import attempt_snapshot, changed_since, git_snapshot
from tests.conftest_git import AutoGate, git, make_repo


@dataclass
class FakeSubagents:
    gate: Any


@dataclass
class FakeContext:
    """Only the four attributes git_snapshot and changed_since read."""

    subagents: Any
    project_path: Path
    console: Console
    cfg: Any


@pytest.fixture
def context(tmp_path: Path) -> FakeContext:
    make_repo(tmp_path)
    return FakeContext(
        subagents=FakeSubagents(gate=AutoGate(tmp_path)),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=build_config(tmp_path),
    )


def test_a_new_file_in_a_new_directory_is_reported_as_a_file(context: FakeContext):
    before = git_snapshot(context)
    (context.project_path / "src").mkdir()
    (context.project_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    assert changed_since(context, before) == ("src/main.rs",)


def test_a_second_file_in_an_already_seen_directory_still_counts(context: FakeContext):
    """A1.66's exact shape: this returned () and blocked the task."""
    (context.project_path / "src").mkdir()
    (context.project_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    before = git_snapshot(context)
    (context.project_path / "src" / "lib.rs").write_text("pub fn f() {}\n", encoding="utf-8")

    assert changed_since(context, before) == ("src/lib.rs",)


def test_editing_a_file_inside_an_already_seen_directory_counts(context: FakeContext):
    """The live failure verbatim: task 1 makes src/, task 2 rewrites in it."""
    (context.project_path / "src").mkdir()
    main = context.project_path / "src" / "main.rs"
    main.write_text("fn main() {}\n", encoding="utf-8")

    before = git_snapshot(context)
    main.write_text('fn main() { println!("hi"); }\n', encoding="utf-8")

    assert changed_since(context, before) == ("src/main.rs",)


def test_no_directory_entry_ever_reaches_the_ledger(context: FakeContext):
    before = git_snapshot(context)
    nested = context.project_path / "src" / "deep"
    nested.mkdir(parents=True)
    (nested / "mod.rs").write_text("pub mod x;\n", encoding="utf-8")

    touched = changed_since(context, before)
    assert touched == ("src/deep/mod.rs",)
    assert not any(path.endswith("/") for path in touched)


def test_build_output_is_pruned(context: FakeContext):
    """Every stack profile declares its build dirs; the loop must use them.

    A Rust target/ holds thousands of files, and reporting them as work
    the coder did is both wrong and enormous.
    """
    before = git_snapshot(context)
    for directory in ("target", "node_modules", "__pycache__", ".venv"):
        built = context.project_path / directory / "sub"
        built.mkdir(parents=True)
        (built / "artifact.rs").write_text("// generated\n", encoding="utf-8")
    (context.project_path / "real.rs").write_text("fn main() {}\n", encoding="utf-8")

    assert changed_since(context, before) == ("real.rs",)


def test_rudra_s_own_directory_is_pruned(context: FakeContext):
    """.rudra/ churns every run: the ledger and facts.json are not the coder's work."""
    before = git_snapshot(context)
    rudra = context.project_path / ".rudra" / "run"
    rudra.mkdir(parents=True)
    (rudra / "ledger.json").write_text("{}\n", encoding="utf-8")
    (context.project_path / "real.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_since(context, before) == ("real.py",)


def test_a_tracked_file_modification_is_still_reported(context: FakeContext):
    """The case that always worked must keep working."""
    before = git_snapshot(context)
    (context.project_path / "a.txt").write_text("two\n", encoding="utf-8")

    assert changed_since(context, before) == ("a.txt",)


def test_a_deleted_file_is_reported(context: FakeContext):
    before = git_snapshot(context)
    (context.project_path / "a.txt").unlink()

    assert changed_since(context, before) == ("a.txt",)


@pytest.fixture
def no_repo(tmp_path: Path) -> FakeContext:
    """A project directory that is not a git repository."""
    return FakeContext(
        subagents=FakeSubagents(gate=AutoGate(tmp_path)),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=build_config(tmp_path),
    )


def test_outside_a_repository_the_caller_gets_none(no_repo: FakeContext):
    """None is git_snapshot's own answer, and stays that way.

    `attempt_snapshot` is what the loop calls, and it turns this None into a
    tree walk (OPEN-13). This test pins the lower-level contract so the two
    do not get merged by accident.
    """
    assert git_snapshot(no_repo) is None


def test_outside_a_repository_an_attempt_still_has_a_snapshot(no_repo: FakeContext):
    """OPEN-13: no git must not mean no answer."""
    (no_repo.project_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    before = attempt_snapshot(no_repo)
    assert "app.py" in before
    assert changed_since(no_repo, before) == ()


def test_outside_a_repository_a_written_file_is_reported(no_repo: FakeContext):
    """The half that was missing: writes are visible without git."""
    before = attempt_snapshot(no_repo)
    (no_repo.project_path / "src").mkdir()
    (no_repo.project_path / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("src/main.py",)


def test_outside_a_repository_a_rewrite_is_reported(no_repo: FakeContext):
    """A fix-loop retry rewrites a file it already wrote. Same file, new
    content, and the fingerprint is the only thing that says so."""
    target = no_repo.project_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")

    before = attempt_snapshot(no_repo)
    target.write_text("x = 2\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("app.py",)


def test_outside_a_repository_build_output_is_not_attributed_to_the_coder(
    no_repo: FakeContext,
):
    """`source_files` prunes it, and the tree snapshot inherits that."""
    before = attempt_snapshot(no_repo)
    build = no_repo.project_path / "node_modules" / "pkg"
    build.mkdir(parents=True)
    (build / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ()


def test_a_path_with_spaces_survives(context: FakeContext):
    before = git_snapshot(context)
    (context.project_path / "my dir").mkdir()
    (context.project_path / "my dir" / "a file.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_since(context, before) == ("my dir/a file.py",)


def test_a_committed_change_is_no_longer_pending(context: FakeContext):
    """Sanity: the snapshot reads the working tree, not history."""
    (context.project_path / "new.py").write_text("x = 1\n", encoding="utf-8")
    git(context.project_path, "add", "new.py")
    git(context.project_path, "commit", "-q", "-m", "add new")

    assert "new.py" not in (git_snapshot(context) or frozenset())


# --- OPEN-42: a marker file is work in a repo and invisible outside one ---


def test_a_marker_file_registers_as_work_in_a_git_project(context: FakeContext):
    """The hazard OPEN-42 is filed against, which run7 could not show.

    `git_snapshot` passes `all_untracked=True` and fingerprints contents, so
    it sees a suffixless `DONE` like any other file. A task whose coder wrote
    nothing but its own completion announcement therefore reports
    `files_touched`, and the empty-diff guard (OPEN-13) never runs.
    """
    before = git_snapshot(context)
    (context.project_path / "DONE").write_text("The task is complete.\n", encoding="utf-8")
    (context.project_path / "task_complete.txt").write_text("Task t5 done.\n", encoding="utf-8")

    assert changed_since(context, before) == ("DONE", "task_complete.txt")


def test_a_marker_file_is_invisible_without_git(no_repo: FakeContext):
    """Run7's own case, recorded so the asymmetry is documented rather than
    surprising.

    `tree_snapshot` walks `source_files`, which filters on `_SCANNED_SUFFIXES`
    (verify/stubs.py:30-33). `DONE` has no suffix and `.txt` is not in the
    set, so both markers are invisible -- which is why run7's t4 and t5
    correctly show `files_touched: []` despite three marker writes each.
    """
    before = attempt_snapshot(no_repo)
    (no_repo.project_path / "DONE").write_text("The task is complete.\n", encoding="utf-8")
    (no_repo.project_path / "task_complete.txt").write_text("Task t5 done.\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ()
