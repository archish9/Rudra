"""Talking to configured MCP servers.

Nothing here enters a prompt. `tools.py` is the only caller that a model can
reach, and it reaches this through three fixed tools -- which is what keeps
MCP's prompt cost independent of how many servers are configured (C4.4).

One session per call: `MultiServerMCPClient.session()` is an anyio-scoped
context manager, so a session entered in one task and exited in another is a
cancel-scope error. Sessions are therefore short-lived and the *catalog* is
what gets cached.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rudra.mcp.config import ServerEntry, to_connection
from rudra.mcp.registry import split_id, tool_id


class McpUnavailable(Exception):
    """A server could not be reached, or an id names one that is not configured.

    Raised rather than returned so `tools.py` decides what the model is told;
    the run itself never fails on it (D19: absence must be graceful).
    """


@dataclass(frozen=True)
class ToolInfo:
    """One tool on one server."""

    id: str
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


def _text(result: Any) -> str:
    """The text of a CallToolResult, whatever content types it carries."""
    parts = []
    for item in getattr(result, "content", ()) or ():
        text = getattr(item, "text", None)
        parts.append(text if text is not None else f"[{getattr(item, 'type', 'content')}]")
    body = "\n".join(parts).strip()
    if getattr(result, "isError", False):
        return f"The tool reported an error:\n{body}" if body else "The tool reported an error."
    return body or "(the tool returned no content)"


class McpClient:
    """Every configured server, reached lazily.

    Construction starts no process and opens no connection. The first call
    that needs a server spawns it; a server that fails to start is recorded
    as broken so a retrying model does not respawn it once per attempt.
    """

    def __init__(
        self,
        entries: Sequence[ServerEntry],
        *,
        timeout: int = 60,
        console: Any = None,
    ) -> None:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self._entries = {entry.name: entry for entry in entries}
        self._client = MultiServerMCPClient(
            {name: to_connection(entry) for name, entry in self._entries.items()}
        )
        self._timeout = timeout
        self._console = console
        self._catalog: dict[str, tuple[ToolInfo, ...]] = {}
        self._broken: dict[str, str] = {}

    @property
    def servers(self) -> tuple[str, ...]:
        return tuple(self._entries)

    @property
    def catalog_loaded(self) -> tuple[str, ...]:
        """Which servers have actually been contacted. Lazy-loading's evidence."""
        return tuple(sorted(self._catalog))

    @property
    def broken_servers(self) -> tuple[str, ...]:
        return tuple(sorted(self._broken))

    def _require(self, server: str) -> None:
        if server not in self._entries:
            known = ", ".join(self._entries) or "none configured"
            msg = f"No MCP server named '{server}' is configured. Configured: {known}."
            raise McpUnavailable(msg)
        if server in self._broken:
            msg = f"MCP server '{server}' is unavailable: {self._broken[server]}"
            raise McpUnavailable(msg)

    def _fail(self, server: str, exc: BaseException) -> McpUnavailable:
        reason = f"{type(exc).__name__}: {exc}".strip()
        self._broken[server] = reason
        if self._console is not None:
            self._console.print(f"[yellow]Warning:[/yellow] MCP server '{server}' failed: {reason}")
        return McpUnavailable(f"MCP server '{server}' is unavailable: {reason}")

    async def list_tools(self, server: str | None = None) -> tuple[ToolInfo, ...]:
        """Every visible tool, contacting only servers not already cached."""
        names = [server] if server is not None else list(self._entries)
        infos: list[ToolInfo] = []
        for name in names:
            self._require(name)
            if name not in self._catalog:
                self._catalog[name] = await self._load(name)
            infos.extend(self._catalog[name])
        return tuple(infos)

    async def _load(self, server: str) -> tuple[ToolInfo, ...]:
        try:
            async with self._client.session(server) as session:
                listed = await asyncio.wait_for(session.list_tools(), timeout=self._timeout)
        except Exception as exc:  # noqa: BLE001 - reported to the model, never fatal
            raise self._fail(server, exc) from exc
        return tuple(
            ToolInfo(
                id=tool_id(server, tool.name),
                server=server,
                name=tool.name,
                description=(tool.description or "").strip(),
                input_schema=dict(tool.inputSchema or {}),
            )
            for tool in listed.tools
        )

    async def describe(self, value: str) -> ToolInfo:
        """One tool's full schema."""
        server, name = split_id(value)
        for info in await self.list_tools(server):
            if info.name == name:
                return info
        known = ", ".join(i.id for i in await self.list_tools(server)) or "none"
        msg = f"MCP server '{server}' has no tool '{name}'. It offers: {known}."
        raise McpUnavailable(msg)

    async def call(self, value: str, args: dict[str, Any] | None = None) -> str:
        """Invoke one tool and return its output as text."""
        server, name = split_id(value)
        self._require(server)
        try:
            async with self._client.session(server) as session:
                result = await asyncio.wait_for(
                    session.call_tool(name, args or {}), timeout=self._timeout
                )
        except TimeoutError as exc:
            msg = f"MCP call '{value}' exceeded the {self._timeout}s [mcp] timeout."
            raise McpUnavailable(msg) from exc
        except Exception as exc:  # noqa: BLE001 - reported to the model, never fatal
            raise self._fail(server, exc) from exc
        return _text(result)

    async def aclose(self) -> None:
        """Nothing to unwind -- sessions are per call. Present so callers need not care."""
        self._catalog.clear()


__all__ = ["McpClient", "McpUnavailable", "ToolInfo"]
