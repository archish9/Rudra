"""The three meta-tools. No server's schemas ever enter a prompt (C4.4)."""

import sys
from pathlib import Path

from rudra.mcp.client import McpClient
from rudra.mcp.config import ServerEntry
from rudra.mcp.render import mcp_catalog_block
from rudra.mcp.tools import create_mcp_tools

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


def client(*names: str) -> McpClient:
    entries = [
        ServerEntry(name=name, transport="stdio", command=sys.executable, args=(FIXTURE,))
        for name in (names or ("echo",))
    ]
    return McpClient(entries)


def by_name(tools):
    return {tool.name: tool for tool in tools}


def test_exactly_three_tools_are_registered():
    assert set(by_name(create_mcp_tools(client()))) == {
        "list_mcp_tools",
        "describe_mcp_tool",
        "call_mcp_tool",
    }


async def test_list_shows_ids_and_descriptions_but_no_schema():
    tools = by_name(create_mcp_tools(client()))
    text = await tools["list_mcp_tools"].ainvoke({})
    assert "echo__echo" in text
    assert "Return the text" in text
    assert "properties" not in text  # the schema is describe's job, not list's


async def test_describe_returns_the_schema():
    tools = by_name(create_mcp_tools(client()))
    text = await tools["describe_mcp_tool"].ainvoke({"tool_id": "echo__echo"})
    assert "text" in text and "properties" in text


async def test_call_invokes_the_server():
    tools = by_name(create_mcp_tools(client()))
    text = await tools["call_mcp_tool"].ainvoke(
        {"tool_id": "echo__echo", "arguments": {"text": "hi"}}
    )
    assert "echo: hi" in text


async def test_a_denied_id_is_invisible_and_uncallable():
    tools = by_name(create_mcp_tools(client(), deny=["*__explode"]))
    listing = await tools["list_mcp_tools"].ainvoke({})
    assert "explode" not in listing
    refusal = await tools["call_mcp_tool"].ainvoke({"tool_id": "echo__explode", "arguments": {}})
    assert "not available" in refusal.lower()


async def test_an_unreachable_server_returns_a_sentence_not_an_exception():
    broken = McpClient([ServerEntry(name="ghost", transport="stdio", command="rudra-no-binary")])
    tools = by_name(create_mcp_tools(broken))
    text = await tools["list_mcp_tools"].ainvoke({})
    assert "ghost" in text and "unavailable" in text.lower()


async def test_a_malformed_id_tells_the_model_the_shape():
    tools = by_name(create_mcp_tools(client()))
    text = await tools["call_mcp_tool"].ainvoke({"tool_id": "echo", "arguments": {}})
    assert "server__tool" in text


def test_catalog_block_names_the_servers():
    block = mcp_catalog_block(["kala", "fetch"])
    assert "kala" in block and "fetch" in block and "list_mcp_tools" in block


def test_catalog_block_is_empty_without_servers():
    assert mcp_catalog_block([]) == ""
