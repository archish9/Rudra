"""The agent's three ledger tools.

Typed arguments, so the model never emits JSON syntax. That is what lets
the ledger be JSON without reintroducing the parsing problem PLAN.md had:
the format the model writes and the format Rudra reads stopped being the
same artefact (C6.10).

There is deliberately no tool that sets DONE or BLOCKED. Only
loop/engine.py writes those, and DONE only when VerifyReport.passed --
which is how D9's "no LLM judge decides termination" is made structural
rather than a request in a prompt.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from rudra.loop.decomposition import (
    forbidden_shape_refusal,
    over_decomposition_refusal,
    single_file_evidence,
)
from rudra.loop.ledger import Ledger, Task, TaskStatus

logger = logging.getLogger(__name__)

# Statuses a task can no longer be dropped out of: the gate has already
# ruled, and letting the model retract that would rewrite history.
_SETTLED = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED})


def render_ledger(ledger: Ledger) -> str:
    """Every task, one line each: id, status, description.

    Public, and the ONLY spelling of the ledger a model is ever shown.
    `read_ledger` returns it when the planner asks, and consult_planner
    interpolates it into every re-consult so the planner does not have to
    ask (OPEN-65). Two renderers would drift, and the drift would land in a
    prompt -- the planner would be told the ledger in one spelling and shown
    it in another, which is the class of defect OPEN-64 was filed on.
    """
    if not ledger.tasks:
        return "The ledger has no tasks yet."
    return "\n".join(
        f"{task.id}  [{task.status.value}]  {task.description}" for task in ledger.tasks
    )


def _normalised(text: str) -> str:
    """The whole of the duplicate check: collapse whitespace, casefold.

    Deliberately NOT fuzzy, and deliberately not stemmed. A near-duplicate
    that is genuinely different work would be silently dropped, and the
    failure mode of a false positive here is *work that never happens* --
    strictly worse than the wasted turn a false negative costs. Run7's case
    was byte-identical three times over, so exact-after-normalisation
    catches it (OPEN-43).
    """
    return " ".join(text.split()).casefold()


def _duplicate_refusal(text: str, clash: Task) -> str:
    """Name the collision and a move that works, or this is OPEN-24 again.

    A model told only "no" re-issues the identical call. The refusal names
    the task, the status it is in, and the one action that will succeed.
    """
    quoted = text if len(text) <= 60 else f"{text[:57]}..."
    return (
        f'REJECTED: "{quoted}" duplicates {clash.id}, which is already '
        f"{clash.status.value}. That task exists and will not be worked "
        f"twice, so an identical add_tasks will be refused identically. If "
        f"work is genuinely still outstanding, add a task with a different "
        f"description saying what is still wrong."
    )


def _announce_refusal(trace: Any, usage: Any, offenders: int) -> None:
    """Say that Rudra refused a plan the model wrote (TODO.md lesson 5).

    A new guard is a new silence unless it is wired to something. The
    model is told by the tool result; the USER and the maintainer reading
    `.rudra/run/logs/` are told here -- a NOTICE in the debug log at every
    trace level, and a counter in usage.json.

    Swallows its own failure both times: a run that did its work must not
    be reported failed because a log line could not be written (§8a).
    """
    try:
        if usage is not None:
            usage.record_plan_refused("planner")
    except Exception:  # noqa: BLE001 - bookkeeping may never end a run
        logger.debug("refused plan not counted", exc_info=True)
    try:
        if trace is not None:
            trace.notice(
                f"refused a plan that split one file into {offenders} tasks; "
                f"the planner has been asked to combine them",
                role="planner",
                name="plan-shape",
            )
    except Exception:  # noqa: BLE001 - same rule
        logger.debug("refused plan not announced", exc_info=True)


def create_ledger_tools(
    ledger: Ledger,
    path: Path,
    facts: Any = None,
    *,
    trace: Any = None,
    usage: Any = None,
) -> list:
    """Tools bound to one run's ledger. Every mutation persists immediately.

    Args:
        ledger: The live Ledger the engine also reads. The same object, not
            a copy -- the agent and the loop must not diverge.
        path: Where to persist after each change.
        facts: The run's FactStore, or None. Read for one thing only: does
            anything the planner has settled say the deliverable is a
            single file (OPEN-90)? Duck-typed on `.items()` so this module
            keeps importing nothing from `rudra.facts`, and optional so
            every existing caller and test is unaffected -- without it the
            over-decomposition guard simply never fires.
        trace: The run's TraceSink, or None. A guard that acts on the
            user's behalf and reports nothing is the defect OPEN-35 ->
            OPEN-44 -> OPEN-45 hit three times.
        usage: The run's RunUsage, or None. Carries `plans_refused`.
    """

    def _fact_pairs() -> list[tuple[str, str]]:
        """(key, value) for every fact, or nothing if the store is unusable.

        Bookkeeping may never end a run (CLAUDE.md §8a): a guard that
        cannot read the facts declines to fire rather than raising into a
        tool call the model is waiting on.
        """
        if facts is None:
            return []
        try:
            return [(key, fact.value) for key, fact in facts.items()]
        except Exception:  # noqa: BLE001 -- a guard never ends a run (§8a)
            return []

    @tool
    def add_tasks(descriptions: list[str]) -> str:
        """Add tasks to the ledger.

        Each description is one unit of work, in plain language — not a
        filename. Good: "write a CSV parser that handles quoted commas".
        Also good: "write tests for the parser". Bad: "parser.py".

        Call this once with every task you can foresee. You can call it
        again later if you discover more.

        Args:
            descriptions: One entry per task.

        Returns:
            The ids assigned, so you can refer to them later.
        """
        wanted = [text.strip() for text in descriptions if text and text.strip()]
        if not wanted:
            return "REJECTED: give at least one non-empty task description."

        # OPEN-90, and it is all-or-nothing where the duplicate check below
        # is per-task: the defect is the SHAPE of the list -- several tasks
        # each owning a region of one file -- so there is no good half to
        # keep. Refused at most ONCE per run; the second list is accepted
        # whatever it says, because a run with no plan is worse than a run
        # with a wasteful one.
        if not ledger.decomposition_refused:
            refusal = over_decomposition_refusal(
                wanted,
                evidence=single_file_evidence(_fact_pairs()),
                existing=[
                    task.description
                    for task in ledger.tasks
                    if task.status is not TaskStatus.DROPPED
                ],
            )
            if refusal is not None:
                ledger.decomposition_refused = True
                _announce_refusal(trace, usage, sum(1 for text in wanted if text))
                return refusal

        # Every status, not only DONE. A duplicate of a PENDING task is
        # worked twice; a duplicate of a BLOCKED one is the case that ends
        # a run at MAX_BLOCKED_CONSULTS. DROPPED is the arguable exception
        # -- treated the same until a real run shows it blocking (OPEN-43).
        seen = {_normalised(task.description): task for task in ledger.tasks}
        added: list[Task] = []
        refused: list[str] = []
        for text in wanted:
            key = _normalised(text)
            clash = seen.get(key)
            if clash is not None:
                refused.append(_duplicate_refusal(text, clash))
                continue
            # The two shapes _BREAKDOWN_BODY already names as BAD and this
            # model emitted anyway (OPEN-90 §3.1). Per-task, like the
            # duplicate refusal: the rest of the batch is real work and
            # rejecting it wholesale would lose it.
            shape = forbidden_shape_refusal(text)
            if shape is not None:
                refused.append(shape)
                continue
            task = ledger.add(text)
            # Recorded before the next iteration, so two copies in ONE call
            # collide with each other and not only with the ledger.
            seen[key] = task
            added.append(task)

        if not added:
            return "\n".join(refused)
        # A batch carrying real work and one duplicate adds the work and
        # reports the duplicate: rejecting the whole call would lose the
        # rest, and the model has no way to retry only the good part.
        ledger.save(path)
        listed = ", ".join(f"{task.id} ({task.description})" for task in added)
        message = f"Added {len(added)} task(s): {listed}"
        return "\n".join([message, *refused]) if refused else message

    @tool
    def drop_task(task_id: str, reason: str) -> str:
        """Retract a task that should not be done after all.

        Use this when a task turned out to be unnecessary or wrong — not
        when it is merely hard. A dropped task is reported to the user
        along with your reason.

        Only a task that is still pending can be dropped. One that is
        already done, blocked or dropped has been settled and cannot be
        retracted -- to change course there, call add_tasks instead.

        Args:
            task_id: The id, e.g. "t2".
            reason: Why it should not be done. Required.

        Returns:
            Confirmation, or REJECTED and why.
        """
        if not reason.strip():
            return "REJECTED: dropping a task requires a reason."
        task = ledger.get(task_id)
        if task is None:
            known = ", ".join(item.id for item in ledger.tasks) or "none"
            return f"REJECTED: no task '{task_id}'. Known ids: {known}."
        if task.status in _SETTLED:
            # Name the move that works, or this is a dead end (OPEN-24). A
            # settled status is terminal -- nothing in engine.py moves a
            # task out of one -- so a model told only "no" re-issues the
            # identical call, which is what a planner did three times.
            return (
                f"REJECTED: {task_id} is already {task.status.value}. A settled "
                f"task stays settled and no more work will be spent on it, so an "
                f"identical drop_task will be refused identically. To take a "
                f"different approach, call add_tasks. drop_task only works on a "
                f"task that is still pending."
            )
        task.status = TaskStatus.DROPPED
        task.note = f"dropped: {reason.strip()}"
        ledger.save(path)
        return f"Dropped {task_id}."

    @tool
    def read_ledger() -> str:
        """Show every task and its current status.

        Returns:
            One line per task: id, status, description.
        """
        return render_ledger(ledger)

    return [add_tasks, drop_task, read_ledger]


__all__ = ["create_ledger_tools", "render_ledger"]
