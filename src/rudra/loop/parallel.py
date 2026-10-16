"""Optimistic worktree-parallel batching for the loop (Vaayu fan-out, Layer 1).

The serial loop (`loop/engine.py::work` -> `run_task`) works one PENDING task
at a time. When a batch-configured endpoint says a client may fan out
(`recommended_client_concurrency` > 1), this module runs up to N independent
PENDING tasks concurrently -- each `run_task` in its OWN git worktree -- then
merges them back to the main tree SERIALLY, sending any conflicting or
regressing task back to PENDING to redo.

**Correctness comes from the merge + check, never from a task-independence
model** (design §2, D3). Two tasks that touch the same file diverge in their
worktrees; the second merge conflicts and that task redoes against the merged
tree. A task built against incomplete code fails its own gate or regresses the
integration gate and redoes. A wrong guess costs a redo, not a wrong result.

Everything external is INJECTED so this is unit-testable with fakes:

- `make_worktree(task) -> Path`     -- where this task's checkout goes
- `reroot(worktree_path) -> (backend, gate)` -- a worktree-rooted backend+gate
- `run_task`  (defaults to `loop.engine.run_task`)
- `git_ops`   (defaults to `rudra.git.core`)
- `after_merge_check(task) -> bool` -- True keeps the merge, False = regressed

Task 9 provides the real `reroot`, the real `after_merge_check` (a verify-based
integration gate) and the `work()`-level loop that calls this. `engine.py`'s
serial path stays byte-identical: it does not import this module.

The single global path normalizer (`main_agent.py::install_path_normalizer`)
is left as-is: `strip_project_root` yields a relative path that each
per-worktree backend resolves against its own root (design §10.1).
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.git import core as _git_core
from rudra.loop.engine import LoopContext, Outcome
from rudra.loop.engine import run_task as _engine_run_task
from rudra.loop.ledger import Task, TaskStatus

# Format: rudra-wt/<session>-<task id>-<short uuid>. The uuid is a HARD
# requirement (Task 7 review): a REDO of the same task id in a later batch
# must not collide with the branch its earlier attempt left on
# `git worktree add -b`.
_WORKTREE_BRANCH = "rudra-wt/{session}-{task}-{nonce}"


@dataclass(frozen=True)
class BatchResult:
    """What one parallel batch produced, reduced in ascending id order.

    `results` pairs each frontier task with the `Outcome` its `run_task`
    returned. The `Task` objects are mutated in place during merge-back, so a
    task sent back to redo carries `status == PENDING` even though its
    `run_task` outcome was `DONE` -- `task.status` is authoritative, the
    outcome is informational. `stop` is the STOP_RUN task when one occurred
    (the batch short-circuited before any merge); otherwise None.
    """

    results: tuple[tuple[Task, Outcome], ...]
    stop: Task | None = None


@dataclass
class _Handle:
    """One frontier task's worktree, branch and eventual outcome."""

    task: Task
    path: Path
    branch: str
    dispatched: bool = False
    outcome: Outcome | None = None
    removed: bool = False


def _usage(context: LoopContext) -> Any:
    """The run's usage tally, or None -- optional, like everywhere else."""
    return getattr(context, "usage", None)


_ID_TAIL = re.compile(r"^(.*?)(\d+)$")


def _id_sort_key(handle: _Handle) -> tuple[str, int]:
    """Sort handles by their task's numeric tail so t2 precedes t10.

    Ids are `t1`, `t2`, ... (`ledger.py::Ledger.add`); a lexical sort would
    order t10 before t2 and break the ascending-id reduce the design pins.
    """
    task_id = handle.task.id
    match = _ID_TAIL.match(task_id)
    if match:
        return (match.group(1), int(match.group(2)))
    return (task_id, 0)


def _append_note(task: Task, text: str) -> None:
    """Append a redo reason to `task.note` (design §8, append-only intent).

    Appended rather than overwritten so a note `run_task` already wrote (why
    the task first reached DONE) survives beside the reason it is being redone.
    """
    task.note = f"{task.note}\n{text}".strip() if task.note else text


def _redo(handle: _Handle, text: str) -> None:
    """Send a task back to PENDING and record WHY, durably (CLAUDE.md §8a shape 4).

    `note` carries the reason for the model that reads the next blocker, but
    `run_task` OVERWRITES `note` on the redo -- so the reason would vanish
    exactly when the task later succeeds. `redo_reasons` is append-only state
    beside the note for that reason, and `worktree` names where the discarded
    attempt ran so a reader can find its lost work.
    """
    task = handle.task
    task.status = TaskStatus.PENDING
    _append_note(task, text)
    task.redo_reasons = (*task.redo_reasons, text)
    task.worktree = str(handle.path)


def build_worktree_context(
    base: LoopContext,
    worktree_path: Path,
    *,
    backend: Any,
    gate: Any,
    baseline: frozenset[str],
    baseline_failed: bool,
) -> LoopContext:
    """A COPY of `base` rooted at `worktree_path` (design §10.1).

    Changed: `project_path`, the subagents' `backend`/`gate`/`project_path`
    (the rerooted ones the caller built -- Task 9 owns the rooting), and the
    two baseline fields. Every worktree in a batch forks from the same HEAD,
    so they share ONE base-commit baseline captured once.

    Kept SHARED by reference, deliberately:
    - `paths` -- the single ledger and the durable logs must NOT move into a
      worktree, which is cleaned away with its `.rudra/` (design §10.1).
    - `limiter`, `usage`, `memory`, `console`, `cfg` -- one run, one of each.

    The backend/gate are passed in, never rebuilt here (that is Task 9's job).
    `dataclasses.replace` yields a fresh LoopContext/SubagentContext, so a
    worktree run mutating its own `failure_baseline`/`run_errors` cannot reach
    back into the real run's or a sibling's.
    """
    subagents = replace(
        base.subagents,
        backend=backend,
        gate=gate,
        project_path=worktree_path,
    )
    return replace(
        base,
        subagents=subagents,
        project_path=worktree_path,
        failure_baseline=baseline,
        baseline_failed=baseline_failed,
    )


def _share_main_venv(main: Path, worktree_path: Path) -> None:
    """Point `<worktree>/.venv` at `<main>/.venv` so the gate reuses it (§10.4).

    `_sync_project_env` builds `.venv` once in the MAIN tree at work() start;
    re-syncing per worktree would mean N parallel installs. A symlink lets the
    gate's `.venv/bin/python -m pytest` (resolved relative to the worktree)
    run the shared env.

    Capability, not platform: attempt the symlink; on ANY OSError (an
    unprivileged Windows box, a filesystem that forbids it, the link already
    existing) leave it absent -- the worktree gate then uses the machine
    interpreter, exactly as a no-`.venv` project does today. NEVER raises.
    """
    main_venv = main / ".venv"
    if not main_venv.exists():
        return
    try:
        os.symlink(main_venv, worktree_path / ".venv")
    except OSError:
        pass


async def run_parallel_batch(
    frontier: list[Task],
    *,
    context: LoopContext,
    ledger: Any,
    base_baseline: frozenset[str],
    baseline_failed: bool,
    make_worktree: Any,
    reroot: Any,
    run_task: Any = _engine_run_task,
    git_ops: Any = _git_core,
    after_merge_check: Any,
    console: Console,
    cfg: Any,
) -> BatchResult:
    """Run `frontier` concurrently in worktrees, then merge back serially.

    See the module docstring for the injected dependencies. `base_baseline`
    and `baseline_failed` are the ONE base-commit gate state every worktree in
    this batch forks from (design §6 base-baseline simplification).
    """
    main = context.project_path
    # Every op on the MAIN tree (worktree add/remove, merge, reset) runs under
    # the real run's gate -- the worktree contexts carry the rerooted gates,
    # but merge-back lands on the shared tree, so it uses the shared policy.
    main_gate = context.subagents.gate
    usage = _usage(context)
    session = getattr(context.subagents, "session_id", "") or ""

    # 1. One isolated checkout per task, each on a unique branch.
    handles: list[_Handle] = []
    for task in frontier:
        worktree_path = Path(make_worktree(task))
        branch = _WORKTREE_BRANCH.format(session=session, task=task.id, nonce=uuid.uuid4().hex[:8])
        added = git_ops.worktree_add(
            main, worktree_path, gate=main_gate, console=console, cfg=cfg, branch=branch
        )
        handle = _Handle(task=task, path=worktree_path, branch=branch)
        if not added:
            # Could not isolate this task; leave it PENDING for a later batch
            # rather than run it against the shared tree. No worktree to clean.
            task.run_errors = (*task.run_errors, f"could not create a worktree for {task.id}")
            handles.append(handle)
            continue
        handle.dispatched = True
        _share_main_venv(main, worktree_path)
        handles.append(handle)

    live = [h for h in handles if h.dispatched]

    # Recorded from the actually-live set AFTER the add loop, not len(frontier)
    # before it: a worktree_add that failed created nothing, and over-reporting
    # worktrees/peak would lie to usage.json (§8a -- a diagnostic must be true).
    if usage is not None:
        usage.record_worktrees_created(len(live))
        usage.record_parallel_batch(len(live))

    try:
        # 2. Dispatch every live task concurrently; join on the group's exit.
        async with asyncio.TaskGroup() as group:
            atasks: dict[str, Any] = {}
            for handle in live:
                backend, gate = reroot(handle.path)
                wt_context = build_worktree_context(
                    context,
                    handle.path,
                    backend=backend,
                    gate=gate,
                    baseline=base_baseline,
                    baseline_failed=baseline_failed,
                )
                atasks[handle.task.id] = group.create_task(
                    run_task(handle.task, ledger, context=wt_context)
                )
        for handle in live:
            handle.outcome = atasks[handle.task.id].result()

        ordered = sorted(handles, key=_id_sort_key)

        # 3. STOP_RUN from any task stops the run: merge nothing, clean all.
        stop = next(
            (h.task for h in ordered if h.outcome is Outcome.STOP_RUN),
            None,
        )
        if stop is not None:
            for handle in live:
                # Nothing merged, so a sibling run_task left DONE has its work
                # only in the worktree about to be removed -- leaving it DONE
                # would mark lost work complete. Back to PENDING to redo on a
                # resume, exactly as the merge+check invariant requires. A
                # BLOCKED task keeps its status: that is run_task's judgement,
                # which Task 9's reduce owns, not a side effect of the stop.
                if handle.task.status is TaskStatus.DONE:
                    handle.task.status = TaskStatus.PENDING
                _remove(git_ops, main, main_gate, handle, console, cfg)
            return BatchResult(
                results=tuple((h.task, _outcome(h)) for h in ordered),
                stop=stop,
            )

        # 4. Serial merge-back, ascending id. One owner mutates status.
        merged_ids: list[str] = []
        for handle in ordered:
            if handle.dispatched and handle.task.status is TaskStatus.DONE:
                _merge_back(
                    handle,
                    main=main,
                    main_gate=main_gate,
                    git_ops=git_ops,
                    after_merge_check=after_merge_check,
                    merged_ids=merged_ids,
                    usage=usage,
                    console=console,
                    cfg=cfg,
                )
            # A non-DONE task (BLOCKED / PENDING) keeps its status, unmerged.
            # Only a dispatched task has a worktree on disk to remove -- a
            # failed worktree_add created nothing, so skip it (as `finally`
            # does) rather than issue a spurious gated `git worktree remove`.
            if handle.dispatched:
                _remove(git_ops, main, main_gate, handle, console, cfg)

        return BatchResult(results=tuple((h.task, _outcome(h)) for h in ordered))
    finally:
        # Safety net: a CancelledError (Ctrl-C) or an unexpected raise skips
        # the cleanup above, so remove any worktree still on disk. Idempotent
        # -- a second remove of an already-gone worktree just returns False.
        for handle in handles:
            if handle.dispatched and not handle.removed:
                _remove(git_ops, main, main_gate, handle, console, cfg)


def _outcome(handle: _Handle) -> Outcome:
    """The outcome to report for a handle: run_task's, or DONE if it never ran.

    A task whose worktree could not be created never dispatched; it stays
    PENDING, which has no `Outcome`, so report DONE (the neutral "nothing to
    act on") and let the caller read `task.status`.
    """
    return handle.outcome if handle.outcome is not None else Outcome.DONE


def _remove(
    git_ops: Any, main: Path, main_gate: Any, handle: _Handle, console: Console, cfg: Any
) -> None:
    """Remove a worktree once; record that it is gone so `finally` skips it."""
    if handle.removed:
        return
    git_ops.worktree_remove(main, handle.path, gate=main_gate, console=console, cfg=cfg)
    handle.removed = True


def _merge_back(
    handle: _Handle,
    *,
    main: Path,
    main_gate: Any,
    git_ops: Any,
    after_merge_check: Any,
    merged_ids: list[str],
    usage: Any,
    console: Console,
    cfg: Any,
) -> None:
    """Commit the worktree, merge it, and keep or undo the result.

    A `commit_all` that returns False is read against the worktree's status:
    a CLEAN worktree is a legitimate no-op DONE (nothing to commit, nothing to
    merge -- the serial empty-diff / inherited-pass case, OPEN-27) and STAYS
    DONE; a DIRTY worktree means the commit genuinely failed (a repo pre-commit
    hook rejected real work, or git errored), which is a failed merge-back and
    returns the task to PENDING WITHOUT merging. The latter is the
    silent-wrong-result the net must exclude: an uncommitted branch stays at
    base HEAD, `merge` reports "already up to date" (ok=True),
    `after_merge_check` passes on a tree WITHOUT the work, and the task would
    stay DONE with its code discarded alongside the worktree.

    On a merge that is `not ok` -- conflict OR any other failure -- ALWAYS
    abort (Task 7 review: an unreadable status reports conflicted=False, and
    skipping the abort would leave a half-merged tree). On a clean merge whose
    integration gate regressed, reset the main tree to the pre-merge HEAD.
    Either way the task returns to PENDING to redo against the merged tree.
    """
    task = handle.task
    common = {"gate": main_gate, "console": console, "cfg": cfg}

    if not git_ops.commit_all(handle.path, f"rudra task {task.id}", **common):
        # commit_all returns False for TWO different situations (git/core.py):
        # a CLEAN worktree (nothing to commit) and a real commit failure (work
        # present, commit rejected -- a repo pre-commit hook, a git error).
        # Distinguish by the worktree's own status, so a legitimate no-op DONE
        # -- the serial loop's empty-diff / inherited-pass branch (OPEN-27),
        # reachable here when a redo batch forks from a base that already
        # satisfies the task -- is not forced to redo for ever.
        entries = git_ops.status(handle.path, **common)
        if entries is not None and not entries:
            # Clean: nothing was there to commit, nothing to merge. A no-op
            # DONE, exactly like the serial empty-diff DONE. Stays DONE.
            return
        # Dirty, or status unreadable (None): the commit genuinely failed, or
        # we cannot prove the worktree clean -- redo rather than risk the
        # silent discard round 1 closed.
        _redo(
            handle,
            f"redo: the work for {task.id} could not be committed in its "
            f"worktree; re-run against the merged tree",
        )
        return

    pre = git_ops.head(main, **common)
    result = git_ops.merge(main, handle.branch, **common)

    if not result.ok:
        git_ops.merge_abort(main, **common)
        if usage is not None:
            usage.record_merge_conflict()
        siblings = ", ".join(merged_ids) if merged_ids else "an earlier sibling in this batch"
        _redo(
            handle,
            f"redo: merging this task conflicted with {siblings}; re-run against the merged tree",
        )
        return

    if not after_merge_check(task):
        git_ops.reset_hard(main, pre, **common)
        _redo(
            handle,
            f"redo: the integration gate regressed after merging {task.id}; "
            f"reset the main tree to {pre}; re-run against the merged tree",
        )
        return

    # Clean merge, integration gate green: the task stays DONE.
    merged_ids.append(task.id)


def _no_regression(now: frozenset[str], base: frozenset[str]) -> bool:
    """Did the integration gate NOT regress after a merge? (design §6)

    `now <= base`: the merged tree fails no key the base did not already
    fail. Any exception -> True, which KEEPS the merge: reverting good work
    over a flaky integration gate is worse than keeping a merge that might
    have regressed, and the next task's own gate still catches a real break.
    """
    try:
        return now <= base
    except Exception:  # noqa: BLE001 -- a flaky gate must not revert good work
        return True


def _make_after_merge_check(run_main_gate: Any, base: frozenset[str]) -> Any:
    """An after_merge_check bound to THIS batch's base gate state.

    A factory, not a loop-body lambda, so `base` is bound now rather than read
    late, and run_parallel_work stays free of ruff's E731/B023.
    """

    def check(task: Task) -> bool:
        return _no_regression(run_main_gate(), base)

    return check


async def run_parallel_work(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Any,
    run_task: Any = None,
    git_ops: Any = None,
) -> Any:
    """The work()-level driver for the optimistic parallel path (Task 9a).

    Mirrors loop/engine.py::work EXACTLY -- the empty-ledger / stale-failure
    consult, the STOP_RUN stop, the BLOCKED MAX_BLOCKED_CONSULTS bound, the
    Ctrl-C cancel, and the review/summary tail -- but works a FRONTIER of up
    to effective_fanout PENDING tasks per iteration through run_parallel_batch
    rather than one task through run_task. Returns exactly what work() returns.

    `run_task` and `git_ops` default to the engine's and rudra.git.core, and
    are injected so this is unit-testable with fakes. The engine helpers are
    imported lazily, both to break the engine<->parallel import cycle (engine
    imports THIS module only inside work()) and so a test that monkeypatches
    engine.review_once / engine._sync_project_env is honoured at call time.
    """
    from rudra.loop.engine import (
        MAX_BLOCKED_CONSULTS,
        _stale_failure_list,
        _sync_project_env,
        effective_fanout,
        notice_if_suspended,
        review_once,
        summarise,
        summarise_architecture,
        write_usage_log,
    )
    from rudra.loop.engine import run_task as engine_run_task

    if run_task is None:
        run_task = engine_run_task
    if git_ops is None:
        git_ops = _git_core

    await _sync_project_env(context)
    consulted_on_empty = False
    consulted_on_stale = False
    blocked_consults = 0
    cancelled = False

    while True:
        frontier = list(ledger.resumable())[: effective_fanout(context)]
        if not frontier:
            # Mirror work(): a stale red suite with an empty ledger is asked
            # about before "is anything missing?", and either consult's task
            # is worked on the next turn of this loop.
            if context.failure_baseline and not consulted_on_stale:
                consulted_on_stale = True
                await planner(
                    ledger,
                    request,
                    stage="breakdown",
                    reason="stale_failures",
                    feedback=_stale_failure_list(context.failure_baseline),
                )
                continue
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
            continue

        # The ONE base-commit gate state this batch forks from and merges
        # against (design §6). Read once here; after_merge_check re-reads it
        # after each merge to catch a regression against this same base.
        base = context.run_main_gate()
        after_merge_check = _make_after_merge_check(context.run_main_gate, base)

        try:
            result = await run_parallel_batch(
                frontier,
                context=context,
                ledger=ledger,
                base_baseline=base,
                baseline_failed=bool(base),
                make_worktree=context.make_worktree,
                reroot=context.reroot,
                run_task=run_task,
                git_ops=git_ops,
                after_merge_check=after_merge_check,
                console=context.console,
                cfg=context.cfg,
            )
        except asyncio.CancelledError:
            # A cooperative stop (Ctrl-C), mirroring work(). The batch's own
            # `finally` has already removed every worktree; each unfinished
            # frontier task goes back to PENDING so Ledger.resumable() (which
            # excludes IN_PROGRESS) picks it up on --continue.
            for task in frontier:
                if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                    task.status = TaskStatus.PENDING
                    task.note = "cancelled by the user"
            ledger.save(context.paths.ledger_json)
            cancelled = True
            break

        ledger.save(context.paths.ledger_json)

        if result.stop is not None:
            context.console.print(
                f"\n[bold red]Run stopped early.[/bold red] [dim]{result.stop.note}[/dim]"
            )
            break

        # Every BLOCKED task consults the planner, in ascending id order (the
        # order BatchResult.results is reduced in), all under the one bound.
        stop_run = False
        for task, _ in result.results:
            if task.status is not TaskStatus.BLOCKED:
                continue
            blocked_consults += 1
            if blocked_consults > MAX_BLOCKED_CONSULTS:
                context.console.print(
                    f"\n[bold red]Stopping.[/bold red] [dim]{blocked_consults - 1} tasks "
                    f"blocked and re-planning is not making progress.[/dim]"
                )
                stop_run = True
                break
            consulted_on_empty = False
            # What this consult adds is worked next, not after every pending
            # task the block may be starving (OPEN-158), exactly as work() does.
            with ledger.unblocking(task.id):
                await planner(ledger, request, stage="breakdown", reason="blocked", task=task)
        if stop_run:
            break

    run_usage = getattr(context, "usage", None)

    # Both are model calls; a user who just pressed Ctrl-C is not waiting
    # through two more inferences. The usage log is still written -- a
    # cancelled run still cost tokens and wall clock. (Mirrors work()'s tail.)
    if not cancelled:
        if any(task.status is TaskStatus.DONE for task in ledger.tasks):
            await review_once(context, ledger)
        await summarise_architecture(context, ledger)

    notice_if_suspended(getattr(context.subagents, "trace", None), run_usage)
    write_usage_log(context.paths.logs / "usage.json", run_usage)
    return summarise(ledger, context.console, run_usage, cancelled=cancelled)


__all__ = [
    "BatchResult",
    "build_worktree_context",
    "run_parallel_batch",
    "run_parallel_work",
]
