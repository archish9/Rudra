"""The decision diagnostic record (CLAUDE.md section 8a).

One line per decision on the ``rudra.decide`` logger, promoted to the top level
of ``debug-<id>.jsonl`` by ``_JsonLines.format``. Metadata only: the raw
``state`` a decision was made from is never logged.
"""

from __future__ import annotations

import logging

from rudra.decide.engine import Decision

DECISION_EVENT = "decision"

_logger = logging.getLogger("rudra.decide")


def _scalar(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def record(decision: Decision, *, acted: bool) -> None:
    """Log one decision. Never raises: bookkeeping may not end a run."""
    try:
        _logger.info(
            "decision",
            extra={
                "event": {
                    "kind": DECISION_EVENT,
                    "question_id": decision.question_id,
                    "backend": decision.backend,
                    "qtype": decision.kind,
                    "abstain": decision.abstain,
                    "route": decision.route,
                    "confidence": decision.confidence,
                    "value": _scalar(decision.value),
                    "acted": acted,
                }
            },
        )
    except Exception:  # noqa: BLE001 - a diagnostic must never fail the run
        pass
