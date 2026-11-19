"""Backend selection by capability: MLX, then ONNX, else None."""

from types import SimpleNamespace

from rudra.decide import backends
from rudra.decide.backends import select_backend


class _Fake:
    def __init__(self, name: str) -> None:
        self.name = name

    def predict(self, state, questions, min_confidence):
        return {}


def _cfg(backend: str = "auto"):
    return SimpleNamespace(decide=SimpleNamespace(backend=backend))


def test_select_prefers_mlx_when_usable(monkeypatch):
    monkeypatch.setattr(backends, "_try_mlx", lambda cfg: _Fake("mlx"))
    monkeypatch.setattr(backends, "_try_onnx", lambda cfg: _Fake("onnx"))
    assert select_backend(_cfg()).name == "mlx"


def test_select_falls_through_to_onnx_when_no_mlx(monkeypatch):
    monkeypatch.setattr(backends, "_try_mlx", lambda cfg: None)
    monkeypatch.setattr(backends, "_try_onnx", lambda cfg: _Fake("onnx"))
    assert select_backend(_cfg()).name == "onnx"


def test_select_returns_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(backends, "_try_mlx", lambda cfg: None)
    monkeypatch.setattr(backends, "_try_onnx", lambda cfg: None)
    assert select_backend(_cfg()) is None


def test_off_and_single_backend_overrides(monkeypatch):
    monkeypatch.setattr(backends, "_try_mlx", lambda cfg: _Fake("mlx"))
    monkeypatch.setattr(backends, "_try_onnx", lambda cfg: _Fake("onnx"))
    assert select_backend(_cfg("off")) is None
    assert select_backend(_cfg("onnx")).name == "onnx"
    assert select_backend(_cfg("mlx")).name == "mlx"
    assert select_backend(SimpleNamespace()).name == "mlx"


def test_load_failure_means_not_this_backend(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("checkpoint download failed")

    monkeypatch.setattr(backends, "_load_mlx", boom)
    monkeypatch.setattr(backends, "_load_onnx", boom)
    assert select_backend(_cfg()) is None


def test_mlx_predict_uses_the_gated_decide_call(monkeypatch):
    import sys
    import types

    calls = {}
    values = {"q": {"noul": 0.1, "abstention": "passed"}}

    def fake_decide(agent, state, **kwargs):
        calls.update(kwargs)
        return types.SimpleNamespace(values=values)

    stub = types.ModuleType("laya_mlx")
    stub.decide = fake_decide
    monkeypatch.setitem(sys.modules, "laya_mlx", stub)

    out = backends._AgentBackend("mlx", object()).predict("s", {"q": {}}, 0.75)
    assert out is values
    assert calls["return_details"] is True and calls["min_confidence"] == 0.75
