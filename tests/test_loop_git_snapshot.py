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


def test_a_marker_file_registers_as_work_without_git_too(no_repo: FakeContext):
    """Was `test_a_marker_file_is_invisible_without_git`, and it asserted the
    invisibility was *correct* -- "which is why run7's t4 and t5 correctly
    show `files_touched: []` despite three marker writes each".

    OPEN-63 is why that reading did not hold. It works for `DONE` and
    `task_complete.txt`, which are noise, and fails for `requirements.txt`,
    `Cargo.toml`, `package.json` and `Dockerfile`, which are deliverables --
    the filter cannot tell them apart, because it never looks at anything
    but the suffix. run12 wrote a `requirements.txt` nobody recorded.

    So the marker files are visible now, on both paths, and the loop's
    protection against a marker faking progress is where it always actually
    was: an attempt that writes only a marker reaches a whole-project gate
    (verify/__init__.py:65-70) which judges the project, not the marker.
    """
    before = attempt_snapshot(no_repo)
    (no_repo.project_path / "DONE").write_text("The task is complete.\n", encoding="utf-8")
    (no_repo.project_path / "task_complete.txt").write_text("Task t5 done.\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("DONE", "task_complete.txt")


# --- OPEN-60 §8.3: refusing a no-op write cannot change the loop's verdict --
#
# A PIN, not a new behaviour. `RepeatGuardMiddleware` now refuses a write
# whose bytes are already on disk, and the empty-diff guard
# (`run_task`, OPEN-13) fails an attempt that touched nothing. If refusing
# the write made an attempt look emptier than performing it would have, a
# task that passes today could start failing -- so the equivalence is
# asserted here rather than assumed.
#
# It holds because `changed_since` compares FINGERPRINTS, not events: a
# write that puts back identical bytes moves no fingerprint, so it was
# already invisible before it could be refused.


def test_rewriting_a_file_with_identical_bytes_touches_nothing(context: FakeContext):
    """In a repository. `git status --porcelain` reports content, not writes."""
    tracked = context.project_path / "src" / "main.rs"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("fn main() {}\n", encoding="utf-8")
    git(context.project_path, "add", "src/main.rs")
    git(context.project_path, "commit", "-q", "-m", "add main")

    before = attempt_snapshot(context)
    tracked.write_text("fn main() {}\n", encoding="utf-8")

    assert changed_since(context, before) == ()


def test_rewriting_a_file_with_identical_bytes_touches_nothing_without_git(
    no_repo: FakeContext,
):
    """Outside one, where the fingerprint is Rudra's own (OPEN-12/13)."""
    source = no_repo.project_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")

    before = attempt_snapshot(no_repo)
    source.write_text("x = 1\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ()


def test_a_refused_write_and_a_performed_no_op_write_are_indistinguishable(
    no_repo: FakeContext,
):
    """The equivalence stated directly, which is what §8.3 asked for.

    One attempt performs the redundant write, the other has it refused by
    the guard and never reaches the filesystem. `changed_since` must answer
    the same both times, or the guard would be changing what the loop
    believes an attempt did.
    """
    source = no_repo.project_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")

    performed_before = attempt_snapshot(no_repo)
    source.write_text("x = 1\n", encoding="utf-8")  # the write, performed
    performed = changed_since(no_repo, performed_before)

    refused_before = attempt_snapshot(no_repo)
    # the write, refused: nothing happens to the filesystem at all
    refused = changed_since(no_repo, refused_before)

    assert performed == refused == ()


def test_a_real_change_is_still_seen_after_a_no_op_rewrite(no_repo: FakeContext):
    """The pin must not be satisfiable by a snapshot that sees nothing."""
    source = no_repo.project_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")

    before = attempt_snapshot(no_repo)
    source.write_text("x = 1\n", encoding="utf-8")
    source.write_text("x = 2\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("app.py",)


# --- OPEN-63: the two snapshot paths must answer the same question ---
#
# `tree_snapshot` walked `source_files`, which filters on `_SCANNED_SUFFIXES`
# (verify/stubs.py:33), while `git_snapshot` prunes build output and nothing
# else. So the same coder writing the same file was work in a git project
# and invisible outside one -- and every project Rudra has been run against
# so far is outside one.
#
# Measured on run12 (`2796039bde32`): t1 wrote `requirements.txt`, its
# `files_touched` recorded `['src/app.py', 'tests/test_app.py']`, and t2 --
# whose brief WAS requirements.txt -- then reported "the coder wrote
# nothing, and nothing needed writing". The file the user got was absent
# from the ledger, from AGENTS.md's Session Log, and from the reviewer's
# file list, and TODO.md had to reconstruct it from the debug log by hand.


def test_a_non_source_deliverable_is_reported_without_git(no_repo: FakeContext):
    """run12's defect, reduced. `requirements.txt` is work, not noise."""
    before = attempt_snapshot(no_repo)
    (no_repo.project_path / "requirements.txt").write_text("flask\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("requirements.txt",)


def test_a_file_a_connection_string_named_is_reported_without_git(no_repo: FakeContext):
    """OPEN-55's other half, which nothing in Rudra has ever reported.

    A SQLite URI reaching `open()` as a literal filename left a real
    database on disk in three runs of five -- run9, run10 and run13, four
    files -- and the gate reported `test: passed` over two of them. Rudra
    does not prevent generated code from being wrong. It stops being silent
    about what the run left behind.
    """
    before = attempt_snapshot(no_repo)
    (no_repo.project_path / "file::memory:?cache=shared").write_bytes(b"SQLite format 3\x00")

    assert changed_since(no_repo, before) == ("file::memory:?cache=shared",)


def test_the_same_writes_are_reported_the_same_with_and_without_git(
    context: FakeContext, tmp_path_factory: pytest.TempPathFactory
):
    """The pin, and the only thing here that is really the item.

    Whatever the pruning rules turn out to be, both paths must share them.
    A separate directory rather than the `no_repo` fixture because both
    fixtures take `tmp_path` and `context` turns it into a repository.
    """
    plain_root = tmp_path_factory.mktemp("no_git")
    plain = FakeContext(
        subagents=FakeSubagents(gate=AutoGate(plain_root)),
        project_path=plain_root,
        console=Console(quiet=True),
        cfg=build_config(plain_root),
    )

    def write_the_same_project(root: Path) -> None:
        (root / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
        (root / "README.md").write_text("All tests should now pass.\n", encoding="utf-8")
        (root / "DONE").write_text("finished\n", encoding="utf-8")
        # OPEN-64: `out` below the root is the coder's own work, and `out`
        # AT the root is build output. Both paths must say so.
        (root / "src" / "out").mkdir(parents=True, exist_ok=True)
        (root / "src" / "out" / "handler.py").write_text("def handle(): ...\n", encoding="utf-8")
        (root / "out").mkdir(exist_ok=True)
        (root / "out" / "bundle.js").write_text("// generated\n", encoding="utf-8")

    git_before = attempt_snapshot(context)
    write_the_same_project(context.project_path)
    with_git = changed_since(context, git_before)

    tree_before = attempt_snapshot(plain)
    write_the_same_project(plain.project_path)
    without_git = changed_since(plain, tree_before)

    assert with_git == without_git
    assert with_git == (
        "DONE",
        "README.md",
        "app.py",
        "requirements.txt",
        "src/out/handler.py",
    )


def test_build_output_is_still_pruned_from_the_wider_walk(no_repo: FakeContext):
    """Widening the suffix filter must not widen the directory pruning.

    `node_modules` holds non-source files by the thousand, and every one of
    them would otherwise be fingerprinted on every attempt.
    """
    before = attempt_snapshot(no_repo)
    build = no_repo.project_path / "node_modules" / "pkg"
    build.mkdir(parents=True)
    (build / "package.json").write_text("{}\n", encoding="utf-8")
    (build / "README.md").write_text("vendored\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ()


def test_rudra_s_own_directory_is_pruned_from_the_wider_walk(no_repo: FakeContext):
    """`.rudra/` is now full of files with no source suffix -- ledger.json,
    the transcripts, the debug log -- and every one of them changes during
    the attempt that would be reading them."""
    before = attempt_snapshot(no_repo)
    state = no_repo.project_path / ".rudra" / "run"
    state.mkdir(parents=True)
    (state / "ledger.json").write_text('{"tasks": []}\n', encoding="utf-8")
    (no_repo.project_path / "real.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_since(no_repo, before) == ("real.py",)


def test_out_below_the_root_is_the_coders_work_in_a_repo(context: FakeContext):
    """OPEN-64: the git path called `src/out/handler.py` build output.

    `_is_build_output` matched the five root-anchored names at any depth
    while the gate and the model's tree applied A1.29's split, so in a
    project that HAS a `.git` this file never reached `files_touched`,
    was never stub-scanned, and as an attempt's only write read as "the
    coder wrote nothing" -- three of those is BLOCKED. No test run ever
    had a `.git`, which is why it was never caught.
    """
    before = git_snapshot(context)
    handler = context.project_path / "src" / "out"
    handler.mkdir(parents=True)
    (handler / "handler.py").write_text("def handle(): ...\n", encoding="utf-8")
    dist = context.project_path / "packages" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")

    assert changed_since(context, before) == (
        "packages/web/dist/index.ts",
        "src/out/handler.py",
    )


def test_build_output_at_the_root_is_still_pruned_in_a_repo(context: FakeContext):
    """The other half, and the one a lazy fix breaks (A1.66).

    Turning `_is_build_output` into a no-op passes the test above and is
    wrong: a Rust `target/` arrives from `all_untracked=True` as thousands
    of files the coder did not write.
    """
    before = git_snapshot(context)
    for directory in ("out", "build", "dist", "target", "coverage"):
        built = context.project_path / directory
        built.mkdir()
        (built / "artifact.js").write_text("// generated\n", encoding="utf-8")
    (context.project_path / "real.py").write_text("x = 1\n", encoding="utf-8")

    assert changed_since(context, before) == ("real.py",)


def test_a_file_named_dist_at_the_root_is_reported_in_a_repo(context: FakeContext):
    """`parts[:-1]` -- the filename is never a directory name.

    A behaviour change, stated rather than smuggled. `filesystem/tree.py`
    still prunes a root file called `dist`, so the model is not shown a
    file the ledger records; that is a smaller member of this family and
    belongs in its own item, not this one.
    """
    before = git_snapshot(context)
    (context.project_path / "dist").write_text("the deliverable\n", encoding="utf-8")

    assert changed_since(context, before) == ("dist",)
