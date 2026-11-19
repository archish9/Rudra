"""The decision_label training-row stub (S2 Task 3).

Decisions whose ground-truth label is NOT derivable from today's archive
(#1 failure_class, #2 needs_clarification, #3 needs_tests — see LABEL-SOURCES.md)
accrue rows through this emitter so S3 can fit them later. Metadata only; one
line on the `rudra.decide` logger, promoted into debug-<id>.jsonl.
"""

import logging

from rudra.decide import log as decide_log


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        self.events.append(getattr(record, "event", None))


def _attach():
    cap = _Capture()
    logger = logging.getLogger("rudra.decide")
    logger.addHandler(cap)
    logger.setLevel(logging.INFO)
    return cap, logger


def test_record_label_emits_a_decision_label_event():
    cap, logger = _attach()
    try:
        decide_log.record_label(
            question_id="needs_tests",
            stage="pre_dispatch",
            features={"domain": "python", "greenfield": True},
            label=False,
        )
    finally:
        logger.removeHandler(cap)
    assert len(cap.events) == 1
    e = cap.events[0]
    assert e["kind"] == decide_log.LABEL_EVENT == "decision_label"
    assert e["question_id"] == "needs_tests"
    assert e["stage"] == "pre_dispatch"
    assert e["features"] == {"domain": "python", "greenfield": True}
    assert e["label"] is False
    assert e["pending"] is False


def test_record_label_marks_a_pending_row_when_label_unknown():
    cap, logger = _attach()
    try:
        decide_log.record_label(
            question_id="failure_class",
            stage="loop_recovery",
            features={"attempts": 4},
            label=None,
            pending=True,
        )
    finally:
        logger.removeHandler(cap)
    e = cap.events[0]
    assert e["pending"] is True
    assert e["label"] is None


def test_record_label_scalarizes_non_scalar_feature_values():
    cap, logger = _attach()
    try:
        decide_log.record_label(
            question_id="q",
            stage="s",
            features={"paths": ["a.py", "b.py"]},  # non-scalar -> str
            label="x",
        )
    finally:
        logger.removeHandler(cap)
    assert isinstance(cap.events[0]["features"]["paths"], str)


def test_record_label_never_raises_on_a_bad_logger(monkeypatch):
    # Bookkeeping may never end a run.
    def boom(*a, **k):
        raise RuntimeError("logger down")

    monkeypatch.setattr(decide_log._logger, "info", boom)
    # must not raise
    decide_log.record_label(question_id="q", stage="s", features={}, label=True)
