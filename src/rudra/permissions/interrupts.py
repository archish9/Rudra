"""Turning `ask` decisions into deepagents interrupts.

Only the mutating tools get an `interrupt_on` entry. Read-only tools are
never gated, and giving them a config would make the `when` predicate run
on every read for an answer that is always False.

The `when` predicate is what keeps the two mechanisms consistent: it calls
the same PermissionEngine the deny middleware calls, so a call already
covered by an allow rule or a session grant never reaches a prompt.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rudra.permissions.rules import MUTATING_TOOLS, PermissionEngine


def _predicate(engine: PermissionEngine, tool: str) -> Callable[[Any], bool]:
    """Bind `tool` per entry.

    A closure over the loop variable would make every predicate see
    whichever tool the loop ended on.
    """

    def when(request: Any) -> bool:
        call = getattr(request, "tool_call", None) or {}
        name = call.get("name", tool)
        args = call.get("args") or {}
        return engine.decide(name, args).effect == "ask"

    return when


def build_interrupt_on(engine: PermissionEngine) -> dict[str, dict[str, Any]]:
    """The `interrupt_on=` mapping for `create_deep_agent`."""
    return {
        tool: {
            "allowed_decisions": ["approve", "reject"],
            "when": _predicate(engine, tool),
        }
        for tool in sorted(MUTATING_TOOLS)
    }


__all__ = ["build_interrupt_on"]
