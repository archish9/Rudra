"""TestResult -> StageResult. The mapping A1.57 exists to protect."""

from __future__ import annotations

from rudra.testing.runner import TestResult
from rudra.verify import pipeline
from rudra.verify.result import DENIED, FAILED, MISSING_TOOL, NOT_APPLICABLE, PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def stage_for(monkeypatch, result, tmp_path):
    monkeypatch.setattr(pipeline, "run_tests", lambda *a, **k: result)
    return pipeline.test_stage(tmp_path, gate=object(), console=None, cfg=FakeCfg())


def test_passing_tests_pass(monkeypatch, tmp_path):
    result = stage_for(monkeypatch, TestResult(available=True, exit_code=0, passed=True), tmp_path)
    assert result.outcome == PASSED


def test_failing_tests_fail_without_escalating(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=1, passed=False, total=3, failed=1, output_tail="E"),
        tmp_path,
    )
    assert result.outcome == FAILED
    assert result.escalate is False
    assert result.output_tail == "E"


def test_no_test_command_is_not_applicable(monkeypatch, tmp_path):
    result = stage_for(monkeypatch, TestResult(available=False), tmp_path)
    assert result.outcome == NOT_APPLICABLE


def test_no_tests_collected_is_not_a_failure(monkeypatch, tmp_path):
    # pytest exits 5 when it collects nothing. Reading that as "tests
    # failed" sends the fix loop to repair working code -- A1.57.
    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True),
        tmp_path,
    )
    assert result.outcome == NOT_APPLICABLE
    assert "collected" in result.detail


def test_denied_tests_escalate(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, denied=True, output_tail="<auto:shell-not-opted-in>"),
        tmp_path,
    )
    assert result.outcome == DENIED
    assert result.escalate is True


def test_a_launch_error_is_a_missing_tool(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, launch_error="pytest: not found"),
        tmp_path,
    )
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True


def test_a_timed_out_suite_fails_without_escalating(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch, TestResult(available=True, timed_out=True, output_tail="hung"), tmp_path
    )
    assert result.outcome == FAILED
    assert result.escalate is False
