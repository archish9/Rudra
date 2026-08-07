"""Import and --help smoke tests for the CLI (C0.5).

cli.py was at 0% coverage -- no test imported it. These do not test behaviour;
they catch the import-time breakage that unit tests miss, which is exactly the
failure mode Step 3's config refactor risked (a decorator argument evaluated
at import).
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_module_imports():
    """A broken module-level statement fails here rather than at a user's shell."""
    import rudra.cli

    assert rudra.cli.app is not None


def test_help_exits_zero_and_lists_the_flags():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--verbose" in result.stdout
    assert "--project-dir" in result.stdout


def test_version_reports_the_installed_version():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "0.0.0+unknown" not in result.stdout
