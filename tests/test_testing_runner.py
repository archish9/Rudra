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


def test_an_empty_suite_is_not_reported_as_a_failure(tmp_path: Path, env: dict):
    """A1.57: pytest exits 5 when it collects nothing, not 1."""
    _python_project(tmp_path)  # no test files at all
    result = run_tests(tmp_path, **env)
    assert result.exit_code == 5
    assert result.no_tests_collected is True
    assert result.passed is False, "nothing ran, so nothing is verified either"


def test_a_real_failure_is_not_mistaken_for_an_empty_suite(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert result.no_tests_collected is False
    assert result.failed == 1


def test_a_passing_suite_is_not_mistaken_for_an_empty_suite(tmp_path: Path, env: dict):
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(tmp_path, **env)
    assert result.no_tests_collected is False
    assert result.passed is True


def _unittest_project(tmp_path: Path, body: str) -> None:
    """A project `_python_test_command` resolves to `unittest discover`.

    Nothing here is contrived: no `.venv`, no `manage.py`, no pytest
    declaration and no `tests/` directory is exactly run8's finished
    project, and `detect.py:374` is the branch it lands on.
    """
    (tmp_path / "test_unit.py").write_text(body, encoding="utf-8")


UNITTEST_ONE_FAILING = """\
import unittest


class T(unittest.TestCase):
    def test_ok(self):
        pass

    def test_bad(self):
        self.fail("boom")
"""

UNITTEST_TWO_PASSING = """\
import unittest


class T(unittest.TestCase):
    def test_a(self):
        pass

    def test_b(self):
        pass
"""


def test_a_failing_unittest_suite_is_not_reported_as_an_empty_suite(tmp_path: Path, env: dict):
    """OPEN-48. This is run8's t5, and it cost that run 790.9s.

    Unparsed output made `total` None, so `_collected_nothing` fell back to
    `exit_code != 0` and called a suite that ran and failed "collected
    nothing" -- sending the coder to hunt a layout problem instead of the
    failing test.
    """
    _unittest_project(tmp_path, UNITTEST_ONE_FAILING)
    result = run_tests(tmp_path, **env)
    assert list(result.command[-2:]) == ["unittest", "discover"], "the branch under test"
    assert result.no_tests_collected is False, "the suite ran; it did not collect nothing"
    assert result.total == 2
    assert result.failed == 1


def test_a_passing_unittest_suite_reports_its_count(tmp_path: Path, env: dict):
    """The count the verdict line prints -- `## test: passed` said nothing."""
    _unittest_project(tmp_path, UNITTEST_TWO_PASSING)
    result = run_tests(tmp_path, **env)
    assert result.passed is True
    assert result.total == 2
    assert result.failed == 0


# --- OPEN-80: an interpreter that cannot run the runner -------------------
#
# Reachable only since OPEN-80. Before it, the test command always ran under
# Rudra's own interpreter, which always has pytest; now it runs under one of
# the user's, which may not.
#
# `_collected_nothing` reads an unparseable non-zero exit as "collected
# nothing", and `test_stage` then tells the coder to look for a missing
# `__init__.py`. That is an environment problem reported as a code problem --
# which is the class `_collected_nothing`'s own docstring says it exists to
# prevent, and OPEN-80's defect one layer down.


def test_a_runner_the_interpreter_cannot_import_is_a_launch_error(tmp_path: Path, env: dict):
    """`python3 -m pytest` with no pytest installed is not "no tests"."""
    _python_project(tmp_path)
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_tests(
        tmp_path,
        _command_override=[sys.executable, "-m", "definitely_not_installed_xyz"],
        **env,
    )
    assert result.launch_error is not None, "the runner was never imported, so nothing ran"
    assert "definitely_not_installed_xyz" in result.launch_error
    assert result.no_tests_collected is False, (
        "a suite that never ran collected nothing by omission"
    )
    assert result.passed is False


def test_a_genuinely_empty_suite_is_still_not_a_launch_error(tmp_path: Path, env: dict):
    """The guard above must not swallow A1.57 -- an empty suite stays empty."""
    _python_project(tmp_path)
    result = run_tests(tmp_path, **env)
    assert result.launch_error is None
    assert result.no_tests_collected is True


def test_an_import_error_inside_a_test_is_not_a_launch_error(tmp_path: Path, env: dict):
    """A project whose OWN dependency is missing is a real failure to report.

    This is the case OPEN-80 exists to surface honestly: run `689f0ea263be`
    saw `No module named 'greenlet'` and could not act on it. It must reach
    the fix loop as a test failure, not be reclassified as a broken runner.
    """
    _python_project(tmp_path)
    (tmp_path / "test_dep.py").write_text(
        "def test_dep():\n    import definitely_not_installed_xyz\n", encoding="utf-8"
    )
    result = run_tests(tmp_path, **env)
    assert result.launch_error is None, "the runner started fine; the project's import failed"
    assert result.passed is False
