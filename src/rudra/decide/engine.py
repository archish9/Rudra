"""Decision type, the act gate, and the engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rudra.decide.backends import DecideBackend, select_backend


@dataclass(frozen=True)
class Decision:
    question_id: str
    kind: str  # "choice" | "score" | "noul"
    value: object  # label (choice) | float level (score) | P(true) (noul)
    probabilities: dict[str, float] | None
    prediction_set: list[str] | None
    abstain: bool
    route: str  # "auto" | "review"
    confidence: float
    backend: str  # "mlx" | "onnx" | "unavailable"


def should_act(d: Decision) -> bool:
    return d.backend != "unavailable" and not d.abstain and d.route == "auto"


_log = logging.getLogger("rudra.decide")

# S1 has no per-question threshold; S2 adds them.
DEFAULT_MIN_CONFIDENCE = 0.75
_KNOWN_BACKENDS = {"auto", "mlx", "onnx", "off"}


def _unavailable(questions: dict) -> dict[str, Decision]:
    return {
        qid: Decision(
            question_id=qid,
            kind=(q or {}).get("type", "?"),
            value=None,
            probabilities=None,
            prediction_set=None,
            abstain=True,
            route="review",
            confidence=0.0,
            backend="unavailable",
        )
        for qid, q in questions.items()
    }


class DecisionEngine:
    """Backend-agnostic front for laya. Never raises from build or decide."""

    def __init__(self, backend: DecideBackend | None = None) -> None:
        self._backend = backend
        self._threshold = DEFAULT_MIN_CONFIDENCE

    @classmethod
    def build(cls, cfg) -> DecisionEngine:
        try:
            settings = getattr(cfg, "decide", None)
            if not getattr(settings, "enabled", True):
                return cls()
            choice = getattr(settings, "backend", "auto")
            if choice not in _KNOWN_BACKENDS:
                _log.warning("unknown [decide] backend %r; decision layer stays off", choice)
            return cls(select_backend(cfg))
        except Exception as exc:  # noqa: BLE001 - build must never fail a run
            _log.warning("decision backend selection failed: %s", exc)
            return cls()

    def available(self) -> bool:
        return self._backend is not None

    def decide(self, state, questions: dict) -> dict[str, Decision]:
        """Decide every question. Synchronous: laya's forward pass blocks.

        Async-loop callers must offload via ``asyncio.to_thread(engine.decide, ...)``.
        """
        if self._backend is None:
            return _unavailable(questions)
        try:
            result = self._backend.predict(state, questions, self._threshold)
        except Exception as exc:  # noqa: BLE001 - one bad call must not end the run
            _log.warning("decision call failed (%s): %s", type(exc).__name__, exc)
            return _unavailable(questions)
        name = self._backend.name
        return {
            qid: to_decision(qid, q["type"], (result or {}).get(qid) or {}, name)
            for qid, q in questions.items()
        }


from rudra.decide.normalize import to_decision  # noqa: E402 - normalize imports Decision
