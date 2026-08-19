"""Connecting to a real stdio MCP server -- a subprocess, not a mock."""

import sys
from pathlib import Path

import pytest

from rudra.mcp.client import McpClient, McpUnavailable
from rudra.mcp.config import ServerEntry

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


def echo_entry(name: str = "echo") -> ServerEntry:
    return ServerEntry(name=name, transport="stdio", command=sys.executable, args=(FIXTURE,))


async def test_list_tools_reaches_a_real_server():
    client = McpClient([echo_entry()])
    try:
        infos = await client.list_tools()
        assert {info.id for info in infos} == {"echo__echo", "echo__explode"}
        assert next(i for i in infos if i.name == "echo").description
    finally:
        await client.aclose()


async def test_describe_returns_the_input_schema():
    client = McpClient([echo_entry()])
    try:
        info = await client.describe("echo__echo")
        assert "text" in info.input_schema["properties"]
    finally:
        await client.aclose()


async def test_call_returns_the_tool_output():
    client = McpClient([echo_entry()])
    try:
        assert "echo: hi" in await client.call("echo__echo", {"text": "hi"})
    finally:
        await client.aclose()


async def test_a_failing_tool_reports_rather_than_raises():
    client = McpClient([echo_entry()])
    try:
        result = await client.call("echo__explode", {})
        assert "fail" in result.lower() or "error" in result.lower()
    finally:
        await client.aclose()


async def test_two_mounts_of_the_same_server_stay_distinct():
    # C4.5, constructed: kala collides with no Rudra tool name, so the
    # collision that matters is one server mounted twice.
    client = McpClient([echo_entry("a"), echo_entry("b")])
    try:
        ids = {info.id for info in await client.list_tools()}
        assert {"a__echo", "b__echo"} <= ids
        assert "echo: x" in await client.call("b__echo", {"text": "x"})
    finally:
        await client.aclose()


async def test_a_missing_binary_degrades_instead_of_crashing():
    entry = ServerEntry(name="ghost", transport="stdio", command="rudra-no-such-binary")
    client = McpClient([entry])
    try:
        with pytest.raises(McpUnavailable) as excinfo:
            await client.list_tools("ghost")
        assert "ghost" in str(excinfo.value)
        # And the failure is remembered, so a retrying model does not spawn
        # a doomed subprocess once per call.
        assert client.broken_servers == ("ghost",)
    finally:
        await client.aclose()


async def test_an_unknown_server_in_an_id_is_reported():
    client = McpClient([echo_entry()])
    try:
        with pytest.raises(McpUnavailable, match="nope"):
            await client.call("nope__thing", {})
    finally:
        await client.aclose()


async def test_no_process_is_started_until_a_call():
    # "Lazy" means exactly this: constructing the client touches nothing.
    client = McpClient([echo_entry()])
    try:
        assert client.servers == ("echo",)
        assert client.catalog_loaded == ()
    finally:
        await client.aclose()
