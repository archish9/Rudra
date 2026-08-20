"""`rudra --version` must not pay for deepagents (C9.8 / A1.94).

Asserted on the import graph, not the clock. A wall-clock threshold is
flaky on CI and measures the machine; the import is the actual invariant,
and D16 already settled that this cost *is* the import -- it cannot be
engineered away in another language, only deferred.

Every one of these runs a real subprocess, because the whole question is
what a fresh interpreter loads.
"""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import sys
from typer.testing import CliRunner
from rudra.cli import app
result = CliRunner().invoke(app, {args!r})
print("EXIT:%d" % result.exit_code)
print("MODULES:" + ",".join(sorted(sys.modules)))
"""


def _modules_after(args: list[str]) -> set[str]:
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE.format(args=args)],
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(x for x in completed.stdout.splitlines() if x.startswith("MODULES:"))
    return set(line.removeprefix("MODULES:").split(","))


def test_version_does_not_import_deepagents():
    loaded = _modules_after(["--version"])

    assert "deepagents" not in loaded
    assert not any(name.startswith("langchain.agents") for name in loaded)


def test_version_does_not_import_the_agent_package():
    assert "rudra.agent.main_agent" not in _modules_after(["--version"])


def test_help_does_not_import_deepagents():
    """`--help` is what shell completion and every first-time user hits."""
    assert "deepagents" not in _modules_after(["--help"])


def test_config_list_does_not_import_deepagents():
    """Reading configuration has nothing to do with building an agent."""
    assert "deepagents" not in _modules_after(["config", "list"])
