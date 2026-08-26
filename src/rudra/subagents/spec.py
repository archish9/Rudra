"""The RudraSubagent record -- pure data, no behaviour, no I/O.

Deliberately has no `middleware` field. Middleware is assembled by
build.py, which always injects the permission gate, so there is no way to
declare a subagent that forgets it (spec S9b.2). That matters because
deepagents does not propagate the parent's `middleware=` to subagents --
only `interrupt_on` is inherited (graph.py:666-703, :718) -- so a spec
without the gate would be gated for approvals and not for denials.
"""

from __future__ import annotations

from dataclasses import dataclass

# The deepagents built-in filesystem tools, exactly as FsToolName spells
# them (filesystem.py:1321). A name outside this set is rejected at build
# time rather than silently dropped.
FS_TOOL_NAMES = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
)

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete"})


@dataclass(frozen=True)
class RudraSubagent:
    """One subagent Rudra ships.

    Attributes:
        name: Unique identifier. For "general-purpose" the exact spelling
            matters -- it is what suppresses the ungated subagent
            deepagents adds automatically (graph.py:751).
        description: What a delegating parent reads when choosing.
        system_prompt: Instructions. Must state a stop condition, because
            only the final assistant message reaches the caller.
        role: A BUILTIN_ROLES member, resolved through build_model.
        fs_tools: Which built-in filesystem tools this subagent may see.
            Absence is the enforcement -- a model cannot call a tool that
            was never registered (U.17).
        rudra_tools: Rudra tool names, not callables: the factories need
            per-run arguments a frozen spec cannot hold.
        wants_skills: Does this subagent get the run's skill index? A
            methodology library helps an agent choosing *how* to work. The
            reviewer reads a diff and reports, and would pay ~916 tokens
            for descriptions it cannot act on (S11b.1).
        wants_mcp: Does this subagent get the MCP meta-tools? The tester and
            the planner stages do not: the tester runs one command, and the
            planner stages must not reach a network service while deciding
            what to build.
        mcp_readonly: Restrict it to `[mcp] readonly` ids. The reviewer
            cannot write because its writing tools are never registered
            (U.17); an unfiltered `call_mcp_tool` would hand that back, since
            an MCP server can write. The patterns live in the project's
            config rather than here so no shipped spec names a real server.
        wants_memory: Does this subagent get the recalled-memories block in
            its prompt? The reviewer does not: it reads a diff and reports,
            project history does not change what the diff says, and it
            would pay for the block on every review -- S11b.1's reasoning
            applied to a second payload. The two memory *tools* are gated
            separately, by `rudra_tools`, because that is how every other
            Rudra tool reaches a spec.
        can_delegate: May this subagent call `task` to dispatch another
            agent? Default False, and no shipped spec sets it True (OPEN-26).
            deepagents registers `task` for every agent Rudra builds, because
            Rudra always passes the gated `general-purpose` spec that
            suppresses the ungated auto-added one -- so the tool is present
            whether or not the spec wants it, and the coder found it. It
            writes one file for one task; there is no sub-work to hand off.
            Enforced by withholding the tool, never by asking the prompt
            nicely (middleware/delegation_guard.py).
        wants_compaction: Does this subagent get `compact_conversation`?
            The coder and tester retry and read pytest transcripts; the
            planner stages are short and the reviewer runs once, so they
            are simply not given the tool (S12.8, on S9b.3's precedent).
    """

    name: str
    description: str
    system_prompt: str
    role: str
    fs_tools: tuple[str, ...] = ()
    rudra_tools: tuple[str, ...] = ()
    wants_skills: bool = False
    can_delegate: bool = False
    wants_compaction: bool = False
    wants_memory: bool = False
    wants_mcp: bool = False
    mcp_readonly: bool = False

    @property
    def can_write(self) -> bool:
        """Can this subagent change the project?"""
        return bool(_WRITE_TOOLS & set(self.fs_tools))


__all__ = ["FS_TOOL_NAMES", "RudraSubagent"]
