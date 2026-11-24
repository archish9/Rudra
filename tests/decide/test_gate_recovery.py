"""recovery_route: the BLOCKED-task failure-class gate (decide/gates.py).

Maps a laya ``failure_class`` choice to a deterministic recovery, or None
(re-consult the breakdown planner, today's path). Per the S5 design amendment
(Option 2) only two classes have a safe deterministic recovery:

* ``flaky_or_environment`` -> RERUN_GATE (re-run the gate once, no subagent),
* ``missing_dependency`` -> ADD_DEP_TASK, but ONLY when a concrete package name
  was parsed from the gate output (the package is data, not laya's guess).

Every other class, abstain, review, or unavailable -> None. The decide layer
takes the already-masked gate output and the parsed packages; it never imports
the loop.
"""

from rudra.decide.engine import Decision
from rudra.decide.gates import RecoveryAction, recovery_route


class _FakeEngine:
    """Returns one pre-built Decision for ``failure_class`` and records the
    state it was asked about and whether ``decide`` was called at all."""

    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision
        self.called = False
        self.state = None

    def decide(self, state, questions):
        self.called = True
        self.state = state
        return {"failure_class": self._decision}


def _decision(*, value, abstain=False, route="auto", backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="failure_class",
        kind="choice",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def test_confident_missing_dependency_with_package_returns_add_dep_task():
    eng = _FakeEngine(_decision(value="missing_dependency"))
    action = recovery_route(eng, "No module named 'sqlalchemy'", packages=("sqlalchemy",))
    assert action is RecoveryAction.ADD_DEP_TASK


def test_confident_flaky_returns_rerun_gate():
    eng = _FakeEngine(_decision(value="flaky_or_environment"))
    action = recovery_route(eng, "connection reset during test setup", packages=())
    assert action is RecoveryAction.RERUN_GATE


def test_missing_dependency_without_parsed_package_returns_none():
    # laya is confident it is a missing dependency, but no package name was
    # parsed from the gate output -> never synthesize a task blind -> re-consult.
    eng = _FakeEngine(_decision(value="missing_dependency"))
    assert recovery_route(eng, "ImportError: something", packages=()) is None


def test_abstain_returns_none_so_planner_is_consulted():
    eng = _FakeEngine(_decision(value="missing_dependency", abstain=True, route="review"))
    assert recovery_route(eng, "No module named 'x'", packages=("x",)) is None


def test_review_route_returns_none():
    eng = _FakeEngine(_decision(value="flaky_or_environment", route="review"))
    assert recovery_route(eng, "flaky", packages=()) is None


def test_class_without_deterministic_recovery_returns_none():
    for cls in ("wrong_approach", "needs_decomposition"):
        eng = _FakeEngine(_decision(value=cls))
        assert recovery_route(eng, "assert 1 == 2", packages=()) is None


def test_unavailable_returns_none_without_recovery():
    eng = _FakeEngine(
        _decision(value=None, abstain=True, route="review", backend="unavailable", confidence=0.0)
    )
    assert recovery_route(eng, "anything", packages=("x",)) is None


def test_gate_output_is_passed_as_named_state_field():
    # FAILURE_CLASS instructions reference `gate_output`; the state must key it so.
    eng = _FakeEngine(_decision(value="wrong_approach"))
    recovery_route(eng, "E   assert 1 == 2", packages=())
    assert eng.state == {"gate_output": "E   assert 1 == 2"}
