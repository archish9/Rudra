"""The test runner, as a tool the model can call.

Thin on purpose. The Python function is what Step 9's loop uses; this exists
so the agent is not blind to test state mid-task (Step 8 spec S8.1). It
returns a short summary rather than the raw dump -- the full output is
already on disk at .rudra/run/logs/tests.log.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from rich.console import Console

from rudra.testing.runner import TestResult
from rudra.testing.runner import run_tests as _run_tests


def summarise(result: TestResult) -> str:
    """One short paragraph for the model.

    Every branch that cannot succeed on retry says so. A denied or
    unavailable run that reads like a transient failure invites the model to
    call it again for the rest of the run.
    """
    if not result.available:
        return (
            "This project declares no test command, so there is nothing to run. "
            "Do not retry — add tests and a test configuration first if the task needs them."
        )
    if result.denied:
        return (
            f"Running tests was not permitted: {result.output_tail}. Do not retry this call. "
            "The user can allow it with --allow-shell or an explicit allow rule."
        )
    if result.timed_out:
        command = " ".join(result.command or ())
        return (
            f"The test command timed out and was killed: {command}. "
            f"Raise [tools] test_timeout if the suite is genuinely slow.\n\n"
            f"Last output:\n{result.output_tail}"
        )
    if result.launch_error is not None:
        command = " ".join(result.command or ())
        return f"The test command could not be started: {result.launch_error}. Command: {command}."

    if result.no_tests_collected:
        # Not a failure. Saying "Tests failed" here sends the model, and
        # Step 9's fix loop, to repair code that is fine (A1.55).
        return (
            "No tests were collected — the suite ran but found nothing to execute. "
            "The code was not exercised, so this is neither a pass nor a failure. "
            "Write the test files first, then call run_tests again."
        )

    headline = "All tests passed." if result.passed else "Tests failed."
    counted = ""
    if result.total is not None:
        counted = f" {result.total} run, {result.failed} failed, {result.skipped} skipped."
    if result.passed:
        return f"{headline}{counted}"
    return f"{headline}{counted}\n\nOutput:\n{result.output_tail}"


def create_testing_tools(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list:
    """The test-running tools for one run. Currently exactly one."""

    @tool
    def run_tests() -> str:
        """Run this project's test suite and report what happened.

        Rudra works out the right command from the project's own layout —
        pytest, cargo test, or the package.json test script. Call this after
        writing code, to check whether it actually works.

        Returns:
            A short summary, plus failure output when there is any.
        """
        return summarise(_run_tests(project_path, gate=gate, console=console, cfg=cfg))

    return [run_tests]


__all__ = ["create_testing_tools", "summarise"]
