"""The decision diagnostic record (CLAUDE.md section 8a).

One line per decision on the ``rudra.decide`` logger, promoted to the top level
of ``debug-<id>.jsonl`` by ``_JsonLines.format``. Metadata only: the raw
``state`` a decision was made from is never logged.
"""

from __future__ import annotations

import logging

from rudra.decide.engine import Decision

DECISION_EVENT = "decision"
LABEL_EVENT = "decision_label"

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


def record_label(
    *,
    question_id: str,
    stage: str,
    features: dict,
    label: object,
    pending: bool = False,
) -> None:
    """Emit one training-label row for a decision laya cannot yet fit from the
    archive (see ``LABEL-SOURCES.md``). Metadata only -- ``features`` carry the
    caller's chosen scalar signals, never raw prompt/state text.

    ``pending=True`` records an input row whose outcome label is not yet known
    (e.g. a blocked-task cause before the recovery converges). Never raises.
    """
    try:
        _logger.info(
            "decision_label",
            extra={
                "event": {
                    "kind": LABEL_EVENT,
                    "question_id": question_id,
                    "stage": stage,
                    "features": {str(k): _scalar(v) for k, v in (features or {}).items()},
                    "label": _scalar(label),
                    "pending": pending,
                }
            },
        )
    except Exception:  # noqa: BLE001 - bookkeeping may never end a run
        pass
