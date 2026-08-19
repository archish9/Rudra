"""A two-tool stdio MCP server, for tests that must not need the network.

Launched as `sys.executable <this file>`. It is not a Rudra module and is
never imported -- it runs as its own process, which is the whole point.
"""

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def echo(text: str) -> str:
    """Return the text you were given."""
    return f"echo: {text}"


@server.tool()
def explode() -> str:
    """Always fail, so error handling can be tested."""
    msg = "this tool always fails"
    raise ValueError(msg)


if __name__ == "__main__":
    server.run()
