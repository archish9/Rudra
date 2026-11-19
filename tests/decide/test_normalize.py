from rudra.decide.engine import should_act
from rudra.decide.normalize import to_decision


def _choice(**over):
    raw = {
        "type": "choice",
        "choice": "code",
        "probabilities": {"code": 0.8, "chat": 0.2},
        "confidence": 0.8,
        "answer_confidence": 0.9,
        "action": {"act_probability": 1.0},
        "abstention": "passed",
        "abstention_threshold": 0.5,
    }
    raw.update(over)
    return raw


def test_full_answer_maps_all_fields():
    d = to_decision("q1", "choice", _choice(), "mlx")
    assert d.question_id == "q1"
    assert d.kind == "choice"
    assert d.value == "code"
    assert d.probabilities == {"code": 0.8, "chat": 0.2}
    assert d.confidence == 0.9
    assert d.abstain is False
    assert d.route == "auto"
    assert d.prediction_set is None
    assert d.backend == "mlx"
    assert should_act(d) is True


def test_abstained_answer_routes_to_review():
    d = to_decision("q", "choice", _choice(abstention="abstained"), "mlx")
    assert d.abstain is True
    assert d.route == "review"
    assert should_act(d) is False


def test_missing_abstention_forces_abstain():
    d = to_decision("q", "noul", {"noul": 0.7}, "mlx")
    assert d.abstain is True
    assert d.route == "review"
    assert should_act(d) is False


def test_none_answer_confidence_forces_abstain():
    d = to_decision("q", "choice", _choice(answer_confidence=None), "mlx")
    assert d.abstain is True
    assert d.confidence == 0.0
    assert should_act(d) is False


def test_none_value_forces_abstain():
    d = to_decision("q", "choice", _choice(choice=None), "mlx")
    assert d.abstain is True
    assert should_act(d) is False


def test_score_and_noul_values():
    passed = {"answer_confidence": 0.8, "abstention": "passed"}
    s = to_decision("q", "score", {"score": 3.5, **passed}, "onnx")
    assert s.value == 3.5
    n = to_decision("q", "noul", {"noul": 0.7, **passed}, "onnx")
    assert n.value == 0.7
    assert should_act(s) and should_act(n)
