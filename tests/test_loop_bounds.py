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


def test_max_fix_attempts_must_be_a_positive_whole_number(tmp_path):
    # Zero would make every task block without the coder running once,
    # which reads as a broken model rather than a config mistake.
    from rudra.config import ConfigError, build_config

    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    base = (
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
    )

    for bad in ("0", "-1", "true", '"three"'):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_fix_attempts = {bad}\n", encoding="utf-8"
        )
        try:
            build_config(tmp_path)
        except ConfigError as exc:
            assert "max_fix_attempts" in str(exc)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"max_fix_attempts = {bad} was accepted")

    (rudra / "config.toml").write_text(f"{base}\n[agent]\nmax_fix_attempts = 5\n", encoding="utf-8")
    assert build_config(tmp_path).agent.max_fix_attempts == 5


def test_max_invocation_seconds_must_be_a_whole_number_of_zero_or_more(tmp_path):
    """OPEN-91's bound is configurable because the number that separates a
    runaway from real work is a property of the PROVIDER's latency -- 80
    calls is 4.7 minutes at 3.5 s/call and 61 minutes at 46 s/call.

    0 is legal here and illegal for max_fix_attempts, deliberately: it is
    how a user on a very slow provider says "no time bound" without losing
    the call ceiling, which is a separate guard.
    """
    from rudra.config import ConfigError, build_config

    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    base = (
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
    )

    for bad in ("-1", "true", '"1200"', "12.5"):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_invocation_seconds = {bad}\n", encoding="utf-8"
        )
        try:
            build_config(tmp_path)
        except ConfigError as exc:
            assert "max_invocation_seconds" in str(exc)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"max_invocation_seconds = {bad} was accepted")

    for good in (0, 3600):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_invocation_seconds = {good}\n", encoding="utf-8"
        )
        assert build_config(tmp_path).agent.max_invocation_seconds == good


def test_the_shipped_default_is_the_one_the_runner_uses(tmp_path):
    """Two spellings of one bound is two answers to "why did it stop".
    `subagents/runner.py` reads the config and falls back to its constant
    when there is none, so the two must be the same number."""
    from rudra.config.schema import DEFAULTS
    from rudra.subagents.runner import MAX_INVOCATION_SECONDS

    assert DEFAULTS["agent"]["max_invocation_seconds"] == MAX_INVOCATION_SECONDS
