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

import re
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


# Where each superpowers skill is applied in Rudra after OPEN-168. The inline
# adaptation lives in prompt text (not machine-readable), so this map is Rudra's
# own statement of fact, kept beside the catalog. "" = not applied anywhere.
SKILL_STAGE_MAP: dict[str, str] = {
    "brainstorming": "planner: clarify (inlined)",
    "writing-plans": "planner: architect + breakdown (inlined)",
    "test-driven-development": "coder + tester (inlined)",
    "systematic-debugging": "coder fix-loop (inlined)",
    "verification-before-completion": "the deterministic gate (Python)",
    "requesting-code-review": "reviewer rubric (inlined)",
    "executing-plans": "the loop engine (Python)",
    "subagent-driven-development": "the loop engine (Python)",
    "using-superpowers": "bootstrap primer (indexed for coder/tester)",
    "receiving-code-review": "",
    "dispatching-parallel-agents": "",
    "finishing-a-development-branch": "",
    "writing-skills": "",
}


class SkillRow(NamedTuple):
    """One row of the `/skills` listing."""

    name: str
    description: str
    enabled: bool
    applied_at: str


class McpRow(NamedTuple):
    """One row of the `/mcp` listing."""

    name: str
    transport: str
    endpoint: str


_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _skill_description(skill_md: Path) -> str:
    """The `description:` from a SKILL.md frontmatter, or ''.

    Parsed leniently: a corpus skill that cannot be read must not crash the
    listing.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _FRONTMATTER.match(text)
    if not match:
        return ""
    try:
        import yaml

        data = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 - a malformed frontmatter is just no description
        return ""
    if isinstance(data, dict):
        return str(data.get("description", "")).strip()
    return ""


def skill_rows(cfg: Any = None) -> list[SkillRow]:
    """Every vendored skill, its description, whether it is enabled, and where
    it is applied in Rudra (post-OPEN-168), sorted by name.
    """
    from rudra.skills.registry import BUNDLES

    enabled = set(getattr(getattr(cfg, "skills", None), "enabled", ()) or ())
    rows: list[SkillRow] = []
    for bundle in BUNDLES:
        for name in bundle.skill_names():
            is_enabled = name in enabled
            applied = SKILL_STAGE_MAP.get(name, "")
            if not applied and is_enabled:
                applied = "indexed (coder/tester)"
            rows.append(
                SkillRow(
                    name=name,
                    description=_skill_description(bundle.skills_path / name / "SKILL.md"),
                    enabled=is_enabled,
                    applied_at=applied,
                )
            )
    return sorted(rows, key=lambda r: r.name)


def mcp_rows(project_path: Path, cfg: Any = None) -> list[McpRow]:
    """Configured MCP servers (from `.mcp.json`), never their env. Empty when
    no `.mcp.json`, when it is malformed, or when a server is disabled in cfg.
    """
    from rudra.mcp.config import McpConfigError, mcp_json_path, read_mcp_json

    try:
        entries = read_mcp_json(mcp_json_path(project_path))
    except McpConfigError:
        return []
    disabled = set(getattr(getattr(cfg, "mcp", None), "disabled_servers", ()) or ())
    rows: list[McpRow] = []
    for entry in entries:
        if entry.name in disabled:
            continue
        endpoint = entry.command or entry.url or ""
        if entry.command and entry.args:
            endpoint = f"{entry.command} {' '.join(entry.args)}"
        rows.append(McpRow(name=entry.name, transport=entry.transport, endpoint=endpoint))
    return sorted(rows, key=lambda r: r.name)


__all__ = [
    "SKILL_STAGE_MAP",
    "McpRow",
    "SkillRow",
    "ToolRow",
    "mcp_rows",
    "skill_rows",
    "tool_rows",
]
