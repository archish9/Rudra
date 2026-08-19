"""Reading .mcp.json. The schema is Claude Code's, so users paste theirs verbatim."""

import json

import pytest

from rudra.mcp.config import (
    McpConfigError,
    ServerEntry,
    mcp_json_path,
    read_mcp_json,
    to_connection,
    write_mcp_json,
)


def write(tmp_path, payload):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_absent_file_is_not_an_error(tmp_path):
    assert read_mcp_json(tmp_path / ".mcp.json") == ()


def test_stdio_server_is_parsed(tmp_path):
    path = write(tmp_path, {"mcpServers": {"kala": {"command": "npx", "args": ["-y", "kala-mcp"]}}})
    (entry,) = read_mcp_json(path)
    assert entry == ServerEntry(
        name="kala",
        transport="stdio",
        command="npx",
        args=("-y", "kala-mcp"),
        env={},
        url=None,
        headers={},
    )


def test_http_server_is_parsed(tmp_path):
    path = write(tmp_path, {"mcpServers": {"remote": {"url": "https://example.test/mcp"}}})
    (entry,) = read_mcp_json(path)
    assert entry.transport == "http"
    assert entry.url == "https://example.test/mcp"
    assert entry.command is None


def test_explicit_transport_wins(tmp_path):
    path = write(tmp_path, {"mcpServers": {"s": {"url": "https://e.test/x", "transport": "sse"}}})
    assert read_mcp_json(path)[0].transport == "sse"


def test_malformed_json_names_the_file(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(McpConfigError) as excinfo:
        read_mcp_json(path)
    assert ".mcp.json" in str(excinfo.value)


def test_server_with_neither_command_nor_url_is_fatal(tmp_path):
    path = write(tmp_path, {"mcpServers": {"broken": {"args": ["x"]}}})
    with pytest.raises(McpConfigError) as excinfo:
        read_mcp_json(path)
    assert "broken" in str(excinfo.value)


def test_double_underscore_in_a_server_name_is_fatal(tmp_path):
    # It would make server__tool ids ambiguous to split (C4.5).
    path = write(tmp_path, {"mcpServers": {"a__b": {"command": "x"}}})
    with pytest.raises(McpConfigError):
        read_mcp_json(path)


def test_round_trip_preserves_entries(tmp_path):
    entries = (
        ServerEntry("kala", "stdio", "npx", ("-y", "kala-mcp"), {"K": "v"}, None, {}),
        ServerEntry("remote", "http", None, (), {}, "https://e.test/mcp", {"A": "b"}),
    )
    path = tmp_path / ".mcp.json"
    write_mcp_json(path, entries)
    assert read_mcp_json(path) == entries
    assert set(json.loads(path.read_text())["mcpServers"]) == {"kala", "remote"}


def test_to_connection_is_what_the_adapter_expects(tmp_path):
    entry = ServerEntry("kala", "stdio", "npx", ("-y", "kala-mcp"), {"K": "v"}, None, {})
    assert to_connection(entry) == {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "kala-mcp"],
        "env": {"K": "v"},
    }


def test_mcp_json_path_is_the_project_root(tmp_path):
    assert mcp_json_path(tmp_path) == tmp_path / ".mcp.json"


def test_module_imports_nothing_from_rudra():
    import ast
    import pathlib

    import rudra.mcp.config as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("rudra") for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("rudra")
