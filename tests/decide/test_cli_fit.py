"""S3 Task 4 — `rudra decide fit` / `rudra decide status` CLI.

Weight-free. With laya absent (the test environment), the backend is
unavailable and `fit` must exit 0 with a clear message rather than error. A
stubbed backend exercises the real dataset build + report path.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from rudra.cli import app
from rudra.decide.fit import FitReport, QFit

runner = CliRunner()


def _archive_with_one_done_task(tmp_path):
    run = tmp_path / "proj" / "run-1"
    run.mkdir(parents=True)
    (run / "ledger.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": 1, "description": "x", "status": "done", "attempts": 1, "seconds": 10.0}
                ]
            }
        )
    )
    (run / "facts.json").write_text(json.dumps({"facts": {"stack": {"value": "python project"}}}))
    return tmp_path


def test_fit_with_unavailable_backend_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.decide.backends.select_backend", lambda cfg: None)
    result = runner.invoke(app, ["decide", "fit", "-d", str(tmp_path), "--runs", str(tmp_path)])
    assert result.exit_code == 0
    assert "unavailable" in result.output.lower()


def test_status_with_unavailable_backend(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.decide.backends.select_backend", lambda cfg: None)
    result = runner.invoke(app, ["decide", "status", "-d", str(tmp_path)])
    assert result.exit_code == 0
    assert "unavailable" in result.output.lower()


def test_fit_with_stubbed_backend_builds_and_reports(tmp_path, monkeypatch):
    archive = _archive_with_one_done_task(tmp_path / "arch")
    out_dir = tmp_path / "cal"

    captured = {}

    class _StubBackend:
        name = "mlx"
        model_id = "convaiinnovations/laya"

        def fit(self, rows, question_set, fit_out_dir):
            captured["rows"] = rows
            captured["out_dir"] = fit_out_dir
            return FitReport(
                per_question={
                    "complexity": QFit(
                        n_rows=len(rows), served=False, note="declined: too few rows for a gate"
                    ),
                    "domain": QFit(
                        n_rows=len(rows), served=False, note="declined: too few rows for a gate"
                    ),
                },
                calibration_written=False,
            )

    monkeypatch.setattr("rudra.decide.backends.select_backend", lambda cfg: _StubBackend())
    monkeypatch.setattr("rudra.decide.fit.resolve_calibration_dir", lambda cfg: out_dir)

    result = runner.invoke(app, ["decide", "fit", "-d", str(archive), "--runs", str(archive)])
    assert result.exit_code == 0, result.output
    # real build_dataset ran over the fixture: one done python task -> one row
    assert len(captured["rows"]) == 1
    assert captured["out_dir"] == out_dir
    # honest report is printed
    out = result.output.lower()
    assert "complexity" in out
    assert "declined" in out or "zero-shot" in out


def test_status_with_stubbed_backend_names_it(tmp_path, monkeypatch):
    class _StubBackend:
        name = "mlx"
        model_id = "convaiinnovations/laya"

        def load_calibration(self, path):
            pass

    monkeypatch.setattr("rudra.decide.engine.select_backend", lambda cfg: _StubBackend())
    result = runner.invoke(app, ["decide", "status", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "mlx" in result.output.lower()
