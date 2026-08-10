"""The deny half of the gate.

Only `deny` is handled here. `ask` belongs to `interrupt_on`, whose
pause/resume plumbing deepagents already owns, and routing a denial through
an interrupt would pause a graph purely to un-pause it. `allow` falls
through to the handler untouched.

A denial returns an error ToolMessage naming the rule, so the model can
adapt rather than retry blindly against something that will never succeed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.permissions.audit import AuditLog
from rudra.permissions.rules import PermissionEngine, gated_arg


def _denial_text(tool: str, arg: str | None, rule: str | None) -> str:
    target = f" on {arg!r}" if arg else ""
    if rule and rule.startswith("<floor:"):
        name = rule[len("<floor:") : -1]
        return (
            f"Permission denied: {tool}{target} is blocked by Rudra's built-in "
            f"'{name}' rule, which applies in every permission mode. "
            f"Do not retry this call."
        )
    if rule:
        return (
            f"Permission denied: {tool}{target} matches the deny rule {rule!r} "
            f"in this project's configuration. Do not retry this call."
        )
    return (
        f"Permission denied: {tool}{target} is not permitted in "
        f"'plan' mode, which makes no project changes. Do not retry this call."
    )


class RudraPermissionMiddleware(AgentMiddleware):
    """Short-circuits denied tool calls before they reach the backend."""

    def __init__(self, engine: PermissionEngine, audit: AuditLog, mode: str) -> None:
        super().__init__()
        self.engine = engine
        self.audit = audit
        self.mode = mode

    def _check(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        tool = call["name"]
        args = call.get("args") or {}
        decision = self.engine.decide(tool, args)
        if decision.effect != "deny":
            return None
        arg = gated_arg(tool, args)
        self.audit.record(tool, arg, decision, mode=self.mode, outcome="deny")
        return ToolMessage(
            content=_denial_text(tool, arg, decision.rule),
            tool_call_id=call.get("id", ""),
            name=tool,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        denial = self._check(request)
        return denial if denial is not None else handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        denial = self._check(request)
        return denial if denial is not None else await handler(request)


__all__ = ["RudraPermissionMiddleware"]
