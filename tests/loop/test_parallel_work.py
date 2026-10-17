"""The work()-level fork to the optimistic parallel driver (Task 9a).

Everything external is INJECTED (fake run_task, fake git_ops, fake planner,
fake reroot/make_worktree/run_main_gate), so this suite needs no real model,
gate, or git repository. Task 9b wires the real factories into
create_main_agent; until then every parallel field on LoopContext defaults to
None and work() runs the serial body byte-identically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

import rudra.loop.engine as engine
import rudra.loop.parallel as parallel
from rudra.context.usage import RunUsage
from rudra.git.core import MergeResult
from rudra.llm.concurrency import EndpointLimiter
from rudra.loop.engine import (
    LoopContext,
    Outcome,
    _should_parallelize,
    effective_fanout,
    work,
)
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.parallel import run_parallel_batch, run_parallel_work
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentContext

_CODER_URL = "http://vaayu:8000/v1"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTools:
    max_parallel_tasks: int = 0


@dataclass
class FakeModelCfg:
    base_url: str | None = _CODER_URL
    provider: str = "openai_compatible"


@dataclass
class FakeCfg:
    tools: FakeTools = field(default_factory=FakeTools)
    coder: FakeModelCfg = field(default_factory=FakeModelCfg)

    def model_for(self, role: str) -> FakeModelCfg:
        return self.coder


class FakeGit:
    """Records git ops; merge results configurable per branch order."""

    def __init__(self, *, fail_second_merge: bool = False, fail_all_merges: bool = False) -> None:
        self.worktree_adds: list[tuple[Path, str | None]] = []
        self.worktree_removes: list[Path] = []
        self.commits: list[Path] = []
        self.merges: list[str] = []
        self.merge_aborts = 0
        self.resets: list[str] = []
        self._fail_second_merge = fail_second_merge
        self._fail_all_merges = fail_all_merges
        self._head = 0

    def worktree_add(
        self, project_path, worktree_path, *, gate, console, cfg, ref="HEAD", branch=None
    ):
        self.worktree_adds.append((Path(worktree_path), branch))
        Path(worktree_path).mkdir(parents=True, exist_ok=True)
        return True

    def worktree_remove(self, project_path, worktree_path, *, gate, console, cfg, force=True):
        self.worktree_removes.append(Path(worktree_path))
        return True

    def commit_all(self, worktree_path, message, *, gate, console, cfg):
        self.commits.append(Path(worktree_path))
        return True

    def status(self, project_path, *, gate, console, cfg, all_untracked=False):
        from rudra.git.core import FileStatus

        return [FileStatus(index="A", worktree=" ", path="x.py")]

    def merge(self, project_path, ref, *, gate, console, cfg):
        self.merges.append(ref)
        if self._fail_all_merges or (self._fail_second_merge and len(self.merges) == 2):
            return MergeResult(ok=False, conflicted=True)
        return MergeResult(ok=True, conflicted=False)

    def merge_abort(self, project_path, *, gate, console, cfg):
        self.merge_aborts += 1
        return True

    def head(self, project_path, *, gate, console, cfg):
        self._head += 1
        return f"sha{self._head}"

    def reset_hard(self, project_path, ref, *, gate, console, cfg):
        self.resets.append(ref)
        return True


def make_run_task(recorder, *, stop_ids=(), block_ids=(), block_all=False):
    async def fake_run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
        recorder.append(task.id)
        (Path(context.project_path) / f"{task.id}.py").write_text("x\n", encoding="utf-8")
        if task.id in stop_ids:
            task.status = TaskStatus.PENDING
            task.note = f"stop because of {task.id}"
            return Outcome.STOP_RUN
        if block_all or task.id in block_ids:
            task.status = TaskStatus.BLOCKED
            return Outcome.BLOCKED
        task.status = TaskStatus.DONE
        return Outcome.DONE

    return fake_run_task


class FakePlanner:
    """Records consults; optionally adds one task per blocked consult."""

    def __init__(self, *, add_on_blocked=False) -> None:
        self.calls: list[dict] = []
        self._add_on_blocked = add_on_blocked
        self._added = 0

    async def __call__(self, ledger, request, *, stage, reason, feedback=None, task=None):
        self.calls.append({"stage": stage, "reason": reason, "task": task})
        if reason == "blocked" and self._add_on_blocked:
            self._added += 1
            ledger.add(f"fix for {task.id if task else '?'} #{self._added}")

    def blocked_consults(self) -> int:
        return sum(1 for c in self.calls if c["reason"] == "blocked")


def build_context(
    tmp_path: Path,
    *,
    limiter=None,
    cfg=None,
    reroot=None,
    make_worktree=None,
    run_main_gate=None,
) -> LoopContext:
    main = tmp_path / "main"
    main.mkdir(exist_ok=True)
    console = Console(quiet=True)
    cfg = cfg or FakeCfg()
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
    return LoopContext(
        subagents=subagents,
        project_path=main,
        console=console,
        cfg=cfg,
        paths=rudra_paths(main),
        usage=usage,
        limiter=limiter,
        reroot=reroot,
        make_worktree=make_worktree,
        run_main_gate=run_main_gate,
    )


def make_worktree_for(tmp_path: Path):
    root = tmp_path / "wt"
    root.mkdir(exist_ok=True)

    def _mk(task: Task) -> Path:
        return root / task.id

    return _mk


def reroot_fn(worktree_path: Path):
    return ("backend", str(worktree_path)), ("gate", str(worktree_path))


# --------------------------------------------------------------------------
# effective_fanout
# --------------------------------------------------------------------------


def test_effective_fanout(tmp_path: Path):
    limiter = EndpointLimiter({_CODER_URL: 3})

    # mpt 0 honors the server's cap exactly.
    ctx = build_context(tmp_path, limiter=limiter, cfg=FakeCfg(tools=FakeTools(0)))
    assert effective_fanout(ctx) == 3

    # mpt 2 clamps 3 down to 2.
    ctx = build_context(tmp_path, limiter=limiter, cfg=FakeCfg(tools=FakeTools(2)))
    assert effective_fanout(ctx) == 2

    # No limiter: serial.
    ctx = build_context(tmp_path, limiter=None, cfg=FakeCfg(tools=FakeTools(0)))
    assert effective_fanout(ctx) == 1

    # Cap 1 (endpoint not batch-capable): serial even at mpt 0.
    cap1 = EndpointLimiter({_CODER_URL: 1})
    ctx = build_context(tmp_path, limiter=cap1, cfg=FakeCfg(tools=FakeTools(0)))
    assert effective_fanout(ctx) == 1


# --------------------------------------------------------------------------
# _should_parallelize
# --------------------------------------------------------------------------


def test_should_parallelize_requires_clean_repo_and_factories(tmp_path: Path, monkeypatch):
    limiter = EndpointLimiter({_CODER_URL: 3})

    def ctx(**kw):
        return build_context(
            tmp_path,
            limiter=limiter,
            make_worktree=kw.get("make_worktree", make_worktree_for(tmp_path)),
            reroot=kw.get("reroot", reroot_fn),
            run_main_gate=kw.get("run_main_gate", lambda: frozenset()),
        )

    def patch(is_repo: bool, is_clean: bool):
        monkeypatch.setattr(engine, "is_repo", lambda *a, **k: is_repo)
        monkeypatch.setattr(engine, "is_clean", lambda *a, **k: is_clean)

    # All conditions hold.
    patch(True, True)
    assert _should_parallelize(ctx()) is True

    # Not a repo.
    patch(False, True)
    assert _should_parallelize(ctx()) is False

    # Dirty working tree.
    patch(True, False)
    assert _should_parallelize(ctx()) is False

    # Fanout of 1 (no limiter): serial regardless of the rest.
    patch(True, True)
    serial = build_context(
        tmp_path,
        limiter=None,
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    assert _should_parallelize(serial) is False

    # Any factory missing: serial.
    patch(True, True)
    assert _should_parallelize(ctx(reroot=None)) is False
    assert _should_parallelize(ctx(make_worktree=None)) is False
    assert _should_parallelize(ctx(run_main_gate=None)) is False


# --------------------------------------------------------------------------
# work() forks
# --------------------------------------------------------------------------


async def test_work_forks_when_should_parallelize(tmp_path: Path, monkeypatch):
    sentinel = object()
    spy_calls: list = []

    async def spy(request, *, context, planner, ledger):
        spy_calls.append(request)
        return sentinel

    monkeypatch.setattr(parallel, "run_parallel_work", spy)

    async def noop_sync(context):
        return None

    monkeypatch.setattr(engine, "_sync_project_env", noop_sync)

    async def noop_planner(ledger, request, *, stage, reason, feedback=None, task=None):
        return None

    limiter = EndpointLimiter({_CODER_URL: 3})
    monkeypatch.setattr(engine, "is_repo", lambda *a, **k: True)
    monkeypatch.setattr(engine, "is_clean", lambda *a, **k: True)

    # Wired + batch-capable endpoint: work() delegates to the parallel driver.
    ctx = build_context(
        tmp_path,
        limiter=limiter,
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    result = await work("build it", context=ctx, planner=noop_planner, ledger=Ledger())
    assert result is sentinel
    assert spy_calls == ["build it"]

    # Cap 1: no fork, the serial body runs and returns a real summary.
    spy_calls.clear()
    cap1 = EndpointLimiter({_CODER_URL: 1})
    serial_ctx = build_context(
        tmp_path,
        limiter=cap1,
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    result = await work("build it", context=serial_ctx, planner=noop_planner, ledger=Ledger())
    assert spy_calls == []
    assert result.message  # an AgentResult from summarise

    # Unwired (factories None) even at fanout 3: no fork.
    spy_calls.clear()
    unwired = build_context(tmp_path, limiter=limiter)
    result = await work("build it", context=unwired, planner=noop_planner, ledger=Ledger())
    assert spy_calls == []
    assert result.message


# --------------------------------------------------------------------------
# run_parallel_work
# --------------------------------------------------------------------------


def patch_tail(monkeypatch):
    """Stub the model-calling tail so run_parallel_work is unit-testable."""
    review_calls: list = []
    summ_calls: list = []

    async def fake_review(context, ledger):
        review_calls.append(True)

    async def fake_summ(context, ledger):
        summ_calls.append(True)

    async def noop_sync(context):
        return None

    monkeypatch.setattr(engine, "review_once", fake_review)
    monkeypatch.setattr(engine, "summarise_architecture", fake_summ)
    monkeypatch.setattr(engine, "_sync_project_env", noop_sync)
    return review_calls, summ_calls


async def test_parallel_work_two_done_finish(tmp_path: Path, monkeypatch):
    review_calls, summ_calls = patch_tail(monkeypatch)
    ctx = build_context(
        tmp_path,
        limiter=EndpointLimiter({_CODER_URL: 3}),
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    ledger = Ledger(tasks=[Task(id="t1", description="a"), Task(id="t2", description="b")])
    git = FakeGit()
    recorder: list = []
    planner = FakePlanner()

    result = await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task(recorder),
        git_ops=git,
    )

    assert sorted(recorder) == ["t1", "t2"]
    assert all(t.status is TaskStatus.DONE for t in ledger.tasks)
    assert len(git.merges) == 2
    assert len(git.worktree_removes) == 2
    assert review_calls == [True]
    assert summ_calls == [True]
    assert result.message
    # The ledger was persisted.
    assert ctx.paths.ledger_json.exists()


async def test_parallel_work_stop_run_stops(tmp_path: Path, monkeypatch):
    patch_tail(monkeypatch)
    ctx = build_context(
        tmp_path,
        limiter=EndpointLimiter({_CODER_URL: 3}),
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    ledger = Ledger(
        tasks=[
            Task(id="t1", description="a"),
            Task(id="t2", description="b"),
            Task(id="t3", description="c"),
        ]
    )
    git = FakeGit()
    recorder: list = []
    planner = FakePlanner()

    result = await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task(recorder, stop_ids={"t2"}),
        git_ops=git,
    )

    # One batch ran; the run stopped, so nothing was re-dispatched.
    assert sorted(recorder) == ["t1", "t2", "t3"]
    assert git.merges == []
    assert planner.blocked_consults() == 0
    assert result.message


async def test_parallel_work_blocked_consults_planner(tmp_path: Path, monkeypatch):
    patch_tail(monkeypatch)
    ctx = build_context(
        tmp_path,
        limiter=EndpointLimiter({_CODER_URL: 3}),
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )

    # One blocked task triggers exactly one blocked consult.
    ledger = Ledger(tasks=[Task(id="t1", description="a")])
    git = FakeGit()
    planner = FakePlanner()
    await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task([], block_ids={"t1"}),
        git_ops=git,
    )
    assert planner.blocked_consults() == 1

    # A planner that keeps adding blockers is bounded by MAX_BLOCKED_CONSULTS.
    ledger = Ledger(tasks=[Task(id="t1", description="a")])
    planner = FakePlanner(add_on_blocked=True)
    await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task([], block_all=True),
        git_ops=FakeGit(),
    )
    assert planner.blocked_consults() == engine.MAX_BLOCKED_CONSULTS


# --------------------------------------------------------------------------
# Durable redo diagnostics
# --------------------------------------------------------------------------


async def test_redo_reasons_durable(tmp_path: Path):
    """A redone task's reason lands in redo_reasons and survives a note rewrite."""
    ctx = build_context(tmp_path, limiter=EndpointLimiter({_CODER_URL: 3}))
    frontier = [Task(id="t1", description="a"), Task(id="t2", description="b")]
    ledger = Ledger(tasks=list(frontier))
    git = FakeGit(fail_second_merge=True)

    result = await run_parallel_batch(
        frontier,
        context=ctx,
        ledger=ledger,
        base_baseline=frozenset(),
        baseline_failed=False,
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_task=make_run_task([]),
        git_ops=git,
        after_merge_check=lambda task: True,
        console=ctx.console,
        cfg=ctx.cfg,
    )

    t2 = frontier[1]
    assert t2.status is TaskStatus.PENDING
    assert t2.redo_reasons  # non-empty
    assert any("conflict" in r.lower() for r in t2.redo_reasons)
    assert t2.worktree  # names where it ran
    assert result.stop is None

    # run_task on the redo overwrites note; redo_reasons must still carry it.
    original = t2.redo_reasons
    t2.note = "fresh attempt text"
    assert t2.redo_reasons == original


async def test_parallel_work_redo_cap_blocks(tmp_path: Path, monkeypatch):
    """A task that keeps conflicting is BLOCKED at MAX_PARALLEL_REDOS, not cycled."""
    review_calls, summ_calls = patch_tail(monkeypatch)
    ctx = build_context(
        tmp_path,
        limiter=EndpointLimiter({_CODER_URL: 3}),
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: frozenset(),
    )
    ledger = Ledger(tasks=[Task(id="t1", description="a")])
    git = FakeGit(fail_all_merges=True)
    recorder: list = []
    planner = FakePlanner()

    await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task(recorder),
        git_ops=git,
    )

    t1 = ledger.tasks[0]
    # It left the frontier as BLOCKED, with exactly the cap's worth of reasons.
    assert t1.status is TaskStatus.BLOCKED
    assert len(t1.redo_reasons) == parallel.MAX_PARALLEL_REDOS
    assert any("converge" in r.lower() for r in t1.redo_reasons[-1:]) or t1.note
    # It ran exactly MAX_PARALLEL_REDOS batches, then stopped cycling.
    assert recorder.count("t1") == parallel.MAX_PARALLEL_REDOS
    # And then it routed to the planner exactly once (BLOCKED handling).
    assert planner.blocked_consults() == 1


async def test_parallel_work_seeds_stale_baseline(tmp_path: Path, monkeypatch):
    """context.failure_baseline is seeded from run_main_gate each batch."""
    patch_tail(monkeypatch)
    red = frozenset({"pkg::test_x"})
    ctx = build_context(
        tmp_path,
        limiter=EndpointLimiter({_CODER_URL: 3}),
        make_worktree=make_worktree_for(tmp_path),
        reroot=reroot_fn,
        run_main_gate=lambda: red,
    )
    ledger = Ledger(tasks=[Task(id="t1", description="a")])
    git = FakeGit()
    planner = FakePlanner()

    await run_parallel_work(
        "do it",
        context=ctx,
        planner=planner,
        ledger=ledger,
        run_task=make_run_task([]),
        git_ops=git,
    )

    # The main tree's red gate is now the stale-failure baseline.
    assert ctx.failure_baseline == red
    assert ctx.baseline_failed is True
    # A red main with a drained ledger consulted the stale-failure stage.
    assert any(c["reason"] == "stale_failures" for c in planner.calls)


def test_task_redo_reasons_and_worktree_roundtrip(tmp_path: Path):
    path = tmp_path / "ledger.json"
    led = Ledger(
        tasks=[
            Task(
                id="t1",
                description="a",
                redo_reasons=("redo: merge conflict", "redo: regressed"),
                worktree="/tmp/wt/t1",
            )
        ]
    )
    led.save(path)
    loaded = Ledger.load(path)
    assert loaded.tasks[0].redo_reasons == ("redo: merge conflict", "redo: regressed")
    assert loaded.tasks[0].worktree == "/tmp/wt/t1"

    # An older ledger with neither key loads with defaults.
    import json

    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "a", "status": "pending"}]}),
        encoding="utf-8",
    )
    legacy = Ledger.load(path)
    assert legacy.tasks[0].redo_reasons == ()
    assert legacy.tasks[0].worktree == ""
