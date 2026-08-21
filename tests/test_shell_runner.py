"""The one subprocess call site: gate decision, capture, timeout, failure modes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine
from rudra.shell.runner import CommandResult, _kill_tree, _process_group_kwargs, run_gated


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


def test_the_process_group_kwarg_matches_the_platform():
    """CR-X1: POSIX and Windows spell this differently and neither accepts
    the other's spelling.

    Windows names the POSIX parameter `unused_start_new_session` in its own
    `_execute_child` -- it accepts and silently ignores it -- so passing
    only that left Windows with no process group to kill. Chosen by
    capability (`CREATE_NEW_PROCESS_GROUP` exists only on Windows) rather
    than by `sys.platform`, so the check is the same question as the OS.
    """
    kwargs = _process_group_kwargs()

    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        assert kwargs == {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        assert kwargs == {"start_new_session": True}


def test_a_timeout_kills_the_child_even_without_posix_apis(tmp_path, monkeypatch):
    """CR-X1: `os.killpg` and `os.getpgid` DO NOT EXIST on Windows.

    The old reaper called them inside `except (ProcessLookupError,
    PermissionError)`, which does not catch AttributeError -- so on Windows
    a timeout raised out of run_gated, run_pipeline's blanket handler turned
    it into an internal Rudra error, and the timeout ended the run it
    existed to rescue. Simulated here by removing the POSIX APIs, because
    the suite has never run on Windows (CR-X3).
    """
    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)
    invoked: list[list[str]] = []

    def fake_run(argv, **kwargs):
        invoked.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    try:
        _kill_tree(process)  # must not raise
    finally:
        process.kill()
        process.wait(timeout=5)

    assert invoked and invoked[0][:2] == ["taskkill", "/F"]
