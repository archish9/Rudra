"""S3 Task 3 — load calibration at engine build + staleness guard.

The guard is STRICTER than laya's own load_calibration (which only warns on a
checkpoint mismatch and loads anyway): a calibration whose recorded backend or
model_id does not match the selected backend is ignored, leaving the engine
zero-shot. Weight-free: a fake backend records whether load was called.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rudra.decide.engine import DecisionEngine, load_matching_calibration
from rudra.decide.fit import resolve_calibration_dir


class _FakeBackend:
    def __init__(self, name="mlx", model_id="convaiinnovations/laya"):
        self.name = name
        self.model_id = model_id
        self.loaded_from = None

    def load_calibration(self, path):
        self.loaded_from = path

    def predict(self, state, questions, min_confidence):  # pragma: no cover - unused here
        return {}


def _cfg(calibration_dir, *, backend="mlx", mlx_repo="convaiinnovations/laya"):
    return SimpleNamespace(
        decide=SimpleNamespace(
            enabled=True,
            backend=backend,
            mlx_repo=mlx_repo,
            onnx_model="convaiinnovations/laya",
            calibration_dir=str(calibration_dir),
        )
    )


def _write_calibration(dir_path, *, backend, model_id):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "calibration.json").write_text(json.dumps({"temperature": [1.0]}))
    (dir_path / "meta.json").write_text(
        json.dumps({"backend": backend, "model_id": model_id, "thresholds": {}})
    )


def test_matching_calibration_is_loaded(tmp_path):
    _write_calibration(tmp_path, backend="mlx", model_id="convaiinnovations/laya")
    backend = _FakeBackend(name="mlx", model_id="convaiinnovations/laya")
    loaded = load_matching_calibration(backend, _cfg(tmp_path))
    assert loaded is True
    assert backend.loaded_from == str(tmp_path / "calibration.json")


def test_mismatched_model_id_is_ignored(tmp_path):
    _write_calibration(tmp_path, backend="mlx", model_id="old/checkpoint")
    backend = _FakeBackend(name="mlx", model_id="convaiinnovations/laya")
    loaded = load_matching_calibration(backend, _cfg(tmp_path))
    assert loaded is False
    assert backend.loaded_from is None  # stale -> stays zero-shot, no error


def test_mismatched_backend_is_ignored(tmp_path):
    _write_calibration(tmp_path, backend="onnx", model_id="convaiinnovations/laya")
    backend = _FakeBackend(name="mlx", model_id="convaiinnovations/laya")
    assert load_matching_calibration(backend, _cfg(tmp_path)) is False
    assert backend.loaded_from is None


def test_absent_calibration_is_zero_shot(tmp_path):
    backend = _FakeBackend()
    assert load_matching_calibration(backend, _cfg(tmp_path / "nope")) is False
    assert backend.loaded_from is None


def test_corrupt_meta_is_ignored_not_raised(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "calibration.json").write_text("{}")
    (tmp_path / "meta.json").write_text("{not json")
    backend = _FakeBackend()
    assert load_matching_calibration(backend, _cfg(tmp_path)) is False
    assert backend.loaded_from is None


def test_resolve_calibration_dir_prefers_explicit(tmp_path):
    cfg = _cfg(tmp_path / "explicit")
    assert resolve_calibration_dir(cfg) == tmp_path / "explicit"


def test_resolve_calibration_dir_defaults_to_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    cfg = SimpleNamespace(
        decide=SimpleNamespace(
            enabled=True,
            backend="mlx",
            mlx_repo="x",
            onnx_model="y",
            calibration_dir=None,
        )
    )
    assert resolve_calibration_dir(cfg) == tmp_path / "rudra" / "calibration"


def test_build_loads_matching_calibration(tmp_path, monkeypatch):
    _write_calibration(tmp_path, backend="mlx", model_id="convaiinnovations/laya")
    backend = _FakeBackend(name="mlx", model_id="convaiinnovations/laya")
    monkeypatch.setattr("rudra.decide.engine.select_backend", lambda cfg: backend)
    engine = DecisionEngine.build(_cfg(tmp_path))
    assert engine.available() is True
    assert backend.loaded_from == str(tmp_path / "calibration.json")
