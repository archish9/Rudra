"""Shared helpers for the git and testing suites.

Kept as an importable module rather than a conftest fixture set because the
gate stubs are classes the tests parameterise, and copying them into four
files was the alternative.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine


class AutoGate:
    """auto mode with shell opted in: everything allowed, nothing prompts."""

    def __init__(self, tmp_path: Path) -> None:
        self.engine = PermissionEngine(
            mode="auto",
            allow=(),
            deny=(),
            floor_disable=(),
            project_root=tmp_path,
            shell_in_auto=True,
        )
        self.audit = AuditLog(audit_path(tmp_path))
        self.mode = "auto"

    def prompt(self, requests, console):  # pragma: no cover - auto never asks
        raise AssertionError("auto mode must not prompt")


class DenyShellGate:
    """Bare --auto: filesystem tools yes, commands no (A1.49)."""

    def __init__(self, tmp_path: Path) -> None:
        self.engine = PermissionEngine(
            mode="auto",
            allow=(),
            deny=(),
            floor_disable=(),
            project_root=tmp_path,
            shell_in_auto=False,
        )
        self.audit = AuditLog(audit_path(tmp_path))
        self.mode = "auto"

    def prompt(self, requests, console):  # pragma: no cover
        raise AssertionError("auto mode must not prompt")


class StrictAskGate:
    """ask mode that fails the test if anything prompts."""

    def __init__(self, tmp_path: Path) -> None:
        self.engine = PermissionEngine(
            mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
        )
        self.audit = AuditLog(audit_path(tmp_path))
        self.mode = "ask"

    def prompt(self, requests, console):
        raise AssertionError(f"this call must not prompt: {requests}")


class RecordingAskGate:
    """ask mode that records what it was asked and answers a fixed way."""

    def __init__(self, tmp_path: Path, answer: str = "reject") -> None:
        self.engine = PermissionEngine(
            mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
        )
        self.audit = AuditLog(audit_path(tmp_path))
        self.mode = "ask"
        self.answer = answer
        self.prompted: list[dict] = []

    def prompt(self, requests, console):
        self.prompted.extend(requests)
        return [{"type": self.answer} for _ in requests]


def audit_path(tmp_path: Path) -> Path:
    """An audit log OUTSIDE the repository under test.

    A gate writing `audit.jsonl` into the repo root makes `git status`
    dirty, which silently defeats every clean-tree assertion -- the failure
    that surfaced A1.53.
    """
    outside = tmp_path.parent / f"{tmp_path.name}-audit"
    outside.mkdir(exist_ok=True)
    return outside / "audit.jsonl"


def git(repo: Path, *args: str) -> None:
    """Run a real git command in a fixture repository."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(path: Path) -> Path:
    """An initialised repository with one commit on `main`."""
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "first")
    return path


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
