"""Decision type, the act gate, and the engine stub."""

from __future__ import annotations

from dataclasses import dataclass


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


class DecisionEngine:
    """Stub. The real engine is implemented in a later task."""

    @classmethod
    def build(cls, cfg) -> DecisionEngine:
        return cls()

    def available(self) -> bool:
        return False

    def decide(self, state, questions: dict) -> dict[str, Decision]:
        raise NotImplementedError("DecisionEngine.decide is not implemented yet")
