"""Run-end auto-fit hook (OPEN-182).

Calibration refreshes automatically where it CAN run (ONNX bundles torch); it is
a no-op where it cannot (MLX ships no torch -> Documentation/16-laya.md). These
tests pin the guard and the shared fit path with stubs -- no torch, no laya, no
archive download.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rudra.decide import fit as fitmod


def _cfg(*, auto_fit=True, calibration_dir=None):
    return SimpleNamespace(
        decide=SimpleNamespace(
            enabled=True,
            backend="auto",
            auto_fit=auto_fit,
            calibration_dir=calibration_dir,
        )
    )


def _write_meta(out_dir, fitted_at):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / fitmod.META_FILE).write_text(json.dumps({"fitted_at": fitted_at}))


def _make_runs(runs_dir, n):
    """n archived runs, each with a ledger.json (mtime = now, so > an old fit)."""
    for i in range(n):
        d = runs_dir / f"proj-{i}" / f"run-{i}"
        d.mkdir(parents=True)
        (d / "ledger.json").write_text("{}")


def test_disabled_never_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(fitmod, "_torch_available", lambda: True)
    assert (
        fitmod.should_auto_fit(_cfg(auto_fit=False), tmp_path / "runs", tmp_path / "cal") is False
    )


def test_no_torch_never_fits(tmp_path, monkeypatch):
    # MLX: torch absent -> must skip even with auto_fit on and data present.
    monkeypatch.setattr(fitmod, "_torch_available", lambda: False)
    runs = tmp_path / "runs"
    _make_runs(runs, 10)
    assert fitmod.should_auto_fit(_cfg(), runs, tmp_path / "cal") is False


def test_fits_when_never_calibrated(tmp_path, monkeypatch):
    monkeypatch.setattr(fitmod, "_torch_available", lambda: True)
    # No meta.json in the calibration dir -> fit now.
    assert fitmod.should_auto_fit(_cfg(), tmp_path / "runs", tmp_path / "cal") is True


def test_skips_when_too_few_new_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(fitmod, "_torch_available", lambda: True)
    runs = tmp_path / "runs"
    _make_runs(runs, fitmod.AUTO_FIT_MIN_NEW_RUNS - 1)
    out = tmp_path / "cal"
    _write_meta(out, "1970-01-01T00:00:00+00:00")  # ancient fit; runs are newer
    assert fitmod.should_auto_fit(_cfg(), runs, out) is False


def test_fits_when_enough_new_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(fitmod, "_torch_available", lambda: True)
    runs = tmp_path / "runs"
    _make_runs(runs, fitmod.AUTO_FIT_MIN_NEW_RUNS)
    out = tmp_path / "cal"
    _write_meta(out, "1970-01-01T00:00:00+00:00")
    assert fitmod.should_auto_fit(_cfg(), runs, out) is True


def test_run_fit_returns_none_without_backend(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.decide.backends.select_backend", lambda cfg: None)
    assert fitmod.run_fit(_cfg(), tmp_path / "runs") is None


def test_run_fit_uses_backend_and_archive(tmp_path, monkeypatch):
    seen = {}

    class StubBackend:
        name = "onnx"

        def fit(self, rows, qset, out_dir):
            seen["out_dir"] = out_dir
            seen["qset"] = qset
            return "REPORT"

    monkeypatch.setattr("rudra.decide.backends.select_backend", lambda cfg: StubBackend())
    monkeypatch.setattr(fitmod, "build_dataset", lambda rd, qs: [])
    out = tmp_path / "cal"
    cfg = _cfg(calibration_dir=str(out))
    assert fitmod.run_fit(cfg, tmp_path / "runs") == "REPORT"
    assert seen["out_dir"] == out


def test_maybe_auto_fit_never_raises_and_respects_guard(tmp_path, monkeypatch):
    # Guard says no -> run_fit must not be called, and nothing raises.
    monkeypatch.setattr(fitmod, "should_auto_fit", lambda *a, **k: False)
    called = {"n": 0}
    monkeypatch.setattr(fitmod, "run_fit", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr("rudra.state.archive.archive_home", lambda: tmp_path / "runs")
    assert fitmod.maybe_auto_fit(_cfg()) is None
    assert called["n"] == 0

    # Broken cfg -> swallowed, returns None.
    assert fitmod.maybe_auto_fit(object()) is None


def test_maybe_auto_fit_calls_run_fit_when_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(fitmod, "should_auto_fit", lambda *a, **k: True)
    monkeypatch.setattr(fitmod, "run_fit", lambda cfg, runs_dir: "REPORT")
    monkeypatch.setattr("rudra.state.archive.archive_home", lambda: tmp_path / "runs")
    assert fitmod.maybe_auto_fit(_cfg()) == "REPORT"


def test_decide_config_auto_fit_defaults_true():
    from rudra.config.schema import DecideConfig

    assert DecideConfig().auto_fit is True
