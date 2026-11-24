from rudra.decide import Decision, should_act


def test_should_act_true_only_when_auto_not_abstain_available():
    d = Decision(
        "q", "noul", True, None, None, abstain=False, route="auto", confidence=0.9, backend="mlx"
    )
    assert should_act(d) is True


def test_should_act_false_on_review_abstain_or_unavailable():
    base = dict(
        question_id="q",
        kind="noul",
        value=True,
        probabilities=None,
        prediction_set=None,
        confidence=0.9,
    )
    assert should_act(Decision(**base, abstain=False, route="review", backend="mlx")) is False
    assert should_act(Decision(**base, abstain=True, route="auto", backend="mlx")) is False
    assert should_act(Decision(**base, abstain=False, route="auto", backend="unavailable")) is False
