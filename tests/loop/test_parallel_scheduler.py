"""The optimistic worktree-parallel batch: run N tasks, merge serially, redo.

Everything external is INJECTED (fake run_task, fake git_ops, fake reroot,
fake after_merge_check, a make_worktree returning tmp dirs), so this suite
needs no real model, gate or git repository. Task 9 wires the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from rich.console import Console

from rudra.context.usage import RunUsage
from rudra.git.core import FileStatus, MergeResult
from rudra.loop.engine import LoopContext, Outcome
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.parallel import BatchResult, build_worktree_context, run_parallel_batch
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentContext

# --------------------------------------------------------------------------
# Stand-ins and fakes
# --------------------------------------------------------------------------


@dataclass
class FakeCfg:
    pass


class FakeGit:
    """Records every git op; merge/head answers are configurable per test."""

    def __init__(
        self,
        *,
        merge_results: dict[str, MergeResult] | None = None,
        commit_fail_ids: set[str] | None = None,
        clean_worktree_ids: set[str] | None = None,
    ) -> None:
        self.worktree_adds: list[tuple[Path, str | None, str]] = []
        self.worktree_removes: list[Path] = []
        self.commits: list[tuple[Path, str]] = []
        self.merges: list[str] = []
        self.merge_aborts: int = 0
        self.resets: list[str] = []
        self.heads: list[str] = []
        self.status_calls: list[Path] = []
        self._merge_results = merge_results or {}
        # Task ids whose worktree HAS work but whose commit_all fails (a repo
        # pre-commit hook rejection or a git error). Keyed by worktree name,
        # which make_worktree sets to the task id.
        self._commit_fail_ids = commit_fail_ids or set()
        # Task ids whose worktree `status` reports CLEAN (no changes). Anything
        # else reads as dirty, so the two commit_all==False cases are
        # distinguishable per worktree.
        self._clean_worktree_ids = clean_worktree_ids or set()
        self._head_counter = 0

    def worktree_add(
        self, project_path, worktree_path, *, gate, console, cfg, ref="HEAD", branch=None
    ) -> bool:
        self.worktree_adds.append((Path(worktree_path), branch, ref))
        # Real `git worktree add` creates the directory; mirror that so the
        # fake run_task can write a declared file into context.project_path.
        Path(worktree_path).mkdir(parents=True, exist_ok=True)
        return True

    def worktree_remove(
        self, project_path, worktree_path, *, gate, console, cfg, force=True
    ) -> bool:
        self.worktree_removes.append(Path(worktree_path))
        return True

    def commit_all(self, worktree_path, message, *, gate, console, cfg) -> bool:
        self.commits.append((Path(worktree_path), message))
        return Path(worktree_path).name not in self._commit_fail_ids

    def status(self, project_path, *, gate, console, cfg, all_untracked=False):
        self.status_calls.append(Path(project_path))
        name = Path(project_path).name
        if name in self._clean_worktree_ids:
            return []  # clean: nothing to commit
        return [FileStatus(index="A", worktree=" ", path=f"{name}.py")]  # dirty

    def merge(self, project_path, ref, *, gate, console, cfg) -> MergeResult:
        self.merges.append(ref)
        return self._merge_results.get(ref, MergeResult(ok=True, conflicted=False))

    def merge_abort(self, project_path, *, gate, console, cfg) -> bool:
        self.merge_aborts += 1
        return True

    def head(self, project_path, *, gate, console, cfg) -> str | None:
        self._head_counter += 1
        sha = f"sha{self._head_counter}"
        self.heads.append(sha)
        return sha

    def reset_hard(self, project_path, ref, *, gate, console, cfg) -> bool:
        self.resets.append(ref)
        return True


def make_run_task(recorder, *, stop_ids=(), block_ids=()):
    async def fake_run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
        recorder.append((task, context))
        # A real run_task leaves the coder's files in its worktree.
        (Path(context.project_path) / f"{task.id}.py").write_text("x\n", encoding="utf-8")
        if task.id in stop_ids:
            task.status = TaskStatus.PENDING
            return Outcome.STOP_RUN
        if task.id in block_ids:
            task.status = TaskStatus.BLOCKED
            return Outcome.BLOCKED
        task.status = TaskStatus.DONE
        return Outcome.DONE

    return fake_run_task


@dataclass
class Harness:
    base: LoopContext
    main: Path
    worktrees_root: Path
    recorder: list = field(default_factory=list)
    reroot_calls: list = field(default_factory=list)

    def make_worktree(self):
        def _mk(task: Task) -> Path:
            return self.worktrees_root / task.id

        return _mk

    def reroot(self):
        def _reroot(worktree_path: Path):
            b = ("backend", str(worktree_path))
            g = ("gate", str(worktree_path))
            self.reroot_calls.append((worktree_path, b, g))
            return b, g

        return _reroot


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    main = tmp_path / "main"
    main.mkdir()
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()

    console = Console(quiet=True)
    cfg = FakeCfg()
    limiter = object()
    usage = RunUsage()

    subagents = SubagentContext(
        project_path=main,
        backend=("backend", "main"),
        gate=("gate", "main"),
        console=console,
        cfg=cfg,
        session_id="sess",
        usage=usage,
        limiter=limiter,
    )
    base = LoopContext(
        subagents=subagents,
        project_path=main,
        console=console,
        cfg=cfg,
        paths=rudra_paths(main),
        usage=usage,
        limiter=limiter,
    )
    return Harness(base=base, main=main, worktrees_root=worktrees_root)


def frontier_of(*ids: str) -> list[Task]:
    return [Task(id=i, description=f"do {i}") for i in ids]


async def _run(harness: Harness, frontier, *, git, run_task, after_merge_check):
    ledger = Ledger(tasks=list(frontier))
    return await run_parallel_batch(
        frontier,
        context=harness.base,
        ledger=ledger,
        base_baseline=frozenset(),
        baseline_failed=False,
        make_worktree=harness.make_worktree(),
        reroot=harness.reroot(),
        run_task=run_task,
        git_ops=git,
        after_merge_check=after_merge_check,
        console=harness.base.console,
        cfg=harness.base.cfg,
    )


# --------------------------------------------------------------------------
# build_worktree_context contract (design §10.1)
# --------------------------------------------------------------------------


def test_build_worktree_context_contract(harness: Harness):
    wt = harness.worktrees_root / "t1"
    backend = ("backend", "wt")
    gate = ("gate", "wt")
    baseline = frozenset({"pkg.mod::test_x"})

    ctx = build_worktree_context(
        harness.base, wt, backend=backend, gate=gate, baseline=baseline, baseline_failed=True
    )

    assert ctx.project_path == wt
    # The real run's ledger + durable logs must NOT move into the worktree.
    assert ctx.paths is harness.base.paths
    assert ctx.limiter is harness.base.limiter
    assert ctx.usage is harness.base.usage
    assert ctx.failure_baseline == baseline
    assert ctx.baseline_failed is True
    # The rerooted backend/gate landed on the subagents copy, at the worktree.
    assert ctx.subagents.backend is backend
    assert ctx.subagents.gate is gate
    assert ctx.subagents.project_path == wt
    # A COPY, not the same object: mutating the worktree context's baseline
    # must not reach back into the real run's.
    assert ctx.subagents is not harness.base.subagents
    assert harness.base.project_path == harness.main


# --------------------------------------------------------------------------
# run_parallel_batch
# --------------------------------------------------------------------------


async def test_frontier_bounded(harness: Harness):
    frontier = frontier_of("t1", "t2", "t3")
    git = FakeGit()
    result = await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    assert isinstance(result, BatchResult)
    # Exactly one dispatch per frontier task.
    assert len(harness.recorder) == len(frontier)
    dispatched_ids = {t.id for t, _ in harness.recorder}
    assert dispatched_ids == {"t1", "t2", "t3"}
    # Each run_task saw its OWN worktree as project_path, and the real run's paths.
    for task, ctx in harness.recorder:
        assert ctx.project_path == harness.worktrees_root / task.id
        assert ctx.paths is harness.base.paths


async def test_disjoint_tasks_both_merge(harness: Harness):
    frontier = frontier_of("t1", "t2")
    git = FakeGit()
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    assert len(git.merges) == 2
    assert all(t.status is TaskStatus.DONE for t in frontier)
    assert git.merge_aborts == 0
    assert harness.base.usage.merge_conflicts == 0
    # Every worktree cleaned up.
    assert len(git.worktree_removes) == 2


async def test_collision_second_redone(harness: Harness):
    frontier = frontier_of("t1", "t2")
    # t2's branch is unique; look it up from the recorded adds after the run.
    git = FakeGit()
    # We cannot know the branch name up front (it carries a uuid), so fail
    # the SECOND merge whatever branch it is on.
    merge_calls: list[str] = []

    def merge(project_path, ref, *, gate, console, cfg):
        merge_calls.append(ref)
        git.merges.append(ref)
        if len(merge_calls) == 2:
            return MergeResult(ok=False, conflicted=True)
        return MergeResult(ok=True, conflicted=False)

    git.merge = merge  # type: ignore[method-assign]

    result = await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    t1, t2 = frontier[0], frontier[1]
    assert t1.status is TaskStatus.DONE
    assert t2.status is TaskStatus.PENDING
    assert git.merge_aborts == 1
    assert harness.base.usage.merge_conflicts == 1
    # The redo reason names the sibling it collided with.
    assert "t1" in t2.note
    assert "conflict" in t2.note.lower()
    assert len(git.worktree_removes) == 2
    assert result.stop is None


async def test_merge_not_ok_always_aborts(harness: Harness):
    """A non-conflict merge failure (conflicted=False) MUST still abort."""
    frontier = frontier_of("t1")
    git = FakeGit()

    def merge(project_path, ref, *, gate, console, cfg):
        git.merges.append(ref)
        return MergeResult(ok=False, conflicted=False)

    git.merge = merge  # type: ignore[method-assign]

    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    assert git.merge_aborts == 1
    assert frontier[0].status is TaskStatus.PENDING
    assert harness.base.usage.merge_conflicts == 1


async def test_integration_regression_resets(harness: Harness):
    frontier = frontier_of("t1")
    git = FakeGit()

    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: False,  # integration gate regressed
    )

    # reset_hard was called with the PRE-merge head (the first head read).
    assert git.resets == [git.heads[0]]
    assert frontier[0].status is TaskStatus.PENDING
    assert "regress" in frontier[0].note.lower()
    # A reset is not a conflict.
    assert harness.base.usage.merge_conflicts == 0
    assert git.merge_aborts == 0


async def test_stop_run_short_circuits(harness: Harness):
    frontier = frontier_of("t1", "t2", "t3")
    git = FakeGit()
    result = await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder, stop_ids={"t2"}),
        after_merge_check=lambda task: True,
    )

    assert result.stop is not None
    assert result.stop.id == "t2"
    # No merge happened at all.
    assert git.merges == []
    assert git.merge_aborts == 0
    # Every worktree created was removed.
    assert len(git.worktree_removes) == len(frontier)
    # Nothing was merged, so a sibling run_task left DONE is reset to PENDING:
    # its work lived only in the removed worktree. No task stays DONE.
    assert all(t.status is not TaskStatus.DONE for t in frontier)
    assert frontier[0].status is TaskStatus.PENDING


async def test_counters(harness: Harness):
    frontier = frontier_of("t1", "t2")
    git = FakeGit()
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    assert harness.base.usage.worktrees_created == len(frontier)
    log = harness.base.usage.as_log()
    assert log["run"]["parallel_tasks"] >= 2
    assert log["run"]["worktrees_created"] == 2
    assert log["run"]["merge_conflicts"] == 0


async def test_unique_branch_names_per_task(harness: Harness):
    """Branch names carry session, task id and a uuid so a REDO never collides."""
    frontier = frontier_of("t1", "t2")
    git = FakeGit()
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    branches = [branch for _, branch, _ in git.worktree_adds]
    assert len(branches) == 2
    assert len(set(branches)) == 2
    for branch in branches:
        assert branch is not None
        assert branch.startswith("rudra-wt/sess-")


async def test_non_done_task_not_merged(harness: Harness):
    """A BLOCKED task is left as-is and never merged, but its worktree is cleaned."""
    frontier = frontier_of("t1", "t2")
    git = FakeGit()
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder, block_ids={"t2"}),
        after_merge_check=lambda task: True,
    )

    # Only t1 (DONE) merged.
    assert len(git.merges) == 1
    assert frontier[1].status is TaskStatus.BLOCKED
    assert len(git.worktree_removes) == 2


async def test_commit_all_failure_redoes_not_discards(harness: Harness):
    """A DONE task whose commit fails goes PENDING -- never silently discarded.

    If commit_all fails while the worktree holds real work (a repo pre-commit
    hook rejection, a git error), the branch stays at base HEAD, merge would
    report "already up to date" (ok=True), after_merge_check would pass on a
    tree WITHOUT the work, and the task would stay DONE with its code discarded
    alongside the worktree. That is the silent-wrong-result the net excludes.
    """
    frontier = frontier_of("t1", "t2")
    git = FakeGit(commit_fail_ids={"t2"})
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    t1, t2 = frontier
    assert t1.status is TaskStatus.DONE
    assert t2.status is TaskStatus.PENDING  # redo, not DONE
    # t2's branch was NEVER merged -- no "already up to date" silent pass.
    t2_branch = next(b for _, b, _ in git.worktree_adds if b and b.startswith("rudra-wt/sess-t2-"))
    assert t2_branch not in git.merges
    assert len(git.merges) == 1  # only t1
    assert "could not be committed" in t2.note
    # An uncommittable worktree is not a merge conflict.
    assert harness.base.usage.merge_conflicts == 0
    assert git.merge_aborts == 0
    # Both worktrees still cleaned up.
    assert len(git.worktree_removes) == 2


async def test_empty_done_worktree_stays_done(harness: Harness):
    """A DONE task whose CLEAN worktree makes commit_all return False stays DONE.

    commit_all returns False both for a real commit failure (work present) AND
    for a clean worktree (nothing to commit). The latter is a legitimate no-op
    DONE -- the serial empty-diff / inherited-pass branch (OPEN-27), reachable
    when a redo batch forks from a base that already satisfies the task. It
    must NOT be forced to redo for ever.
    """
    frontier = frontier_of("t1", "t2")
    # t2: commit_all returns False AND its worktree is clean -> no-op DONE.
    git = FakeGit(commit_fail_ids={"t2"}, clean_worktree_ids={"t2"})
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    t1, t2 = frontier
    assert t1.status is TaskStatus.DONE
    assert t2.status is TaskStatus.DONE  # clean no-op stays DONE, never redone
    # Nothing to merge for t2 (its branch never merged); no abort/reset.
    t2_branch = next(b for _, b, _ in git.worktree_adds if b and b.startswith("rudra-wt/sess-t2-"))
    assert t2_branch not in git.merges
    assert len(git.merges) == 1  # only t1
    assert git.merge_aborts == 0
    assert git.resets == []
    assert harness.base.usage.merge_conflicts == 0
    # t2's status was consulted to make the distinction, and its worktree is
    # still cleaned up.
    assert (harness.worktrees_root / "t2") in git.status_calls
    assert len(git.worktree_removes) == 2


async def test_venv_symlinked_into_worktree(harness: Harness):
    """When main has a .venv, each worktree gets one resolving to it (§10.4)."""
    venv_python = harness.main / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")

    frontier = frontier_of("t1")
    git = FakeGit()
    await _run(
        harness,
        frontier,
        git=git,
        run_task=make_run_task(harness.recorder),
        after_merge_check=lambda task: True,
    )

    wt = harness.worktrees_root / "t1"
    assert (wt / ".venv" / "bin" / "python").exists()
