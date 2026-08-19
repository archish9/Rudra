"""The three tools that carry every MCP call.

Why three and not one per MCP tool: a server's schemas would otherwise be
paid for on every model call, for every server, whether used or not -- and a
dozen servers blows a 32B window (C4.4). Why three and not two: without
`describe_mcp_tool` the model must guess arguments, because `list_mcp_tools`
deliberately withholds schemas.

`allow`/`deny` are per-subagent visibility. A filtered id is not listed and
not callable, so the reviewer cannot name a writing tool at all -- absence is
the enforcement, exactly as it is for its filesystem tools (U.17).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import tool

from rudra.mcp.client import McpClient, McpUnavailable
from rudra.mcp.registry import filter_ids


def _visible(infos: Sequence[Any], allow: Sequence[str], deny: Sequence[str]) -> list[Any]:
    permitted = set(filter_ids([info.id for info in infos], allow=allow, deny=deny))
    return [info for info in infos if info.id in permitted]


def _bad_id(tool_id: str, exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return (
            f"'{tool_id}' is not a tool id. Ids have the form server__tool — "
            f"call `list_mcp_tools` to see the real ones."
        )
    return str(exc)


def create_mcp_tools(
    client: McpClient,
    *,
    allow: Sequence[str] = (),
    deny: Sequence[str] = (),
) -> list:
    """The MCP tool layer for one agent."""

    async def _list(server: str | None) -> list[Any]:
        """Every visible tool, tolerating servers that are down.

        Partial availability lists what works: one unreachable server must
        not hide the others. Only a total failure is raised, so the model is
        told something rather than shown an empty list it cannot explain.
        """
        infos: list[Any] = []
        problems: list[str] = []
        for name in [server] if server else list(client.servers):
            try:
                infos.extend(await client.list_tools(name))
            except McpUnavailable as exc:
                problems.append(str(exc))
        if problems and not infos:
            raise McpUnavailable("\n".join(problems))
        return _visible(infos, allow, deny)

    @tool
    async def list_mcp_tools(server: str = "") -> str:
        """List the tools available from this project's MCP servers.

        Shows each tool's id and what it does, without its arguments. Call
        `describe_mcp_tool` for the arguments of one you intend to use.

        Args:
            server: Limit the listing to one server. Leave empty for all.

        Returns:
            One line per tool, as `server__tool — description`.
        """
        try:
            infos = await _list(server.strip() or None)
        except McpUnavailable as exc:
            return str(exc)
        if not infos:
            return "No MCP tools are available to you."
        return "\n".join(f"{info.id} — {info.description or 'no description'}" for info in infos)

    @tool
    async def describe_mcp_tool(tool_id: str) -> str:
        """Show one MCP tool's arguments, as a JSON schema.

        Args:
            tool_id: The id from `list_mcp_tools`, e.g. `kala__verify`.

        Returns:
            The tool's description and its input schema.
        """
        try:
            info = await client.describe(tool_id)
        except (McpUnavailable, ValueError) as exc:
            return _bad_id(tool_id, exc)
        if not _visible([info], allow, deny):
            return f"'{tool_id}' is not available to you."
        schema = json.dumps(info.input_schema, indent=2)
        return f"{info.id}\n{info.description}\n\nArguments:\n{schema}"

    @tool
    async def call_mcp_tool(tool_id: str, arguments: dict | None = None) -> str:
        """Run one MCP tool.

        Args:
            tool_id: The id from `list_mcp_tools`, e.g. `kala__verify`.
            arguments: The tool's arguments, as an object. Get their shape
                from `describe_mcp_tool` first — a wrong shape is rejected by
                the server, not repaired. Named `arguments` rather than
                `args`, which langchain's structured-tool layer mangles into
                `v__args`.

        Returns:
            Whatever the tool returned, as text.
        """
        try:
            info = await client.describe(tool_id)
        except (McpUnavailable, ValueError) as exc:
            return _bad_id(tool_id, exc)
        if not _visible([info], allow, deny):
            return f"'{tool_id}' is not available to you. Do not retry this call."
        try:
            return await client.call(tool_id, arguments or {})
        except McpUnavailable as exc:
            return str(exc)

    return [list_mcp_tools, describe_mcp_tool, call_mcp_tool]


__all__ = ["create_mcp_tools"]
