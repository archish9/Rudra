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
