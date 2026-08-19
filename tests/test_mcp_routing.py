"""Which subagent may reach MCP, and the reviewer's no-write invariant."""

import sys
from pathlib import Path
from types import SimpleNamespace

from rudra.config.loader import build_config
from rudra.mcp.client import McpClient
from rudra.mcp.config import ServerEntry
from rudra.subagents.build import _tools_for
from rudra.subagents.registry import CODER, GENERAL_PURPOSE, REVIEWER, TESTER

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


def context(tmp_path, cfg):
    entry = ServerEntry(name="echo", transport="stdio", command=sys.executable, args=(FIXTURE,))
    return SimpleNamespace(
        project_path=tmp_path,
        gate=None,
        console=None,
        cfg=cfg,
        mcp=McpClient([entry]),
    )


def names(tools):
    return {tool.name for tool in tools}


def test_specs_declare_their_access():
    assert CODER.wants_mcp and not CODER.mcp_readonly
    assert GENERAL_PURPOSE.wants_mcp and not GENERAL_PURPOSE.mcp_readonly
    assert REVIEWER.wants_mcp and REVIEWER.mcp_readonly
    assert not TESTER.wants_mcp


def test_the_coder_gets_all_three_tools(tmp_path):
    tools = _tools_for(CODER, context(tmp_path, build_config(tmp_path)))
    assert {"list_mcp_tools", "describe_mcp_tool", "call_mcp_tool"} <= names(tools)


def test_the_tester_gets_none(tmp_path):
    tools = _tools_for(TESTER, context(tmp_path, build_config(tmp_path)))
    assert not {"list_mcp_tools", "call_mcp_tool"} & names(tools)


async def test_the_reviewer_cannot_see_a_writing_tool(tmp_path):
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[mcp]\nreadonly = ["echo__echo"]\n', encoding="utf-8"
    )
    cfg = build_config(tmp_path)
    tools = {t.name: t for t in _tools_for(REVIEWER, context(tmp_path, cfg))}
    listing = await tools["list_mcp_tools"].ainvoke({})
    assert "echo__echo" in listing
    assert "explode" not in listing
    refusal = await tools["call_mcp_tool"].ainvoke({"tool_id": "echo__explode", "arguments": {}})
    assert "not available" in refusal.lower()


def test_no_client_means_no_mcp_tools(tmp_path):
    ctx = context(tmp_path, build_config(tmp_path))
    ctx.mcp = None
    assert not {"list_mcp_tools", "call_mcp_tool"} & names(_tools_for(CODER, ctx))


def test_disabled_config_means_no_mcp_tools(tmp_path):
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text("[mcp]\nenabled = false\n", encoding="utf-8")
    ctx = context(tmp_path, build_config(tmp_path))
    assert not {"list_mcp_tools", "call_mcp_tool"} & names(_tools_for(CODER, ctx))
