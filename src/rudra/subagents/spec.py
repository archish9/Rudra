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
    """

    name: str
    description: str
    system_prompt: str
    role: str
    fs_tools: tuple[str, ...] = ()
    rudra_tools: tuple[str, ...] = ()

    @property
    def can_write(self) -> bool:
        """Can this subagent change the project?"""
        return bool(_WRITE_TOOLS & set(self.fs_tools))


__all__ = ["FS_TOOL_NAMES", "RudraSubagent"]
