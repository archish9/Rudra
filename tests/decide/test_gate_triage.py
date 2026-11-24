"""triage: the pre-dispatch task triage gate (decide/gates.py, OPEN-178 / S6).

One laya forward pass over ``catalog.TASK_TRIAGE`` produces three labels the
loop reads before dispatching the coder:

* ``needs_tests`` (noul) -> ``skip_tests`` True ONLY when laya is confident the
  task does NOT need tests. ``value`` is P(tests needed); with ``should_act``
  already forcing answer_confidence >= 0.75, a passing "false" answer has
  P(true) <= 0.25, so the margin pins that direction. On any doubt the tester
  runs (the safe direction -- untested code must never ship silently).
* ``complexity`` (score) -> ``extra_attempts``, a DELTA added to the configured
  fix budget. Only a confident HARD score raises it; everything else is 0. It
  can only ever raise, never starve a task below the floor (S6 Review Focus).
* ``domain`` (choice) -> a label only (feeds S7); None unless confident.

Unavailable / abstain / review on any sub-decision -> that field's safe default.
The decide layer never imports the loop; ``task_text`` is passed as the ``task``
state field the TASK_TRIAGE instructions reference.
"""

from rudra.decide.engine import Decision
from rudra.decide.gates import Triage, triage


class _FakeEngine:
    """Returns pre-built Decisions for the three triage questions and records
    the state it was asked about and whether ``decide`` was called."""

    def __init__(self, decisions: dict[str, Decision]) -> None:
        self._decisions = decisions
        self.called = False
        self.state = None

    def decide(self, state, questions):
        self.called = True
        self.state = state
        return dict(self._decisions)


def _noul(value, *, abstain=False, route="auto", backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="needs_tests",
        kind="noul",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def _score(value, *, abstain=False, route="auto", backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="complexity",
        kind="score",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def _choice(value, *, abstain=False, route="auto", backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="domain",
        kind="choice",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def _engine(*, needs_tests, complexity, domain) -> _FakeEngine:
    return _FakeEngine({"needs_tests": needs_tests, "complexity": complexity, "domain": domain})


# --- needs_tests -> skip_tests ----------------------------------------------


def test_confident_no_needs_tests_skips_the_tester():
    # P(tests needed) is low and laya is confident -> safe to skip the tester.
    eng = _engine(needs_tests=_noul(0.1), complexity=_score(1.0), domain=_choice("python"))
    assert triage(eng, "add a LICENSE file").skip_tests is True


def test_confident_yes_needs_tests_does_not_skip():
    # should_act holds, but P(tests needed) is high -> the task wants tests.
    eng = _engine(needs_tests=_noul(0.9), complexity=_score(1.0), domain=_choice("python"))
    assert triage(eng, "implement a parser").skip_tests is False


def test_abstain_needs_tests_does_not_skip():
    eng = _engine(
        needs_tests=_noul(0.1, abstain=True, route="review"),
        complexity=_score(1.0),
        domain=_choice("python"),
    )
    assert triage(eng, "whatever").skip_tests is False


# --- complexity -> extra_attempts (never below the floor) -------------------


def test_normal_complexity_adds_no_attempts():
    eng = _engine(needs_tests=_noul(0.9), complexity=_score(1.0), domain=_choice("python"))
    assert triage(eng, "a normal task").extra_attempts == 0


def test_confident_hard_complexity_raises_the_budget():
    eng = _engine(needs_tests=_noul(0.9), complexity=_score(2.0), domain=_choice("python"))
    assert triage(eng, "a hard task").extra_attempts > 0


def test_abstain_complexity_adds_no_attempts():
    eng = _engine(
        needs_tests=_noul(0.9),
        complexity=_score(2.0, abstain=True, route="review"),
        domain=_choice("python"),
    )
    assert triage(eng, "a hard task").extra_attempts == 0


# --- domain -> label only ----------------------------------------------------


def test_confident_domain_is_labelled():
    eng = _engine(needs_tests=_noul(0.9), complexity=_score(1.0), domain=_choice("python"))
    assert triage(eng, "a python task").domain == "python"


def test_abstain_domain_is_none():
    eng = _engine(
        needs_tests=_noul(0.9),
        complexity=_score(1.0),
        domain=_choice("python", abstain=True, route="review"),
    )
    assert triage(eng, "a task").domain is None


# --- unavailable -> all safe defaults ---------------------------------------


def test_unavailable_engine_returns_all_safe_defaults():
    unav = dict(abstain=True, route="review", backend="unavailable", confidence=0.0)
    eng = _engine(
        needs_tests=_noul(None, **unav),
        complexity=_score(None, **unav),
        domain=_choice(None, **unav),
    )
    result = triage(eng, "anything")
    assert result == Triage(skip_tests=False, extra_attempts=0, domain=None)


# --- state wiring ------------------------------------------------------------


def test_task_text_is_passed_as_the_named_task_state_field():
    # TASK_TRIAGE instructions reference `task`; the state must key it so.
    eng = _engine(needs_tests=_noul(0.9), complexity=_score(1.0), domain=_choice("python"))
    triage(eng, "write a flask app")
    assert eng.state == {"task": "write a flask app"}
