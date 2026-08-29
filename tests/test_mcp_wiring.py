"""MCP in a real run: built once, mounted where declared, closed at the end.

Uses the same hermetic pattern as test_agent_wiring.py — a tmp cwd plus a
config reset — because `create_main_agent` reaches the process-global
`get_config()` (A1.52).
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rudra.agent.main_agent import create_main_agent
from rudra.config.loader import reset_config

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


def project(tmp_path, servers=None, config=""):
    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    body = '[permissions]\nmode = "auto"\n' + config
    (tmp_path / ".rudra" / "config.toml").write_text(body, encoding="utf-8")
    if servers is not None:
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return tmp_path


async def test_no_mcp_json_means_no_client(tmp_path):
    agent = await create_main_agent(project(tmp_path), "task")
    try:
        assert agent.mcp is None
    finally:
        await agent.close()


async def test_a_configured_server_reaches_the_subagent_context(tmp_path):
    root = project(tmp_path, {"echo": {"command": "true"}})
    agent = await create_main_agent(root, "task")
    try:
        assert agent.mcp is not None
        assert agent.mcp.servers == ("echo",)
        assert agent._loop_context.subagents.mcp is agent.mcp
    finally:
        await agent.close()


async def test_a_disabled_server_is_not_loaded(tmp_path):
    root = project(
        tmp_path,
        {"echo": {"command": "true"}, "other": {"command": "true"}},
        config='\n[mcp]\ndisabled_servers = ["other"]\n',
    )
    agent = await create_main_agent(root, "task")
    try:
        assert agent.mcp.servers == ("echo",)
    finally:
        await agent.close()


async def test_malformed_mcp_json_warns_and_the_run_continues(tmp_path, capfd):
    # capfd, not capsys: capsys swaps sys.stderr for an object with no
    # fileno, and `mcp.client.stdio.stdio_client` binds `errlog=sys.stderr`
    # as a default argument at import time (sessions.py:106). A test that
    # imports it under capsys poisons every later server spawn in the same
    # process with "UnsupportedOperation: fileno". See TODO.md A1.84.
    root = project(tmp_path)
    (root / ".mcp.json").write_text("{oops", encoding="utf-8")
    agent = await create_main_agent(root, "task")
    try:
        assert agent.mcp is None
    finally:
        await agent.close()
    assert "mcp" in capfd.readouterr().out.lower()


async def test_mcp_disabled_in_config_beats_a_present_file(tmp_path):
    root = project(tmp_path, {"echo": {"command": "true"}}, config="\n[mcp]\nenabled = false\n")
    agent = await create_main_agent(root, "task")
    try:
        assert agent.mcp is None
    finally:
        await agent.close()


def test_only_mcp_subagents_are_told_servers_exist(tmp_path):
    from rudra.config.loader import build_config
    from rudra.mcp.client import McpClient
    from rudra.mcp.config import ServerEntry
    from rudra.subagents.build import _prompt_for
    from rudra.subagents.registry import CODER, TESTER

    entry = ServerEntry(name="kalatest", transport="stdio", command=sys.executable, args=(FIXTURE,))
    # project_path is a required SubagentContext field (runner.py:73); the
    # coder's PROJECT FILES block reads it (OPEN-39).
    ctx = SimpleNamespace(
        cfg=build_config(tmp_path), facts=None, mcp=McpClient([entry]), project_path=tmp_path
    )
    assert "kalatest" in _prompt_for(CODER, ctx)
    assert "kalatest" not in _prompt_for(TESTER, ctx)
