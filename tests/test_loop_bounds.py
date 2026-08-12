"""Failure signatures: what counts as 'the same failure twice'."""

from __future__ import annotations

from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.verify.result import (
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


def report_with(*stages):
    return VerifyReport.from_stages(stages)


def failing(name="typecheck", findings=(), detail="", tail=""):
    return StageResult(
        name=name,
        outcome=FAILED,
        blocking=True,
        findings=tuple(findings),
        detail=detail,
        output_tail=tail,
    )


def test_the_same_failure_gives_the_same_signature():
    findings = (Finding("a.py", 3, "bad type"),)
    first = report_with(failing(findings=findings))
    second = report_with(failing(findings=findings))
    assert failure_signature(first) == failure_signature(second)


def test_the_output_tail_is_excluded():
    # It carries durations, temp paths and pytest's ordering seed, so
    # including it would mean two identical failures rarely match and the
    # no-progress detector would never fire (spec S9c.3).
    findings = (Finding("a.py", 3, "bad type"),)
    quick = report_with(failing(findings=findings, tail="ran in 0.01s /tmp/aaa"))
    slow = report_with(failing(findings=findings, tail="ran in 9.40s /tmp/zzz"))
    assert failure_signature(quick) == failure_signature(slow)


def test_fixing_one_of_several_findings_changes_the_signature():
    four = [Finding("a.py", n, "bad type") for n in (1, 2, 3, 4)]
    before = report_with(failing(findings=four))
    after = report_with(failing(findings=four[:3]))
    assert failure_signature(before) != failure_signature(after)


def test_moving_a_defect_counts_as_progress():
    before = report_with(failing(findings=(Finding("a.py", 3, "bad type"),)))
    after = report_with(failing(findings=(Finding("a.py", 9, "bad type"),)))
    assert failure_signature(before) != failure_signature(after)


def test_finding_order_does_not_matter():
    one = Finding("a.py", 1, "x")
    two = Finding("b.py", 2, "y")
    assert failure_signature(report_with(failing(findings=(one, two)))) == failure_signature(
        report_with(failing(findings=(two, one)))
    )


def test_a_different_stage_is_a_different_signature():
    findings = (Finding("a.py", 3, "boom"),)
    assert failure_signature(
        report_with(failing(name="typecheck", findings=findings))
    ) != failure_signature(report_with(failing(name="stubs", findings=findings)))


def test_without_findings_the_detail_carries_the_signature():
    # e.g. syntax reports "3 file(s) do not parse" and no Finding list.
    three = report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    two = report_with(failing(name="syntax", detail="2 file(s) do not parse"))
    assert failure_signature(three) != failure_signature(two)
    assert failure_signature(three) == failure_signature(
        report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    )


def test_a_passing_report_has_no_signature():
    passed = report_with(StageResult(name="syntax", outcome=PASSED, blocking=True))
    assert failure_signature(passed) is None


def test_no_test_judgement_is_detected():
    report = report_with(
        StageResult(name="syntax", outcome=PASSED, blocking=True),
        StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            detail="no tests were collected",
        ),
    )
    assert tests_produced_no_judgement(report) is True


def test_a_passing_test_stage_is_a_judgement():
    report = report_with(StageResult(name="test", outcome=PASSED, blocking=True))
    assert tests_produced_no_judgement(report) is False


def test_an_absent_test_stage_is_not_a_missing_judgement():
    # The pipeline short-circuited before tests. That is a blocked run, not
    # a project without tests -- dispatching the tester would be wrong.
    report = report_with(failing(name="syntax"))
    assert tests_produced_no_judgement(report) is False
