"""skip_clarify: the clarify-stage gate helper (decide/gates.py).

The gate may skip the clarify Vaayu call ONLY when clarify could have asked
(interactive + a question budget), the project is greenfield, and laya is
confident the request is NOT ambiguous. Every other path returns False, so
the caller runs today's clarify stage unchanged.
"""

from rudra.decide.engine import Decision
from rudra.decide.gates import skip_clarify


class _FakeEngine:
    """Returns one pre-built Decision for ``needs_clarification`` and records
    whether ``decide`` was called at all (so the noop path can be asserted)."""

    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision
        self.called = False

    def decide(self, state, questions):
        self.called = True
        return {"needs_clarification": self._decision}


def _decision(*, value, abstain, route, backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="needs_clarification",
        kind="noul",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def _args(**overrides):
    base = dict(greenfield=True, interactive=True, max_questions=5)
    base.update(overrides)
    return base


def test_skip_true_when_laya_confident_not_ambiguous():
    # P(true) low + confident + route auto -> request is clearly unambiguous.
    eng = _FakeEngine(_decision(value=0.09, abstain=False, route="auto"))
    assert skip_clarify(eng, "build a flask hello world", **_args()) is True
    assert eng.called is True


def test_no_skip_when_abstain():
    eng = _FakeEngine(_decision(value=0.4, abstain=True, route="review"))
    assert skip_clarify(eng, "build something", **_args()) is False


def test_no_skip_when_laya_says_ambiguous():
    # P(true) high -> laya thinks it IS ambiguous, even though confident.
    eng = _FakeEngine(_decision(value=0.9, abstain=False, route="auto"))
    assert skip_clarify(eng, "make it better", **_args()) is False


def test_no_skip_when_unavailable():
    eng = _FakeEngine(
        _decision(value=None, abstain=True, route="review", backend="unavailable", confidence=0.0)
    )
    assert skip_clarify(eng, "build a flask app", **_args()) is False


def test_noop_when_not_interactive_or_zero_questions():
    # Clarify could not have asked anyway: return False without consulting laya.
    confident = _decision(value=0.01, abstain=False, route="auto")

    eng = _FakeEngine(confident)
    assert skip_clarify(eng, "x", **_args(interactive=False)) is False
    assert eng.called is False

    eng = _FakeEngine(confident)
    assert skip_clarify(eng, "x", **_args(max_questions=0)) is False
    assert eng.called is False


def test_no_skip_when_not_greenfield():
    # Brownfield: clarify also surveys existing code. Never skip; no laya call.
    eng = _FakeEngine(_decision(value=0.01, abstain=False, route="auto"))
    assert skip_clarify(eng, "add a route", **_args(greenfield=False)) is False
    assert eng.called is False
