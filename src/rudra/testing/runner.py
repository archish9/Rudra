"""Running the project's tests and reporting what happened.

This is C3.6's deliverable and Step 9's input. The fix loop (C6.5) and the
deterministic completion gate (C6.6) call `run_tests` directly, with no
model in the loop -- which is why the Python function is the primary
artefact and the tool wrapping it is thin (Step 8 spec S8.1).

Gated as `execute`, so under `--auto` without `--allow-shell` it is denied
and no tests run (A1.49). That is correct and stays: pytest executes the
test files the *model* wrote, so it is a genuine arbitrary-code path, not a
lesser one. The consequence -- unattended runs cannot self-verify unless
the user opts into shell -- is recorded in the spec §2.5 rather than worked
around here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated
from rudra.stacks.detect import detect, resolve_test_command
from rudra.state.paths import rudra_paths
from rudra.testing.parse import parse_counts

# Roughly a page of failure output. Enough for a traceback, small enough
# that it does not dominate a 32B context (D6).
MAX_TAIL_CHARS = 8000


@dataclass(frozen=True)
class TestResult:
    """What one test run did.

    `available=False` means the project declares no test command -- a real
    answer, not a failure (stacks/detect.py). `launch_error` is separate on
    purpose: "no test command" and "the command would not start" must never
    look alike to a completion gate, because collapsing them reports a
    broken environment as a project that simply has no tests.
    """

    # pytest collects any class named Test*; without this it warns on every
    # module that imports this one, which is noise that hides real warnings.
    __test__ = False

    available: bool
    command: tuple[str, ...] | None = None
    stack: str | None = None
    exit_code: int | None = None
    passed: bool = False
    total: int | None = None
    failed: int | None = None
    skipped: int | None = None
    output_tail: str = ""
    launch_error: str | None = None
    denied: bool = False
    timed_out: bool = False
    no_tests_collected: bool = False


# pytest's exit code for "collected nothing". Distinct from 1, which means
# tests ran and failed -- and the difference is the whole point (A1.55).
_PYTEST_NO_TESTS_EXIT = 5


def _collected_nothing(stack: str | None, exit_code: int | None, total: int | None) -> bool:
    """Did the runner find no tests at all, as opposed to failing them?

    Step 9's gate consumes `passed`, so without this distinction a project
    whose tests have not been written yet -- or whose collection is
    misconfigured -- looks exactly like one whose tests genuinely fail, and
    the fix loop iterates on correct code guided by output naming no failing
    test. That is an environment problem reported as a code problem, the
    same class as A1.33(b).

    Two signals, because neither covers both cases: pytest's exit 5 is
    definitive but stack-specific, and a parsed total of zero catches the
    runners that exit non-zero with a summary line instead.
    """
    if stack == "python" and exit_code == _PYTEST_NO_TESTS_EXIT:
        return True
    return exit_code != 0 and total == 0


def _tail(text: str, limit: int = MAX_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n{text[-limit:]}"


def _write_log(project_path: Path, text: str) -> None:
    """Full output to .rudra/run/logs/tests.log.

    D17's intent -- big output to a log, short preview returned. C3.2 was
    right to decline building this for tool results, since deepagents evicts
    those; a Python-side call gets no such eviction (see also A1.47).
    """
    logs = rudra_paths(project_path).logs
    try:
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "tests.log").write_text(text, encoding="utf-8")
    except OSError:
        # A log is never worth failing a run over.
        pass


def run_tests(
    project_path: Path,
    *,
    gate: Any,
    console: Console,
    cfg: Any,
    _command_override: list[str] | None = None,
) -> TestResult:
    """Run this project's tests and report what happened.

    `_command_override` is test-only: it exercises the missing-binary and
    timeout paths without depending on the machine lacking a toolchain.
    """
    project_path = Path(project_path)
    profiles = detect(project_path)
    profile = profiles[0] if profiles else None
    stack = profile.name if profile is not None else None

    command = _command_override or (
        resolve_test_command(project_path, profile) if profile is not None else None
    )
    if command is None:
        return TestResult(available=False, stack=stack)

    result = run_gated(
        command,
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=cfg.tools.test_timeout,
        env=scrubbed_env(cfg),
    )

    combined = f"{result.stdout}\n{result.stderr}".strip()
    _write_log(project_path, combined)

    if result.denied:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            denied=True,
            output_tail=result.denial_reason or "denied by the permission gate",
        )

    if result.timed_out:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            timed_out=True,
            output_tail=_tail(combined),
        )

    if result.exit_code is None:
        return TestResult(
            available=True,
            command=result.argv,
            stack=stack,
            launch_error=result.stderr.strip() or "the test command could not be started",
            output_tail=_tail(combined),
        )

    counts = parse_counts(stack, result.stdout, result.stderr)
    nothing_ran = _collected_nothing(stack, result.exit_code, counts.total)
    return TestResult(
        available=True,
        command=result.argv,
        stack=stack,
        exit_code=result.exit_code,
        passed=result.exit_code == 0,
        no_tests_collected=nothing_ran,
        total=counts.total,
        failed=counts.failed,
        skipped=counts.skipped,
        output_tail=_tail(combined),
    )


__all__ = ["MAX_TAIL_CHARS", "TestResult", "run_tests"]
