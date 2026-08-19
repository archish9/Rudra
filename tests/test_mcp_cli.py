"""`rudra mcp` — the config surface users actually touch."""

import json

from typer.testing import CliRunner

from rudra.cli import app

runner = CliRunner()


def test_add_then_list_then_remove(tmp_path):
    common = ["--project-dir", str(tmp_path)]
    added = runner.invoke(app, ["mcp", "add", "kala", *common, "--", "npx", "-y", "kala-mcp"])
    assert added.exit_code == 0, added.output

    payload = json.loads((tmp_path / ".mcp.json").read_text())
    assert payload["mcpServers"]["kala"]["command"] == "npx"
    assert payload["mcpServers"]["kala"]["args"] == ["-y", "kala-mcp"]

    listed = runner.invoke(app, ["mcp", "list", *common])
    assert "kala" in listed.output and "npx" in listed.output

    removed = runner.invoke(app, ["mcp", "remove", "kala", *common])
    assert removed.exit_code == 0
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"] == {}


def test_removing_an_absent_server_fails_cleanly(tmp_path):
    result = runner.invoke(app, ["mcp", "remove", "ghost", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_list_shows_disabled_servers_as_disabled(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kala": {"command": "npx"}}}), encoding="utf-8"
    )
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[mcp]\ndisabled_servers = ["kala"]\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["mcp", "list", "--project-dir", str(tmp_path)])
    assert "disabled" in result.output.lower()


def test_test_reports_a_server_that_cannot_start(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"ghost": {"command": "rudra-no-such-binary"}}}), encoding="utf-8"
    )
    result = runner.invoke(app, ["mcp", "test", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_doctor_names_each_server_and_flags_a_missing_binary(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"kala": {"command": "rudra-no-such-binary"}}}), encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor", "--offline", "--project-dir", str(tmp_path)])
    assert "kala" in result.output
    assert "not on PATH" in result.output


def test_doctor_warns_about_a_policy_naming_an_unconfigured_server(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[mcp]\ndisabled_servers = ["ghost"]\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor", "--offline", "--project-dir", str(tmp_path)])
    assert "ghost" in result.output
