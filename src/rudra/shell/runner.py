"""The only place in Rudra that starts a subprocess.

Both Step 8 consumers -- git (C3.5) and the test runner (C3.6) -- need the
same primitive: run a command, through the permission gate, and capture what
it said. Two copies of a gate check is the thing that drifts, and the half
that drifts is the security-relevant one, so there is one copy.

Every command reaches the gate as `execute` with its real command string
(Step 8 spec S8.2). Three properties follow: users keep writing
`allow = ["execute:pytest*"]` with no new vocabulary, the audit log records
the actual command rather than an opaque tool name, and A1.49's unattended
shell opt-in covers these tools automatically -- correctly, because they are
shell.

Nothing here raises for a denial, a non-zero exit, a timeout, or a missing
binary. Every one of those is a value the caller acts on.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass(frozen=True)
class CommandResult:
    """What one command did.

    `exit_code` is None whenever the command never produced one: denied,
    timed out, or the binary was not found. Callers tell those apart via
    `denied` and `timed_out`.
    """

    argv: tuple[str, ...]
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    denied: bool = False
    denial_reason: str | None = None
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _denied(argv: tuple[str, ...], command: str, reason: str) -> CommandResult:
    return CommandResult(
        argv=argv,
        command=command,
        exit_code=None,
        stdout="",
        stderr="",
        denied=True,
        denial_reason=reason,
    )


def _permitted(
    argv: tuple[str, ...], command: str, gate: Any, console: Console
) -> CommandResult | None:
    """None when the command may run; a denied CommandResult when it may not."""
    decision = gate.engine.decide("execute", {"command": command})

    if decision.effect == "deny":
        gate.audit.record("execute", command, decision, mode=gate.mode, outcome="deny")
        return _denied(argv, command, decision.rule or "denied by the permission gate")

    if decision.effect == "ask":
        # Gate.prompt audits whatever the user chose, so this branch
        # deliberately records nothing itself -- doing both would double-log
        # the one decision a human actually made.
        answers = gate.prompt([{"name": "execute", "args": {"command": command}}], console)
        if not answers or answers[0].get("type") != "approve":
            return _denied(argv, command, "the user rejected this command")
        return None

    gate.audit.record("execute", command, decision, mode=gate.mode, outcome="allow")
    return None


# How long to wait for a killed process's pipes to close before giving up on
# its output. Seconds, not minutes: by this point the process has been
# SIGKILLed and we are only draining what it already wrote.
_REAP_TIMEOUT = 5


def run_gated(
    argv: Sequence[str],
    *,
    cwd: Path,
    gate: Any,
    console: Console,
    timeout: int,
    env: dict[str, str] | None = None,
    read_only: bool = False,
) -> CommandResult:
    """Run `argv` in `cwd`, subject to the permission gate.

    `read_only=True` skips the decision. It is for commands Rudra itself
    composes from a fixed set that only reads -- `git status`, `git log` --
    where prompting would fire several times before the planner even starts.
    It is consistent with policy already in force: reads are never gated
    (permissions/rules.py) and are never a floor violation
    (permissions/floor.py). Never pass it for anything a model composed, or
    anything that writes.
    """
    resolved = tuple(str(part) for part in argv)
    command = shlex.join(resolved)

    if not read_only and gate is not None:
        refusal = _permitted(resolved, command, gate, console)
        if refusal is not None:
            return refusal

    try:
        process = subprocess.Popen(
            resolved,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Lenient decoding, deliberately. `text=True` alone decodes
            # strict UTF-8, so a single latin-1 byte from a linter, a test
            # suite, or a commit subject raised UnicodeDecodeError out of
            # communicate(). Inside run_pipeline the blanket `except
            # Exception` then turned the user's byte into an *internal Rudra
            # error* with escalate=True and stopped the run; outside it
            # (changed_files_from_git, git_snapshot) it was an uncaught
            # traceback. Every consumer here already treats this as
            # best-effort text (CR-E7).
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        return CommandResult(resolved, command, None, "", f"{resolved[0]}: {exc}")

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # start_new_session put the child in its own process group, so the
        # whole tree dies. Killing only the child would leave a test
        # runner's workers alive -- the Karma case C11.3 describes.
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            process.kill()
        try:
            # Bounded, because the SIGKILL above is not a guarantee: a
            # grandchild that called setsid() itself is outside the group we
            # just killed and keeps the inherited pipe open, so an unbounded
            # communicate() here hangs the run that `test_timeout` exists to
            # end (CR-B9).
            stdout, stderr = process.communicate(timeout=_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - needs a stray grandchild
            stdout, stderr = "", ""
        return CommandResult(resolved, command, None, stdout, stderr, timed_out=True)

    return CommandResult(resolved, command, process.returncode, stdout, stderr)


__all__ = ["CommandResult", "run_gated"]
