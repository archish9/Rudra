"""The one subprocess call site: gate decision, capture, timeout, failure modes."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine
from rudra.shell.runner import CommandResult, run_gated


class _Gate:
    """A real engine and a real audit log; only the prompt is scripted."""

    def __init__(
        self, tmp_path: Path, mode: str, answer: str = "approve", shell_in_auto: bool = True
    ) -> None:
        self.engine = PermissionEngine(
            mode=mode,
            allow=(),
            deny=(),
            floor_disable=(),
            project_root=tmp_path,
            shell_in_auto=shell_in_auto,
        )
        self.audit = AuditLog(tmp_path / "audit.jsonl")
        self.mode = mode
        self.answer = answer
        self.prompted: list[dict] = []

    def prompt(self, requests, console):
        self.prompted.extend(requests)
        return [{"type": self.answer} for _ in requests]


def _echo(text: str) -> list[str]:
    return [sys.executable, "-c", f"print({text!r})"]


def test_allowed_command_runs_and_captures_stdout(tmp_path: Path):
    result = run_gated(
        _echo("hello"),
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert result.exit_code == 0
    assert result.ok is True
    assert "hello" in result.stdout
    assert result.denied is False


def test_allowed_command_is_audited_with_the_real_command_string(tmp_path: Path):
    gate = _Gate(tmp_path, "auto")
    run_gated(_echo("hi"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    logged = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert '"tool": "execute"' in logged
    assert "-c" in logged, "the audit line records the command, not a tool name"


def test_denied_command_never_runs(tmp_path: Path):
    marker = tmp_path / "should-not-exist.txt"
    gate = _Gate(tmp_path, "plan")  # plan mode denies every mutating tool
    result = run_gated(
        [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"],
        cwd=tmp_path,
        gate=gate,
        console=Console(),
        timeout=30,
    )
    assert result.denied is True
    assert result.exit_code is None
    assert marker.exists() is False


def test_auto_without_shell_opt_in_denies(tmp_path: Path):
    """A1.49: bare --auto runs no commands, so no tests either."""
    gate = _Gate(tmp_path, "auto", shell_in_auto=False)
    result = run_gated(_echo("nope"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    assert result.denied is True
    assert result.stdout == ""


def test_ask_mode_prompts_once_and_runs_on_approve(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="approve")
    result = run_gated(_echo("ok"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    assert len(gate.prompted) == 1
    assert gate.prompted[0]["name"] == "execute"
    assert "-c" in gate.prompted[0]["args"]["command"]
    assert result.exit_code == 0


def test_ask_mode_reject_returns_denied_and_does_not_run(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="reject")
    result = run_gated(_echo("nope"), cwd=tmp_path, gate=gate, console=Console(), timeout=30)
    assert result.denied is True
    assert result.stdout == ""


def test_read_only_bypasses_the_prompt_entirely(tmp_path: Path):
    gate = _Gate(tmp_path, "ask", answer="reject")
    result = run_gated(
        _echo("read"), cwd=tmp_path, gate=gate, console=Console(), timeout=30, read_only=True
    )
    assert gate.prompted == []
    assert result.exit_code == 0


def test_missing_binary_is_a_value_not_an_exception(tmp_path: Path):
    result = run_gated(
        ["definitely-not-a-real-binary-xyz"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert isinstance(result, CommandResult)
    assert result.exit_code is None
    assert result.timed_out is False
    assert result.denied is False
    assert "definitely-not-a-real-binary-xyz" in result.stderr


def test_timeout_kills_the_command_and_reports_it(tmp_path: Path):
    result = run_gated(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=1,
    )
    assert result.timed_out is True
    assert result.exit_code is None


def test_non_zero_exit_is_not_an_error(tmp_path: Path):
    result = run_gated(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert result.exit_code == 3
    assert result.ok is False
    assert result.denied is False


def test_stderr_is_captured_separately(tmp_path: Path):
    result = run_gated(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom')"],
        cwd=tmp_path,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert "boom" in result.stderr
    assert "boom" not in result.stdout


def test_the_command_runs_in_the_given_directory(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    result = run_gated(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=work,
        gate=_Gate(tmp_path, "auto"),
        console=Console(),
        timeout=30,
    )
    assert str(work.resolve()) in result.stdout


def test_a_deny_rule_matches_the_rendered_command(tmp_path: Path):
    """Rules are written against the command string run_gated renders."""
    gate = _Gate(tmp_path, "auto")
    gate.engine = PermissionEngine(
        mode="auto",
        allow=(),
        deny=("execute:*definitely-blocked*",),
        floor_disable=(),
        project_root=tmp_path,
        shell_in_auto=True,
    )
    result = run_gated(
        _echo("definitely-blocked"),
        cwd=tmp_path,
        gate=gate,
        console=Console(),
        timeout=30,
    )
    assert result.denied is True
    assert "definitely-blocked" in (result.denial_reason or "")
