"""skill_topk: the skill pre-selection HINT (decide/gates.py, OPEN-179 Task 2).

Per the 07a amendment: the superpowers corpus is a progressive-disclosure index
(descriptions, bodies pulled on demand), not a fat inlined blob, so skill_topk
removes NOTHING. It returns an ordered HINT -- "read these SKILL.md first" --
built from laya's top-k over the candidate skills UNION an always-on process
floor laya may never drop. A confident answer emits ``floor + top-k`` (the floor
always present, laya's ranked picks ahead of floor-only remainder); abstain /
review / unavailable emits ``[]`` (no hint, every skill stays equally indexed,
today's behaviour). Because nothing is removed, a wrong pick can only fail to
PROMOTE a skill, never drop one.
"""

from rudra.decide.engine import Decision
from rudra.decide.gates import skill_topk

CANDIDATES = [
    "brainstorming",
    "test-driven-development",
    "systematic-debugging",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
    "using-git-worktrees",
]
FLOOR = ("using-superpowers", "test-driven-development")


class _FakeEngine:
    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision
        self.called = False
        self.state = None

    def decide(self, state, questions):
        self.called = True
        self.state = state
        return {"skills_topk": self._decision}


def _decision(*, value, probabilities=None, abstain=False, route="auto", backend="mlx") -> Decision:
    return Decision(
        question_id="skills_topk",
        kind="choice",
        value=value,
        probabilities=probabilities,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=0.9,
        backend=backend,
    )


def test_confident_ranking_returns_floor_union_topk():
    probs = {
        "writing-plans": 0.5,
        "brainstorming": 0.3,
        "systematic-debugging": 0.1,
        "using-git-worktrees": 0.05,
        "using-superpowers": 0.02,
        "test-driven-development": 0.02,
        "verification-before-completion": 0.01,
    }
    eng = _FakeEngine(_decision(value="writing-plans", probabilities=probs))
    out = skill_topk(eng, "plan a multi-step feature", CANDIDATES, k=2, always_on=FLOOR)
    # laya's top 2 by probability come first, in rank order...
    assert out[:2] == ["writing-plans", "brainstorming"]
    # ...and the floor is always present (appended if not already picked).
    assert set(FLOOR) <= set(out)
    # nothing outside the candidate set, no duplicates
    assert set(out) <= set(CANDIDATES)
    assert len(out) == len(set(out))


def test_abstain_returns_just_the_floor():
    # laya ran but is unsure -> promote the always-on process core only
    # (task-independent, safe). owner 2026-11-23.
    eng = _FakeEngine(_decision(value="writing-plans", abstain=True, route="review"))
    assert skill_topk(eng, "x", CANDIDATES, k=2, always_on=FLOOR) == list(FLOOR)


def test_review_route_returns_just_the_floor():
    eng = _FakeEngine(_decision(value="writing-plans", route="review"))
    assert skill_topk(eng, "x", CANDIDATES, k=2, always_on=FLOOR) == list(FLOOR)


def test_unavailable_returns_empty_hint():
    # laya never ran -> no hint at all, so backend="off" stays byte-identical
    # to today (and no floor leaks in).
    eng = _FakeEngine(_decision(value=None, abstain=True, route="review", backend="unavailable"))
    assert skill_topk(eng, "x", CANDIDATES, k=2, always_on=FLOOR) == []


def test_floor_always_present_when_confident_even_if_low_ranked():
    probs = {n: 0.01 for n in CANDIDATES}
    probs["writing-plans"] = 0.9
    eng = _FakeEngine(_decision(value="writing-plans", probabilities=probs))
    out = skill_topk(eng, "x", CANDIDATES, k=1, always_on=FLOOR)
    assert set(FLOOR) <= set(out)
    assert "writing-plans" in out


def test_k_larger_than_candidates_returns_all_candidates():
    probs = {n: 1.0 / (i + 1) for i, n in enumerate(CANDIDATES)}
    eng = _FakeEngine(_decision(value=CANDIDATES[0], probabilities=probs))
    out = skill_topk(eng, "x", CANDIDATES, k=99, always_on=FLOOR)
    assert set(out) == set(CANDIDATES)


def test_floor_name_not_in_candidates_is_dropped():
    # A floor skill that is not an available candidate cannot be hinted.
    probs = {n: 0.1 for n in CANDIDATES}
    probs["writing-plans"] = 0.9
    eng = _FakeEngine(_decision(value="writing-plans", probabilities=probs))
    out = skill_topk(eng, "x", CANDIDATES, k=1, always_on=("not-a-real-skill", "using-superpowers"))
    assert "not-a-real-skill" not in out
    assert "using-superpowers" in out


def test_no_probabilities_falls_back_to_single_pick_plus_floor():
    # Confident but no probability dict -> use the single chosen label.
    eng = _FakeEngine(_decision(value="systematic-debugging", probabilities=None))
    out = skill_topk(eng, "x", CANDIDATES, k=3, always_on=FLOOR)
    assert "systematic-debugging" in out
    assert set(FLOOR) <= set(out)


def test_no_candidates_returns_empty_without_calling_laya():
    eng = _FakeEngine(_decision(value="x"))
    assert skill_topk(eng, "x", [], k=2, always_on=FLOOR) == []
    assert eng.called is False


def test_task_is_passed_as_named_state_field():
    eng = _FakeEngine(_decision(value="brainstorming", probabilities={"brainstorming": 0.9}))
    skill_topk(eng, "design something new", CANDIDATES, k=1, always_on=FLOOR)
    assert eng.state == {"task": "design something new"}


def test_options_are_built_from_candidate_names():
    captured = {}

    class _CapturingEngine(_FakeEngine):
        def decide(self, state, questions):
            captured["questions"] = questions
            return super().decide(state, questions)

    eng = _CapturingEngine(_decision(value="brainstorming", probabilities={"brainstorming": 0.9}))
    skill_topk(eng, "x", CANDIDATES, k=1, always_on=FLOOR)
    assert set(captured["questions"]["skills_topk"]["criteria"]) == set(CANDIDATES)
