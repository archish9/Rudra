"""The run_tests tool: a short summary the model can act on."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from rudra.config.loader import build_config
from rudra.testing.runner import TestResult
from rudra.tools.testing_tools import create_testing_tools, summarise
from tests.conftest_git import AutoGate, DenyShellGate


def _python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True)
    shim = binaries / "pytest"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pytest "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


def _tool(project_path: Path, gate):
    tools = create_testing_tools(
        project_path, gate=gate, console=Console(), cfg=build_config(project_path)
    )
    assert [t.name for t in tools] == ["run_tests"]
    return tools[0]


def test_run_tests_tool_reports_a_passing_suite(tmp_path: Path):
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    text = _tool(tmp_path, AutoGate(tmp_path)).invoke({})
    assert "passed" in text.lower()
    assert "1 run" in text


def test_run_tests_tool_reports_failures_with_the_tail(tmp_path: Path):
    _python_project(tmp_path)
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    text = _tool(tmp_path, AutoGate(tmp_path)).invoke({})
    assert "failed" in text.lower()
    assert "test_bad" in text


def test_run_tests_tool_says_so_when_there_is_no_suite(tmp_path: Path):
    text = _tool(tmp_path, AutoGate(tmp_path)).invoke({})
    assert "no test command" in text.lower()


def test_run_tests_tool_surfaces_a_denial_as_guidance(tmp_path: Path):
    """A denial that reads as transient invites retrying for the whole run."""
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    text = _tool(tmp_path, DenyShellGate(tmp_path)).invoke({})
    assert "not permitted" in text.lower()
    assert "do not retry" in text.lower()
    assert "--allow-shell" in text


def test_every_unretryable_outcome_says_not_to_retry():
    """Unavailable and denied cannot succeed on a second call."""
    assert "do not retry" in summarise(TestResult(available=False)).lower()
    assert (
        "do not retry"
        in summarise(TestResult(available=True, denied=True, output_tail="nope")).lower()
    )


def test_a_launch_error_is_not_reported_as_no_tests():
    """The distinction TestResult keeps must survive into the summary."""
    absent = summarise(TestResult(available=False))
    broken = summarise(
        TestResult(available=True, command=("cargo", "test"), launch_error="not found")
    )
    assert absent != broken
    assert "could not be started" in broken


def test_a_timeout_summary_names_the_knob():
    text = summarise(
        TestResult(available=True, command=("pytest",), timed_out=True, output_tail="...")
    )
    assert "timed out" in text.lower()
    assert "test_timeout" in text


def test_a_passing_summary_does_not_dump_output():
    """Nothing to fix means nothing to read."""
    text = summarise(
        TestResult(
            available=True,
            exit_code=0,
            passed=True,
            total=3,
            failed=0,
            skipped=0,
            output_tail="a" * 5000,
        )
    )
    assert "aaaa" not in text


def test_an_empty_suite_summary_is_neither_pass_nor_failure():
    """A1.55: 'Tests failed' would send the fix loop after working code."""
    text = summarise(TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True))
    assert "no tests were collected" in text.lower()
    assert "failed" not in text.lower()
