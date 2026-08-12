"""VerifyReport's verdict derivation — the one piece of logic in the data layer."""

from __future__ import annotations

from rudra.verify.result import (
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    StageResult,
    VerifyReport,
)


def stage(name, outcome, *, blocking=True, escalate=False):
    return StageResult(name=name, outcome=outcome, blocking=blocking, escalate=escalate)


def test_all_passed_is_a_pass():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("test", PASSED)])
    assert report.passed is True
    assert report.blocker is None
    assert report.escalate is False


def test_a_blocking_failure_blocks():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    assert report.passed is False
    assert report.blocker.name == "typecheck"
    assert report.escalate is False


def test_a_failing_advisory_stage_never_blocks():
    report = VerifyReport.from_stages(
        [stage("lint", FAILED, blocking=False), stage("test", PASSED)]
    )
    assert report.passed is True
    assert report.blocker is None


def test_not_applicable_never_blocks():
    report = VerifyReport.from_stages([stage("typecheck", NOT_APPLICABLE), stage("test", PASSED)])
    assert report.passed is True


def test_missing_tool_blocks_and_escalates():
    report = VerifyReport.from_stages([stage("typecheck", MISSING_TOOL, escalate=True)])
    assert report.passed is False
    assert report.escalate is True


def test_denied_blocks_and_escalates():
    report = VerifyReport.from_stages([stage("test", DENIED, escalate=True)])
    assert report.passed is False
    assert report.escalate is True


def test_internal_error_escalates_while_reporting_failed():
    # An internal error is a bug in Rudra, not in the user's code. It must not
    # reach the fix loop, so it escalates despite reporting `failed`.
    report = VerifyReport.from_stages([stage("lint", FAILED, blocking=False, escalate=True)])
    assert report.escalate is False, "advisory stages never become the blocker"

    report = VerifyReport.from_stages([stage("typecheck", FAILED, escalate=True)])
    assert report.passed is False
    assert report.escalate is True


def test_the_first_blocking_failure_is_the_blocker():
    report = VerifyReport.from_stages([stage("syntax", FAILED), stage("typecheck", FAILED)])
    assert report.blocker.name == "syntax"
