"""`rudra --continue` and its refusals (Step 12c, C7.2)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from rudra.cli import app


def _ledger(tmp_path, tasks, request="build a JSON parser"):
    path = tmp_path / ".rudra" / "run"
    path.mkdir(parents=True, exist_ok=True)
    (path / "ledger.json").write_text(
        json.dumps({"request": request, "saved_at": "2026-08-19T00:00:00+00:00", "tasks": tasks}),
        encoding="utf-8",
    )


def test_continue_without_a_ledger_exits_with_a_sentence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["--continue", "--auto"])

    assert result.exit_code == 2
    assert "no previous run" in result.stdout


def test_continue_with_a_different_request_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, [{"id": "t1", "description": "parse", "status": "pending"}])

    result = CliRunner().invoke(app, ["--continue", "--auto", "build a YAML parser"])

    assert result.exit_code == 2
    assert "different request" in result.stdout


def test_continue_with_nothing_pending_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, [{"id": "t1", "description": "parse", "status": "done"}])

    result = CliRunner().invoke(app, ["--continue", "--auto"])

    assert result.exit_code == 2
    assert "nothing pending" in result.stdout


def test_a_refusal_never_reaches_a_model(tmp_path, monkeypatch):
    """The refusal must land before any agent is built or any key is read."""
    monkeypatch.chdir(tmp_path)

    import rudra.cli as cli

    def explode(*args, **kwargs):
        raise AssertionError("create_main_agent must not be reached on a refusal")

    monkeypatch.setattr(cli, "create_main_agent", explode)
    result = CliRunner().invoke(app, ["--continue", "--auto"])

    assert result.exit_code == 2
