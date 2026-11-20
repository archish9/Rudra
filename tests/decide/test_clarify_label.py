"""clarify_label_row: the needs_clarification training-row builder (OPEN-176).

Pure, primitive-in / dict-out, so it is testable without a RudraAgent. The
honest gold label is a run OUTCOME, so the row is built at run end. ``label``
is the ``asked`` proxy (known leaky, LABEL-SOURCES.md:44); every raw signal is
kept in ``features`` so a later fit can re-derive the gold label.
"""

from rudra.decide.gates import clarify_label_row


def test_none_when_no_work_attempted():
    # A plan-only / no-work run has no outcome to label.
    assert (
        clarify_label_row(
            greenfield=True, asked=False, questions_n=5, run_succeeded=True, attempted=False
        )
        is None
    )


def test_asked_run_is_labelled_true():
    row = clarify_label_row(
        greenfield=True, asked=True, questions_n=5, run_succeeded=True, attempted=True
    )
    assert row["question_id"] == "needs_clarification"
    assert row["stage"] == "clarify"
    assert row["label"] is True
    assert row["pending"] is False
    assert row["features"] == {
        "greenfield": True,
        "asked": True,
        "questions_n": 5,
        "run_converged": True,
    }


def test_unasked_converged_run_is_labelled_false():
    row = clarify_label_row(
        greenfield=False, asked=False, questions_n=0, run_succeeded=True, attempted=True
    )
    assert row["label"] is False
    assert row["features"] == {
        "greenfield": False,
        "asked": False,
        "questions_n": 0,
        "run_converged": True,
    }


def test_coerces_feature_types():
    row = clarify_label_row(greenfield=1, asked=0, questions_n="5", run_succeeded=1, attempted=True)
    f = row["features"]
    assert f["greenfield"] is True and f["asked"] is False
    assert f["questions_n"] == 5 and isinstance(f["questions_n"], int)
    assert f["run_converged"] is True
    assert row["label"] is False
