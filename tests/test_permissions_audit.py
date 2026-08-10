"""The permission audit log (Step 7 spec §6.7)."""

from __future__ import annotations

import json

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import Decision


def read_lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_denial_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "write_file",
        "/etc/hosts",
        Decision("deny", "<floor:outside-root>", "floor"),
        mode="auto",
        outcome="deny",
    )
    (entry,) = read_lines(log_path)
    assert entry["tool"] == "write_file"
    assert entry["arg"] == "/etc/hosts"
    assert entry["rule"] == "<floor:outside-root>"
    assert entry["mode"] == "auto"
    assert entry["decision"] == "deny"
    assert entry["source"] == "floor"
    assert entry["ts"].endswith("Z") or "+00:00" in entry["ts"]


def test_an_approval_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute",
        "pytest -q",
        Decision("ask", None, "mode-default"),
        mode="ask",
        outcome="approve",
    )
    (entry,) = read_lines(log_path)
    assert entry["decision"] == "approve"
    assert entry["source"] == "prompt"


def test_a_rejection_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute",
        "rm -rf build",
        Decision("ask", None, "mode-default"),
        mode="ask",
        outcome="reject",
    )
    assert read_lines(log_path)[0]["decision"] == "reject"


def test_a_session_grant_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute",
        "pytest -q tests/x",
        Decision("allow", "execute:pytest*", "session-grant"),
        mode="ask",
        outcome="allow",
    )
    entry = read_lines(log_path)[0]
    assert entry["source"] == "session-grant"
    assert entry["rule"] == "execute:pytest*"


def test_a_disabled_floor_rule_is_recorded(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "write_file",
        "/tmp/out.txt",
        Decision("allow", "<floor:outside-root>", "floor-disabled"),
        mode="auto",
        outcome="allow",
    )
    assert read_lines(log_path)[0]["source"] == "floor-disabled"


def test_allowed_reads_are_not_recorded(tmp_path):
    """Hundreds of read_file lines would bury the signal (spec §6.7)."""
    log_path = tmp_path / "permissions.jsonl"
    log = AuditLog(log_path)
    for tool in ("read_file", "ls", "glob", "grep", "update_plan"):
        log.record(
            tool,
            "src/app.py",
            Decision("allow", None, "mode-default"),
            mode="ask",
            outcome="allow",
        )
    assert read_lines(log_path) == []


def test_a_denied_read_is_still_recorded(tmp_path):
    """Silence is for routine reads, not for reads someone blocked."""
    log_path = tmp_path / "permissions.jsonl"
    AuditLog(log_path).record(
        "read_file",
        "/etc/shadow",
        Decision("deny", "read_file:/etc/**", "deny"),
        mode="ask",
        outcome="deny",
    )
    assert len(read_lines(log_path)) == 1


def test_auto_mode_records_every_mutation(tmp_path):
    """The unattended run is the one whose record matters most.

    An earlier version silenced by SOURCE, so in auto mode — where every
    write is an allow from the mode default — the log came out completely
    empty. Found by the Step 7 acceptance run.
    """
    log_path = tmp_path / "permissions.jsonl"
    log = AuditLog(log_path)
    for tool, arg in (
        ("write_file", "src/app.py"),
        ("edit_file", "src/app.py"),
        ("delete", "old.py"),
        ("execute", "pytest -q"),
    ):
        log.record(tool, arg, Decision("allow", None, "mode-default"), mode="auto", outcome="allow")
    assert [entry["tool"] for entry in read_lines(log_path)] == [
        "write_file",
        "edit_file",
        "delete",
        "execute",
    ]


def test_entries_append_rather_than_overwrite(tmp_path):
    log_path = tmp_path / "permissions.jsonl"
    log = AuditLog(log_path)
    for command in ("a", "b", "c"):
        log.record(
            "execute", command, Decision("deny", "execute", "deny"), mode="ask", outcome="deny"
        )
    assert [entry["arg"] for entry in read_lines(log_path)] == ["a", "b", "c"]


def test_the_parent_directory_is_created_on_demand(tmp_path):
    log_path = tmp_path / "run" / "logs" / "permissions.jsonl"
    AuditLog(log_path).record(
        "execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny"
    )
    assert log_path.exists()


def test_an_unwritable_log_warns_and_does_not_raise(tmp_path, capsys):
    """An unwritable log must never abort a run, nor be swallowed silently."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    log = AuditLog(blocked / "permissions.jsonl")
    log.record("execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny")
    assert "audit" in capsys.readouterr().err.lower()


def test_one_write_failure_does_not_silence_later_ones(tmp_path, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    log = AuditLog(blocked / "permissions.jsonl")
    for _ in range(2):
        log.record("execute", "x", Decision("deny", None, "deny"), mode="ask", outcome="deny")
    assert capsys.readouterr().err.lower().count("audit") == 1
