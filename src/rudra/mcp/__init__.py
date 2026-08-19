"""MCP support: servers from .mcp.json, reached through three meta-tools.

No server's tool schemas ever enter a prompt (C4.4). The model lists ids,
asks for one schema when it needs it, and calls by id -- so the prompt cost
of MCP is fixed rather than proportional to how many servers are configured.
"""

from rudra.mcp.config import (
    McpConfigError,
    ServerEntry,
    mcp_json_path,
    read_mcp_json,
    to_connection,
    write_mcp_json,
)

__all__ = [
    "McpConfigError",
    "ServerEntry",
    "mcp_json_path",
    "read_mcp_json",
    "to_connection",
    "write_mcp_json",
]
