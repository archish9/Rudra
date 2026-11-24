"""In-process decision gates that shortcut a Vaayu call when laya is confident.

Each gate returns a plain bool/choice the caller acts on, and NEVER changes
behaviour when laya is unavailable or unsure: the fallback is always today's
existing path (master spec §3). Distinct from ``decide/gate.py``, which is the
run-entry install requirement (OPEN-180).

#2 clarify gating (OPEN-176): skip the planner's clarify stage -- and its Vaayu
round-trip -- only when all of these hold:

* clarify could have asked at all (``interactive`` and ``max_questions > 0``);
  otherwise there is nothing to skip and no decision to make;
* the project is greenfield -- on a brownfield project the clarify stage also
  surveys existing code, so skipping it is not safe on request-clarity alone;
* laya is confident (``should_act``) AND confident the request is NOT ambiguous
  (``needs_clarification`` is a noul whose value is P(true); below a
  conservative margin means "clearly unambiguous").

Laya only ever *removes* the clarify call. It must never cause a clarify that
would not have happened, and never suppress one when unsure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rudra.decide.catalog import (
    CLARIFY_NEEDED,
    FAILURE_CLASS,
    TASK_TRIAGE,
    skill_select,
    tool_select,
)
from rudra.decide.engine import should_act
from rudra.decide.log import record

# P(true) for "too ambiguous to start" must be strictly below this to skip.
# With should_act already requiring answer_confidence >= the gate threshold
# (>= 0.75, so max(P) >= 0.75), a passing noul answer that is "not ambiguous"
# already has P(true) <= 0.25; this margin pins that direction explicitly and
# refuses a merely-leaning answer.
AMBIGUITY_MARGIN = 0.25


def skip_clarify(
    engine,
    request: str,
    *,
    greenfield: bool,
    interactive: bool,
    max_questions: int,
) -> bool:
    """True iff the clarify stage can be safely skipped. Logs the decision."""
    # Clarify could not have asked anyway -> nothing to skip; no laya call.
    if not (interactive and max_questions > 0):
        return False
    # Brownfield clarify also reads existing code; request-clarity is not enough.
    if not greenfield:
        return False

    d = engine.decide(request, CLARIFY_NEEDED)["needs_clarification"]
    not_ambiguous = isinstance(d.value, (int, float)) and d.value < AMBIGUITY_MARGIN
    skip = should_act(d) and not_ambiguous
    record(d, acted=skip)
    return skip


# --- #1: blocked-task recovery routing (loop/engine.py + loop/parallel.py) ---
#
# On a BLOCKED task Rudra re-consults the breakdown planner (a Vaayu call). When
# laya is confident about the failure's cause AND a safe deterministic recovery
# exists, take that recovery and skip the re-consult. Only two classes qualify
# (S5 design amendment, Option 2); every other class, abstain, review, or
# unavailable returns None, so the caller re-consults exactly as today. laya can
# never CAUSE an unsafe recovery -- it only shortcuts a path that already works.


class RecoveryAction(Enum):
    """A deterministic recovery the loop can take without the planner."""

    RERUN_GATE = "rerun_gate"  # re-run the verification gate once, no subagent
    ADD_DEP_TASK = "add_dep_task"  # synthesize a "declare & install <pkg>" task


def recovery_route(
    engine,
    gate_output: str,
    *,
    packages: tuple[str, ...],
) -> RecoveryAction | None:
    """Classify a BLOCKED gate failure; return a recovery or None (re-consult).

    ``gate_output`` is the blocker text the loop already computes
    (``loop/engine.py::_blocker_text``), MASKED by the caller exactly as
    ``loop/bounds.py::_exception_lines`` masks it -- the decide layer never
    imports the loop. ``packages`` is what ``loop/dependencies.py::
    missing_packages`` parsed from the gate (data, not laya's guess).

    Returns:
    * ``RERUN_GATE`` when laya is confident the cause is
      ``flaky_or_environment``;
    * ``ADD_DEP_TASK`` when laya is confident the cause is
      ``missing_dependency`` AND at least one package name was parsed;
    * ``None`` otherwise (abstain / review / unavailable, a class with no safe
      deterministic recovery, or missing_dependency with no parsed package) --
      the caller re-consults the planner.

    Logs the decision either way (acted iff a recovery is returned).
    """
    d = engine.decide({"gate_output": gate_output}, FAILURE_CLASS)["failure_class"]
    action = _recovery_for(d, packages)
    record(d, acted=action is not None)
    return action


def _recovery_for(decision, packages: tuple[str, ...]) -> RecoveryAction | None:
    if not should_act(decision):
        return None
    cls = decision.value
    if cls == "flaky_or_environment":
        return RecoveryAction.RERUN_GATE
    if cls == "missing_dependency" and packages:
        return RecoveryAction.ADD_DEP_TASK
    return None


# --- #3: pre-dispatch task triage (loop/engine.py::run_task) -----------------
#
# One laya forward pass over TASK_TRIAGE before the coder is dispatched. Each
# sub-decision gates a concrete, SAFE shortcut and only when laya is confident;
# abstain / review / unavailable -> that field's safe default, which is today's
# full path (run the tester, default budget, label nothing). laya can never
# CAUSE more work than today -- it only ever removes the tester or raises the
# budget.

# P(tests needed) must be strictly below this to skip the tester. should_act
# already requires answer_confidence >= the gate threshold (>= 0.75, so for a
# noul max(P) >= 0.75); a passing "false" answer then has P(true) <= 0.25. This
# margin pins that direction and refuses a merely-leaning answer -- untested
# code must never ship on a guess. Symmetric with AMBIGUITY_MARGIN above.
NEEDS_TESTS_MARGIN = 0.25

# complexity is a score over TASK_TRIAGE's three ordered levels (0 trivial,
# 1 normal, 2 hard). Only a confident HARD score (value at or above this, i.e.
# clearly level 2) raises the fix budget; the threshold sits between normal and
# hard so a middling score does not. Raising only -- never starving (S6 Review
# Focus): a wrong label must not block a hard task early.
HARD_COMPLEXITY_LEVEL = 1.5
# The DELTA added to the configured max_fix_attempts for a hard task. A delta,
# not an absolute ceiling, so the caller's budget (attempts + default + extra)
# can only ever rise above the floor -- there is no clamp to get wrong.
HARD_EXTRA_ATTEMPTS = 2


@dataclass(frozen=True)
class Triage:
    """What laya read off a pending task before the coder is dispatched.

    Every field is the safe default unless laya was confident: ``skip_tests``
    False runs the tester as today, ``extra_attempts`` 0 uses the configured
    budget unchanged, ``domain`` None labels nothing. The caller treats a None
    engine the same by building ``Triage.safe()``.
    """

    skip_tests: bool
    extra_attempts: int
    domain: str | None

    @classmethod
    def safe(cls) -> Triage:
        """The all-defaults triage -- no engine, or an unavailable one."""
        return cls(skip_tests=False, extra_attempts=0, domain=None)


def triage(engine, task_text: str) -> Triage:
    """Read a pending task with laya in one forward pass. Logs all three.

    ``task_text`` is passed as the ``task`` state field the TASK_TRIAGE
    instructions reference. Blocking: an async-loop caller offloads via
    ``asyncio.to_thread``. Never raises on an unavailable/abstaining answer --
    every field degrades to its safe default (``engine.decide`` itself never
    raises; see ``DecisionEngine.decide``).
    """
    answers = engine.decide({"task": task_text}, TASK_TRIAGE)
    nt = answers["needs_tests"]
    cx = answers["complexity"]
    dm = answers["domain"]

    skip_tests = (
        should_act(nt) and isinstance(nt.value, (int, float)) and nt.value < NEEDS_TESTS_MARGIN
    )
    raise_budget = (
        should_act(cx) and isinstance(cx.value, (int, float)) and cx.value >= HARD_COMPLEXITY_LEVEL
    )
    extra_attempts = HARD_EXTRA_ATTEMPTS if raise_budget else 0
    domain = dm.value if (should_act(dm) and isinstance(dm.value, str)) else None

    record(nt, acted=bool(skip_tests))
    record(cx, acted=extra_attempts > 0)
    record(dm, acted=domain is not None)
    return Triage(skip_tests=bool(skip_tests), extra_attempts=extra_attempts, domain=domain)


# --- #4a: MCP tool pre-selection hint (subagents/build.py prompt build) -------
#
# The agent today burns `list_mcp_tools -> describe_mcp_tool -> call_mcp_tool`
# (mcp/tools.py), 2-3 Vaayu turns, before it can use any MCP tool. When laya is
# confident which VISIBLE tool fits the task, the caller injects that tool's id +
# schema into the prompt so the agent skips straight to the call. A HINT only:
# the exploration tools stay registered (capability is never removed), and laya
# can only ever name a tool already visible to this subagent -- a pick outside
# the visible set, an abstain/review, an unavailable engine, or no visible tools
# all return None and the agent explores exactly as today.


@dataclass(frozen=True)
class McpHint:
    """The pre-selected MCP tool to inject into a coder/tester prompt.

    Carries the chosen tool's own id, description and JSON input schema so the
    prompt can show the agent what to call and with what shape -- the model
    still writes the argument VALUES. Built only from a tool the subagent can
    actually see (``mcp/tools.py::_visible``).
    """

    tool_id: str
    description: str
    input_schema: dict


def mcp_hint(engine, task_text: str, visible_tools: Sequence[Any]) -> McpHint | None:
    """Pre-pick one MCP tool for ``task_text`` from ``visible_tools``, or None.

    ``visible_tools`` is the tool list already filtered to this subagent's
    allow/deny visibility (``mcp/tools.py::_visible``); each element is
    duck-typed with ``.id``, ``.description`` and ``.input_schema`` (an MCP
    ``ToolInfo``). The caller resolves it -- the decide layer never imports the
    MCP client or the loop.

    Returns an ``McpHint`` naming the chosen tool and carrying its schema when
    laya is confident (``should_act``) AND the chosen id is one of the visible
    tools; otherwise ``None`` (abstain / review / unavailable / a pick outside
    the visible set / no visible tools), so the caller's agent explores as today.

    Logs the decision (acted iff a hint is returned). Makes no laya call when
    there is nothing to pick.
    """
    by_id = {t.id: t for t in visible_tools}
    if not by_id:
        return None

    d = engine.decide({"task": task_text}, tool_select(list(by_id)))["mcp_tool"]
    chosen = by_id.get(d.value) if should_act(d) else None
    hint = (
        McpHint(
            tool_id=chosen.id,
            description=chosen.description or "",
            input_schema=chosen.input_schema,
        )
        if chosen is not None
        else None
    )
    record(d, acted=hint is not None)
    return hint


# --- #4b: skill pre-selection hint (subagents/build.py prompt build) ----------
#
# The superpowers corpus rides on deepagents' SkillsMiddleware as a
# progressive-disclosure INDEX (descriptions in the prompt, bodies pulled on
# demand) -- there is no fat inlined blob to prune (07a amendment). So this
# helper removes NOTHING: it returns an ordered HINT ("read these SKILL.md
# first") = laya's top-k over the candidate skills UNION an always-on process
# floor laya may never drop. A confident answer emits floor + top-k (laya's
# ranked picks ahead of the floor-only remainder); abstain / review /
# unavailable emits [] -- no hint, every skill stays equally indexed as today.
# Because nothing is removed, a wrong pick can only fail to PROMOTE a skill,
# never drop the one that mattered.

# The always-on process core: the superpowers skills that apply to any coding
# task regardless of its domain. laya reorders/adds on top but may never drop
# these from the hint (07a amendment, owner 2026-11-23). Intersected with the
# run's actually-enabled candidates at call time -- a floor skill not enabled
# cannot be hinted.
SKILL_FLOOR: tuple[str, ...] = (
    "using-superpowers",
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
)


def skill_topk(
    engine,
    task_text: str,
    candidates: Sequence[str],
    *,
    k: int = 3,
    always_on: Sequence[str] = (),
) -> list[str]:
    """Rank ``candidates`` for ``task_text``; return the hint ``floor + top-k``.

    ``candidates`` are the skill names available to this stage (the corpus
    skills in ``[skills] enabled``). ``always_on`` is the process floor always
    kept, intersected with ``candidates`` (a floor skill that is not a candidate
    cannot be hinted). Returns laya's top-``k`` picks (ranked by probability,
    or the single chosen label when no probabilities are emitted) followed by
    any floor skill not already picked, de-duplicated with order preserved.

    On abstain / review (laya ran but is unsure) returns just the floor -- the
    process core is task-independent and safe to promote regardless (owner
    2026-11-23). Returns ``[]`` for an UNAVAILABLE engine (laya never ran, so the
    prompt stays byte-identical to ``backend="off"``) and when there are no
    candidates (then makes no laya call). Logs the decision.
    """
    names = list(candidates)
    if not names:
        return []

    d = engine.decide({"task": task_text}, skill_select(names))["skills_topk"]
    floor = [n for n in always_on if n in names]
    if not should_act(d):
        # laya ran but is unsure -> still promote the always-on process core
        # (task-independent, safe), owner 2026-11-23. But an UNAVAILABLE engine
        # means laya never ran: emit nothing so `backend="off"` renders the
        # prompt byte-identical to today (the caller already guards on
        # engine.available(); this keeps the contract true standalone too).
        hint = [] if d.backend == "unavailable" else floor
        record(d, acted=bool(hint))
        return hint

    probs = d.probabilities
    if isinstance(probs, dict) and probs:
        ranked = sorted((n for n in names if n in probs), key=lambda n: (-probs[n], n))
        topk = ranked[:k]
    elif isinstance(d.value, str) and d.value in names:
        topk = [d.value]
    else:
        topk = []

    out: list[str] = []
    for name in (*topk, *floor):
        if name not in out:
            out.append(name)
    record(d, acted=bool(out))
    return out


def clarify_label_row(
    *,
    greenfield,
    asked,
    questions_n,
    run_succeeded,
    attempted: bool,
) -> dict | None:
    """Build one ``needs_clarification`` training row, or None if unlabelable.

    The honest label -- was clarification actually needed? -- is a run OUTCOME,
    unknowable when the clarify gate fires, so this is built at run end
    (LABEL-SOURCES.md §#2 Task 3). A run that worked no task has no outcome, so
    returns None. ``label`` is the ``asked`` proxy: it imitates today's clarify
    trigger rather than judging true ambiguity (a known leak, LABEL-SOURCES:44),
    so every raw signal is kept in ``features`` and a later fit re-derives the
    gold label from them without re-instrumenting. Pure: primitives in, dict
    out, the shape ``decide.log.record_label`` consumes.
    """
    if not attempted:
        return None
    return {
        "question_id": "needs_clarification",
        "stage": "clarify",
        "features": {
            "greenfield": bool(greenfield),
            "asked": bool(asked),
            "questions_n": int(questions_n),
            "run_converged": bool(run_succeeded),
        },
        "label": bool(asked),
        "pending": False,
    }
