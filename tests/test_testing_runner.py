"""run_tests: what Step 9's fix loop and completion gate actually consume."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.state.paths import rudra_paths
from rudra.testing.runner import MAX_TAIL_CHARS, run_tests
from tests.conftest_git import AutoGate, DenyShellGate


@pytest.fixture
def env(tmp_path: Path) -> dict:
    return {
        "gate": AutoGate(tmp_path),
        "console": Console(),
        "cfg": build_config(tmp_path),
    }


def _python_project(tmp_path: Path) -> None:
    """A project whose tests run under the interpreter running this suite.

    The `.venv/bin/pytest` layout is what `resolve_test_command` looks for,
    and pointing it at this suite's own pytest is legitimate here: in this
    fixture the temp directory genuinely is the project.

    An exec shim rather than a symlink. A symlinked `python` makes CPython
    compute sys.prefix from the LINK's location, so it would look for
    site-packages under the temp venv and report "No module named pytest".
    Exec'ing keeps the real interpreter's own prefix.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True)
    shim = binaries / "pytest"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m pytest "$@"\n', encoding="utf-8")
    shim.chmod(0o755)


def test_a_project_with_no_test_command_is_unavailable_not_failed(tmp_path: Path, env: dict):
    (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert result.available is False
    assert result.passed is False
    assert result.launch_error is None, "no command declared is not a launch failure"


def test_a_greenfield_directory_is_unavailable(tmp_path: Path, env: dict):
    result = run_tests(tmp_path, **env)
    assert result.available is False
    assert result.stack is None


def test_a_passing_python_suite_is_reported_as_passed(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert result.available is True
    assert result.stack == "python"
    assert result.exit_code == 0
    assert result.passed is True
    assert result.failed == 0
    assert result.total == 1


def test_a_failing_python_suite_reports_counts_and_a_tail(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_bad.py").write_text(
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert 2 + 2 == 5\n",
        encoding="utf-8",
    )
    result = run_tests(tmp_path, **env)
    assert result.passed is False
    assert result.exit_code != 0
    assert result.failed == 1
    assert "test_bad" in result.output_tail


def test_full_output_is_written_to_the_run_log(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    run_tests(tmp_path, **env)
    log_file = rudra_paths(tmp_path).logs / "tests.log"
    assert log_file.exists()
    assert "test_ok" in log_file.read_text(encoding="utf-8")


def test_the_tail_is_capped(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    body = "\n".join(f"def test_n{n}():\n    assert True\n" for n in range(400))
    (tmp_path / "test_many.py").write_text(body, encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert len(result.output_tail) <= MAX_TAIL_CHARS + 200


def test_a_missing_binary_is_a_launch_error_not_an_absent_suite(tmp_path: Path, env: dict):
    """'no tests' and 'could not start' must never look alike to the gate."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    result = run_tests(tmp_path, _command_override=["not-a-real-binary-xyz"], **env)
    assert result.available is True
    assert result.launch_error is not None
    assert result.passed is False
    assert result.exit_code is None


def test_a_denied_run_is_reported_as_denied(tmp_path: Path):
    """A1.49: bare --auto runs no commands, so it verifies nothing."""
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(
        tmp_path,
        gate=DenyShellGate(tmp_path),
        console=Console(),
        cfg=build_config(tmp_path),
    )
    assert result.denied is True
    assert result.passed is False
    assert result.available is True, "the suite exists; it was the run that was refused"


def test_a_timeout_is_reported_and_is_not_a_pass(tmp_path: Path):
    """[tools] test_timeout is what stops a hung suite stalling the fix loop."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text("[tools]\ntest_timeout = 1\n", encoding="utf-8")

    cfg = build_config(tmp_path)
    assert cfg.tools.test_timeout == 1

    result = run_tests(
        tmp_path,
        gate=AutoGate(tmp_path),
        console=Console(),
        cfg=cfg,
        _command_override=[sys.executable, "-c", "import time; time.sleep(30)"],
    )
    assert result.timed_out is True
    assert result.passed is False
    assert result.exit_code is None


def test_the_command_used_is_reported_back(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert result.command is not None
    assert "pytest" in " ".join(result.command)


def test_rust_is_detected_even_when_cargo_is_not_run(tmp_path: Path, env: dict):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    result = run_tests(tmp_path, _command_override=["not-a-real-binary-xyz"], **env)
    assert result.stack == "rust"
