"""Capability discovery for the REPL's `/tools`, `/skills`, `/mcp` commands
(OPEN-169).

Pure row-builders over the LIVE registries — `subagents/registry.py::REGISTRY`,
the skills bundle, and `.mcp.json`. They return plain row tuples and touch no
terminal, so they are unit-testable without a Console and render identically
from `cli.py`.

Descriptions are short glosses held here beside the names rather than read off
the built tool objects: building a tool's factory needs run-scoped objects (a
`Ledger`, a `FactStore`, a gate) that a read-only listing has no business
constructing. The LIVE fact the user asked for — *which* tools exist and which
agent holds each — comes from `REGISTRY`; `test_every_tool_row_has_a_description`
forbids a tool that has no gloss, so a new tool cannot slip in undescribed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from rudra.subagents.registry import REGISTRY

# Planner-stage tools (not in any subagent spec): clarify/architect record facts
# and ask; breakdown manages the ledger. Attributed to "planner".
_PLANNER_TOOLS = ("record_fact", "ask_user", "add_tasks", "drop_task", "read_ledger")

# The three MCP meta-tools, held by any spec with wants_mcp.
_MCP_TOOLS = ("list_mcp_tools", "describe_mcp_tool", "call_mcp_tool")

# One short line per tool name. Co-located with the catalog so a new tool is
# described here or fails the coverage test.
_TOOL_DESCRIPTIONS: dict[str, str] = {
    # Filesystem (deepagents), scoped per agent by spec.fs_tools
    "read_file": "Read a file in the project (never gated).",
    "ls": "List a directory in the project.",
    "glob": "Find files by glob pattern.",
    "grep": "Search file contents by regex.",
    "write_file": "Create or overwrite a project file (gated).",
    "edit_file": "Replace a string in a project file (gated).",
    "delete": "Delete a project file or directory (gated).",
    "execute": "Run a shell command in the project (gated; opt-in under --auto).",
    # Rudra tools
    "run_tests": "Run the project's test suite and report results (runs as execute).",
    "git_diff": "Show the working-tree diff / status (reads inside the project).",
    "remember": "Record something worth knowing for a future run (writes under .rudra/).",
    "search_memory": "Search this project's long-term memory by meaning.",
    # Planner / control-plane (never gated)
    "record_fact": "Record a settled fact with its reason (planner).",
    "ask_user": "Ask the user a clarifying question (planner, interactive only).",
    "add_tasks": "Declare units of work on the ledger (planner: breakdown).",
    "drop_task": "Retract a task from the ledger (planner: breakdown).",
    "read_ledger": "Read the current task ledger (planner).",
    # MCP meta-tools
    "list_mcp_tools": "List tools exposed by configured MCP servers.",
    "describe_mcp_tool": "Show one MCP tool's schema.",
    "call_mcp_tool": "Call an MCP tool by name.",
}


class ToolRow(NamedTuple):
    """One row of the `/tools` listing."""

    name: str
    description: str
    agents: tuple[str, ...]


def _gloss(name: str) -> str:
    return _TOOL_DESCRIPTIONS.get(name, "")


def tool_rows(project_path: Path, cfg: Any = None) -> list[ToolRow]:  # noqa: ARG001
    """Every tool the model can call, with its one-line gloss and the agents
    that hold it, sorted by name.

    `project_path`/`cfg` are accepted for a stable signature (the renderer has
    them, and a future live-description source may need them); the current
    implementation reads only `REGISTRY` and the module constants.
    """
    agents_of: dict[str, set[str]] = {}

    def _add(tool: str, agent: str) -> None:
        agents_of.setdefault(tool, set()).add(agent)

    for agent_name, spec in REGISTRY.items():
        for tool in (*spec.fs_tools, *spec.rudra_tools):
            _add(tool, agent_name)
        if getattr(spec, "wants_mcp", False):
            for tool in _MCP_TOOLS:
                _add(tool, agent_name)

    for tool in _PLANNER_TOOLS:
        _add(tool, "planner")

    rows = [
        ToolRow(name=name, description=_gloss(name), agents=tuple(sorted(agents)))
        for name, agents in agents_of.items()
    ]
    return sorted(rows, key=lambda r: r.name)


__all__ = ["ToolRow", "tool_rows"]
