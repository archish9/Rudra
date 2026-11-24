"""DecisionEngine.build / decide and the decision log line."""

import logging
from types import SimpleNamespace

from rudra.decide import engine as engine_mod
from rudra.decide.engine import Decision, DecisionEngine, should_act
from rudra.decide.log import DECISION_EVENT, record

Q = {"q": {"type": "noul", "instructions": "?"}}


def _cfg(enabled=True, backend="auto"):
    return SimpleNamespace(
        decide=SimpleNamespace(
            enabled=enabled,
            backend=backend,
            mlx_repo="x",
            onnx_model="x",
            calibration_dir=None,
        )
    )


class _Fake:
    def __init__(self, result=None, exc=None, name="mlx"):
        self.name = name
        self._result = result
        self._exc = exc
        self.seen = None

    def predict(self, state, questions, min_confidence):
        self.seen = min_confidence
        if self._exc:
            raise self._exc
        return self._result


def _engine(monkeypatch, backend):
    monkeypatch.setattr(engine_mod, "select_backend", lambda cfg: backend)
    return DecisionEngine.build(_cfg())


def test_build_unavailable_decides_without_raising():
    eng = DecisionEngine.build(_cfg(enabled=False))
    assert eng.available() is False
    out = eng.decide("hi", Q)
    assert out["q"].backend == "unavailable"
    assert should_act(out["q"]) is False


def test_build_swallows_selection_errors(monkeypatch):
    def boom(cfg):
        raise RuntimeError("x")

    monkeypatch.setattr(engine_mod, "select_backend", boom)
    assert DecisionEngine.build(_cfg()).available() is False


def test_build_with_fake_backend_normalizes(monkeypatch):
    ok = {"q": {"type": "noul", "noul": 0.09, "answer_confidence": 0.9, "abstention": "passed"}}
    fake = _Fake(ok)
    eng = _engine(monkeypatch, fake)
    d = eng.decide("hi", Q)["q"]
    assert d.backend == "mlx" and d.value == 0.09 and should_act(d)
    assert fake.seen == engine_mod.DEFAULT_MIN_CONFIDENCE

    no = {"q": {"type": "noul", "noul": 0.5, "answer_confidence": 0.5, "abstention": "abstained"}}
    assert should_act(_engine(monkeypatch, _Fake(no)).decide("hi", Q)["q"]) is False


def test_missing_qid_abstains(monkeypatch):
    d = _engine(monkeypatch, _Fake({})).decide("hi", Q)["q"]
    assert d.abstain and not should_act(d)


def test_decide_falls_back_when_backend_raises(monkeypatch):
    eng = _engine(monkeypatch, _Fake(exc=RuntimeError("boom")))
    d = eng.decide("hi", Q)["q"]
    assert d.backend == "unavailable" and should_act(d) is False


def test_bad_backend_value_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="rudra.decide"):
        DecisionEngine.build(_cfg(backend="bogus"))
    assert any("bogus" in r.getMessage() for r in caplog.records)


def test_record_writes_a_decision_log_line(caplog):
    d = Decision("q1", "noul", 0.1, None, None, False, "auto", 0.9, "mlx")
    with caplog.at_level(logging.INFO, logger="rudra.decide"):
        record(d, acted=True)
    events = [r.event for r in caplog.records if hasattr(r, "event")]
    assert events and events[0]["kind"] == DECISION_EVENT
    assert events[0]["question_id"] == "q1" and events[0]["acted"] is True


def test_failure_class_uses_a_lower_per_question_threshold(monkeypatch):
    # failure_class is gated at its own threshold; other questions keep the
    # default. Each question group is predicted at its own min_confidence.
    seen: dict = {}

    class _Multi:
        name = "mlx"

        def predict(self, state, questions, min_confidence):
            for qid in questions:
                seen[qid] = min_confidence
            return {qid: {} for qid in questions}

    eng = _engine(monkeypatch, _Multi())
    eng.decide("s", {"failure_class": {"type": "choice"}, "other": {"type": "noul"}})
    assert seen["failure_class"] == engine_mod.QUESTION_THRESHOLDS["failure_class"]
    assert seen["failure_class"] < engine_mod.DEFAULT_MIN_CONFIDENCE
    assert seen["other"] == engine_mod.DEFAULT_MIN_CONFIDENCE


def test_malformed_question_abstains(monkeypatch):
    eng = _engine(monkeypatch, _Fake({}))
    d = eng.decide("hi", {"q": {"instructions": "no type"}})["q"]
    assert d.abstain and not should_act(d)


def test_non_dict_question_value_abstains_not_raises(monkeypatch):
    off = DecisionEngine.build(_cfg(enabled=False))
    out = off.decide("s", {"q": "noul"})
    assert out["q"].backend == "unavailable"
    assert should_act(out["q"]) is False

    eng = _engine(monkeypatch, _Fake(result={}))
    out = eng.decide("s", {"q": "noul"})
    assert out["q"].abstain is True
    assert should_act(out["q"]) is False
