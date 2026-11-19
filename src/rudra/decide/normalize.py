"""Raw laya answer dict -> Decision. Never acts on a missing gate signal."""

from __future__ import annotations

from rudra.decide.engine import Decision


def to_decision(qid: str, kind: str, raw: dict, backend: str) -> Decision:
    value = raw.get(kind)
    answer_confidence = raw.get("answer_confidence")
    passed = raw.get("abstention") == "passed"
    # A missing gate signal (no abstention key, no confidence, no answer)
    # means abstain: never act blind.
    abstain = not passed or answer_confidence is None or value is None
    return Decision(
        question_id=qid,
        kind=kind,
        value=value,
        probabilities=raw.get("probabilities"),
        prediction_set=None,  # laya emits none; deliberately not derived
        abstain=abstain,
        route="auto" if passed else "review",
        # Decision.confidence is a float; None is stored as 0.0 and `abstain`
        # carries the real signal.
        confidence=0.0 if answer_confidence is None else float(answer_confidence),
        backend=backend,
    )
