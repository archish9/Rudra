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


def _write(root, relative: str, body: str = "def test_x():\n    assert True\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_collecting_nothing_with_test_files_on_disk_is_a_failure(monkeypatch, tmp_path):
    """OPEN-34's second half. `not_applicable` is deliberately non-halting
    (A1.57), so a runner that finds none of the tests sitting on disk passed
    the gate on every task of run6 while executing nothing.

    "No tests exist" and "the runner cannot find the tests that exist" are
    different claims and only the first one is a pass. This one is a broken
    gate, and the fix loop can act on it -- add the missing `__init__.py`,
    move the file, fix the config -- so it blocks without escalating.
    """
    _write(tmp_path, "tests/unit/test_models.py")

    result = stage_for(
        monkeypatch,
        TestResult(
            available=True,
            command=["python3", "-m", "unittest", "discover"],
            exit_code=5,
            passed=False,
            no_tests_collected=True,
        ),
        tmp_path,
    )

    assert result.outcome == FAILED
    assert result.escalate is False
    assert "tests/unit/test_models.py" in result.detail


def test_collecting_nothing_in_a_project_with_no_test_files_stays_not_applicable(
    monkeypatch, tmp_path
):
    """The greenfield case, and the reason A1.57 exists: a project whose
    tests have not been written yet must not send the fix loop to repair
    working code."""
    _write(tmp_path, "app.py", "x = 1\n")

    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True),
        tmp_path,
    )

    assert result.outcome == NOT_APPLICABLE


def test_the_trailing_underscore_test_spelling_counts_as_a_test_file(monkeypatch, tmp_path):
    """`models_test.py` and `api.test.ts` are the same claim as `test_x.py`."""
    _write(tmp_path, "tests/models_test.py")

    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True),
        tmp_path,
    )

    assert result.outcome == FAILED


def test_test_files_inside_a_skipped_directory_do_not_count(monkeypatch, tmp_path):
    """`.venv` and `node_modules` are full of somebody else's test files.
    Counting them would fail the gate of every project holding a
    virtualenv -- the same trap `stacks` skip_dirs exists for."""
    _write(tmp_path, ".venv/lib/site-packages/pytest/test_thing.py")
    _write(tmp_path, "node_modules/lib/index.test.js", "it('x', () => {});\n")

    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True),
        tmp_path,
    )

    assert result.outcome == NOT_APPLICABLE
