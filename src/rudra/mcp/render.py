"""The only thing MCP puts in a system prompt.

One line per configured server plus one instruction. A model that is never
told a server exists will never call `list_mcp_tools`, so this block is what
makes the meta-tool design usable -- and it is fixed-size, which is what
makes it affordable (C4.4).
"""

from __future__ import annotations

from collections.abc import Sequence


def mcp_catalog_block(servers: Sequence[str]) -> str:
    """The prompt block for these servers, or "" when there are none."""
    if not servers:
        return ""
    listed = ", ".join(sorted(servers))
    return (
        "## MCP servers\n"
        f"Available: {listed}.\n"
        "Their tools are not listed here. Call `list_mcp_tools` to see what "
        "they offer, `describe_mcp_tool` for one tool's arguments, and "
        "`call_mcp_tool` to run it. Tools are named `server__tool`."
    )


__all__ = ["mcp_catalog_block"]
