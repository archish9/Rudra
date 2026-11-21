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

from enum import Enum

from rudra.decide.catalog import CLARIFY_NEEDED, FAILURE_CLASS
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
