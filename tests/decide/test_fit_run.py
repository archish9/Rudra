"""S3 Task 2 — fit + calibrate + honest floor.

Weight-free: a stub agent and an injected ``calibrate`` namespace stand in for
laya. ``fit_abstention_thresholds`` returning ``{}`` is laya's own floor (no
bucket reached the sample minimum) and must leave every question zero-shot and
write no calibration file.
"""

from __future__ import annotations

import json

from rudra.decide.catalog import TASK_TRIAGE
from rudra.decide.fit import fit_calibration


class _StubAgent:
    """Minimal laya-agent surface fit_calibration touches."""

    def __init__(self):
        self.temperature = [1.0]
        self.temperature_by_options = {}
        self.fit_temperatures_called_with = None
        self.saved_to = None

    def fit_temperatures(self, records, compute_ece=False, seed=0):
        self.fit_temperatures_called_with = records
        return {"temperature": self.temperature}

    def save_calibration(self, path):
        self.saved_to = path
        with open(path, "w") as f:
            json.dump({"temperature": self.temperature}, f)


class _FakeCalibrate:
    """Injected stand-in for laya_mlx.calibrate."""

    def __init__(self, thresholds):
        self._thresholds = thresholds
        self.records_from_labeled_called = False

    def records_from_labeled(self, agent, rows):
        self.records_from_labeled_called = True
        return [("rec",) for _ in rows]

    def fit_abstention_thresholds(self, records, temperature, temperature_by_options, **kw):
        return dict(self._thresholds)


def _rows(n, qid="complexity"):
    q = {qid: TASK_TRIAGE[qid]}
    return [({"task": f"t{i}"}, q, {qid: [1.0, 0.0, 0.0]}) for i in range(n)]


def test_empty_thresholds_leaves_question_zero_shot(tmp_path):
    cal = _FakeCalibrate({})  # laya's floor: no bucket big enough
    agent = _StubAgent()
    report = fit_calibration(
        agent,
        _rows(5),
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="m",
        calibrate=cal,
    )
    assert report.per_question["complexity"].served is False
    assert "declined" in report.per_question["complexity"].note
    assert report.calibration_written is False


def test_served_flag_reflects_nonempty_thresholds(tmp_path):
    # complexity is a score question -> a "score:*" bucket means it is served.
    cal = _FakeCalibrate({"score:1-3": 0.8})
    agent = _StubAgent()
    report = fit_calibration(
        agent,
        _rows(40),
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="m",
        calibrate=cal,
    )
    assert report.per_question["complexity"].served is True
    assert report.per_question["complexity"].n_rows == 40
    assert report.calibration_written is True


def test_calibration_and_meta_written_only_when_something_served(tmp_path):
    cal = _FakeCalibrate({"score:1-3": 0.8})
    agent = _StubAgent()
    fit_calibration(
        agent,
        _rows(40),
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="convaiinnovations/laya",
        calibrate=cal,
    )
    cal_file = tmp_path / "calibration.json"
    meta_file = tmp_path / "meta.json"
    assert cal_file.is_file()
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text())
    assert meta["backend"] == "mlx"
    assert meta["model_id"] == "convaiinnovations/laya"
    assert meta["thresholds"] == {"score:1-3": 0.8}
    assert meta["row_counts"]["complexity"] == 40
    assert "fitted_at" in meta


def test_nothing_served_writes_no_file(tmp_path):
    cal = _FakeCalibrate({})
    agent = _StubAgent()
    fit_calibration(
        agent,
        _rows(5),
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="m",
        calibrate=cal,
    )
    assert not (tmp_path / "calibration.json").exists()
    assert not (tmp_path / "meta.json").exists()
    assert agent.saved_to is None


def test_fitter_crash_degrades_to_zero_shot_not_raise(tmp_path):
    # laya-mlx fit_temperatures does `import torch`, absent in the mlx-only
    # install: once a qtype clears MIN_TYPE_N it raises. The fit must degrade to
    # zero-shot and report why, never crash the command.
    class _TorchlessAgent(_StubAgent):
        def fit_temperatures(self, records, compute_ece=False, seed=0):
            raise ModuleNotFoundError("No module named 'torch'")

    cal = _FakeCalibrate({"score:1-3": 0.8})
    report = fit_calibration(
        _TorchlessAgent(),
        _rows(40),
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="m",
        calibrate=cal,
    )
    assert report.calibration_written is False
    assert report.per_question["complexity"].served is False
    assert "torch" in report.per_question["complexity"].note.lower()
    assert not (tmp_path / "calibration.json").exists()


def test_no_rows_reports_no_label_and_skips_laya(tmp_path):
    cal = _FakeCalibrate({"score:1-3": 0.8})
    agent = _StubAgent()
    report = fit_calibration(
        agent,
        [],
        {"complexity": TASK_TRIAGE["complexity"]},
        tmp_path,
        backend="mlx",
        model_id="m",
        calibrate=cal,
    )
    assert report.per_question["complexity"].n_rows == 0
    assert "no label" in report.per_question["complexity"].note
    assert report.calibration_written is False
    assert cal.records_from_labeled_called is False  # no forward passes on empty data
