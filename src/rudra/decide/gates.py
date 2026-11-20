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

from rudra.decide.catalog import CLARIFY_NEEDED
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
