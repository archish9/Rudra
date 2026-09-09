"""Turning `ask` decisions into deepagents interrupts.

Only the mutating tools get an `interrupt_on` entry. Read-only tools are
never gated, and giving them a config would make the `when` predicate run
on every read for an answer that is always False.

The `when` predicate is what keeps the two mechanisms consistent: it calls
the same PermissionEngine the deny middleware calls, so a call already
covered by an allow rule or a session grant never reaches a prompt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
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


def narrow_interrupt_on(
    interrupt_on: dict[str, dict[str, Any]], granted: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """The gate's map, restricted to the tools one agent actually holds.

    `build_interrupt_on` above emits an entry for every name in
    MUTATING_TOOLS and knows nothing about any agent -- which is correct:
    the map describes what the GATE covers. What a given agent can call is
    that agent's business, and this is where the two meet.

    Without this, deepagents' HumanInTheLoopMiddleware matches on the
    tool-call NAME in the assistant message, which happens BEFORE the tool
    node discovers the name is not registered. So an agent raises a full
    approval panel for a call it cannot make, takes the user's answer, and
    writes their grant to the audit log -- OPEN-15 on the subagent stack
    (the coder, with no `execute`, prompting for `execute pwd`) and
    OPEN-101 on the planner, where the user answered a `write_file` panel
    with `!` and set `SessionGrants.approve_all` for the whole session. A
    `task` call ten minutes later was allowed by that grant.

    One implementation, two call sites: `subagents/build.py::_interrupt_on_for`
    and `agent/planner_agent.py`. That is `verify/stubs.py::is_build_output`'s
    lesson (OPEN-64) applied before the second copy exists rather than
    after it drifts.
    """
    names = set(granted)
    return {name: cfg for name, cfg in interrupt_on.items() if name in names}


__all__ = ["build_interrupt_on", "narrow_interrupt_on"]
