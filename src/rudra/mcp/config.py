"""Reading and writing .mcp.json.

The schema is Claude Code's -- `{"mcpServers": {name: {...}}}` -- so a user
pastes an existing config in unchanged (C4.2, D1). Every Rudra-specific knob
lives in `[mcp]` in config.toml instead; putting policy here would break the
one property this file exists for.

Imports nothing from Rudra, for the reason loop/ledger.py does not: it must be
readable and testable with no agent machinery in the way.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_TRANSPORTS = ("stdio", "http", "sse", "websocket")


class McpConfigError(Exception):
    """`.mcp.json` is unreadable, malformed, or describes an unusable server."""


@dataclass(frozen=True)
class ServerEntry:
    """One configured MCP server, normalized."""

    name: str
    transport: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def mcp_json_path(project_root: Path) -> Path:
    """Where a project's MCP servers are declared."""
    return Path(project_root) / ".mcp.json"


def read_mcp_json(path: Path) -> tuple[ServerEntry, ...]:
    """Every server declared in `path`, or `()` when the file is absent.

    Absence is not an error: MCP is opt-in, and Rudra must work identically
    with no servers at all (D19).
    """
    path = Path(path)
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"{path} could not be read as JSON: {exc}"
        raise McpConfigError(msg) from exc

    servers = raw.get("mcpServers") if isinstance(raw, dict) else None
    if servers is None:
        return ()
    if not isinstance(servers, dict):
        msg = f'{path}: "mcpServers" must be an object mapping names to servers.'
        raise McpConfigError(msg)

    return tuple(_entry(path, name, body) for name, body in servers.items())


def _entry(path: Path, name: str, body: Any) -> ServerEntry:
    if not isinstance(body, dict):
        msg = f"{path}: server '{name}' must be an object."
        raise McpConfigError(msg)
    if "__" in name:
        msg = (
            f"{path}: server name '{name}' contains '__', which Rudra uses to "
            f"separate a server from its tool in a tool id. Rename it."
        )
        raise McpConfigError(msg)

    command = body.get("command")
    url = body.get("url")
    if command is None and url is None:
        msg = f"{path}: server '{name}' declares neither 'command' (stdio) nor 'url' (http/sse)."
        raise McpConfigError(msg)

    # `type` is what Claude Code actually writes -- verified against a real
    # ~/.claude.json, whose server keys are ['type','command','args','env'].
    # This module's contract is that a user pastes an existing config in
    # unchanged, so ignoring it meant a pasted `{"type": "sse"}` server was
    # recorded as "http" and routed to the streamable-HTTP session against
    # an SSE endpoint. stdio and http happen to coincide with the inference
    # below, so only sse broke -- silently, with an error the user could not
    # explain from their own file (CR-G7).
    transport = (
        body.get("transport") or body.get("type") or ("stdio" if command is not None else "http")
    )
    if transport not in VALID_TRANSPORTS:
        msg = (
            f"{path}: server '{name}' has transport {transport!r}; "
            f"valid: {', '.join(VALID_TRANSPORTS)}."
        )
        raise McpConfigError(msg)

    return ServerEntry(
        name=name,
        transport=transport,
        command=str(command) if command is not None else None,
        args=tuple(str(item) for item in body.get("args", ())),
        env={str(k): str(v) for k, v in (body.get("env") or {}).items()},
        url=str(url) if url is not None else None,
        headers={str(k): str(v) for k, v in (body.get("headers") or {}).items()},
    )


def write_mcp_json(path: Path, entries: Sequence[ServerEntry]) -> None:
    """Replace `path` with exactly these servers.

    Writing is correct here, unlike `rudra config set` (TODO.md S6.1): this
    file is plain JSON with no comments to destroy and needs no new
    dependency to serialize.
    """
    servers: dict[str, dict[str, Any]] = {}
    for entry in entries:
        body: dict[str, Any] = {}
        if entry.command is not None:
            body["command"] = entry.command
            if entry.args:
                body["args"] = list(entry.args)
            if entry.env:
                body["env"] = dict(entry.env)
        if entry.url is not None:
            body["url"] = entry.url
            if entry.headers:
                body["headers"] = dict(entry.headers)
        body["transport"] = entry.transport
        # Both spellings, so a file Rudra writes is one Claude Code reads.
        body["type"] = entry.transport
        servers[entry.name] = body

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")


def to_connection(entry: ServerEntry) -> dict[str, Any]:
    """The dict `MultiServerMCPClient` wants for this server."""
    if entry.command is not None:
        return {
            "transport": entry.transport,
            "command": entry.command,
            "args": list(entry.args),
            "env": dict(entry.env),
        }
    return {
        "transport": entry.transport,
        "url": entry.url,
        "headers": dict(entry.headers),
    }


__all__ = [
    "VALID_TRANSPORTS",
    "McpConfigError",
    "ServerEntry",
    "mcp_json_path",
    "read_mcp_json",
    "to_connection",
    "write_mcp_json",
]
