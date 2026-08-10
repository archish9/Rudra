"""Import and --help smoke tests for the CLI (C0.5).

cli.py was at 0% coverage -- no test imported it. These do not test behaviour;
they catch the import-time breakage that unit tests miss, which is exactly the
failure mode Step 3's config refactor risked (a decorator argument evaluated
at import).
"""

from __future__ import annotations

import inspect
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


def test_project_path_is_resolved_before_config_loads() -> None:
    """A5.2: get_config() caches for the process, so if cli.py calls it
    before computing project_path, no later call can correct the .env
    location. This asserts source order because the ordering IS the fix —
    a behavioral test would need a full CLI run against a real backend."""
    from rudra import cli

    # Comments are stripped first. The fix's own explanatory comment names
    # get_config() while describing why it moved, and that mention sits
    # above the code — so an unstripped search finds the comment and reports
    # the very ordering bug the code no longer has.
    source = "\n".join(
        line
        for line in inspect.getsource(cli.main).splitlines()
        if not line.strip().startswith("#")
    )
    get_config_at = source.index("get_config(")
    project_path_at = source.index("project_path = get_project_path(")

    assert project_path_at < get_config_at, (
        "cli.main() must resolve project_path before its first get_config() "
        "call, or --project-dir cannot affect which .env is read (A5.2)."
    )


def test_the_first_get_config_call_passes_the_project_path() -> None:
    """Ordering alone is not enough — the path has to be handed over."""
    from rudra import cli

    assert "get_config(project_path)" in inspect.getsource(cli.main)
