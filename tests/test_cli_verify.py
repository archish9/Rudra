"""The verify command: flags, mutual exclusion, exit codes."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from rudra.cli import app

runner = CliRunner()


def project(tmp_path, source, *, allow_shell=False):
    """A minimal Python project.

    `allow_shell` opts the command stages in. Without it every command is
    denied — CliRunner has no TTY, so `mode = "ask"` auto-rejects — and the
    run escalates at lint before the later stages can be observed. That is
    correct behaviour, and one test below asserts it directly.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    if allow_shell:
        rudra = tmp_path / ".rudra"
        rudra.mkdir(exist_ok=True)
        (rudra / "config.toml").write_text(
            '[permissions]\nmode = "auto"\n\n[tools]\nshell_in_auto = true\n', encoding="utf-8"
        )
    return tmp_path


def test_all_and_changed_are_mutually_exclusive(tmp_path):
    project(tmp_path, "x = 1\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--all", "--changed", "a.py"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_a_syntax_error_exits_one(tmp_path):
    project(tmp_path, "def broken(\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py"])
    assert result.exit_code == 1
    assert "syntax" in result.output


def test_json_output_is_parseable(tmp_path):
    project(tmp_path, "def broken(\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py", "--json"])
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert payload["blocker"] == "syntax"


def test_a_stub_exits_one_and_names_the_file(tmp_path):
    # The whole point of C6.6: every other stage is clean and the gate
    # still refuses, because a placeholder is not a finished task.
    project(tmp_path, "def handler():\n    pass\n", allow_shell=True)
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py"])
    assert result.exit_code == 1
    assert "a.py" in result.output
    assert "stubs" in result.output


def test_denied_commands_exit_two_not_one(tmp_path):
    # No TTY means `mode = "ask"` cannot prompt, so the command stages are
    # rejected. That needs a human, not a fix loop — exit 2, not 1.
    project(tmp_path, "x = 1\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py"])
    assert result.exit_code == 2
    assert "denied" in result.output
