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
    if rule == "<auto:shell-not-opted-in>":
        return (
            "Permission denied: running commands is disabled in unattended "
            "('auto') mode unless the user opts in, because nobody reads the "
            "command before it runs. Do not retry this call — write the files "
            "you need and describe what should be run instead. The user can "
            "enable it with --allow-shell or [tools] shell_in_auto = true."
        )
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


def _unregistered_text(tool: str) -> str:
    return (
        f"Permission denied: {tool!r} has no permission rule and no approval "
        f"prompt, so Rudra cannot ask the user about it and will not run it "
        f"unchecked. Do not retry this call. This is a gap in Rudra's "
        f"configuration, not a mistake you made — use a different tool."
    )


class RudraPermissionMiddleware(AgentMiddleware):
    """Short-circuits denied tool calls before they reach the backend.

    `interrupt_tools` is the set of names `interrupt_on` actually registers.
    Without it this class could not tell an `ask` that will reach a prompt
    from one that will reach nothing, and the second kind ran unchecked --
    A1.51.
    """

    def __init__(
        self,
        engine: PermissionEngine,
        audit: AuditLog,
        mode: str,
        interrupt_tools: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.engine = engine
        self.audit = audit
        self.mode = mode
        self.interrupt_tools = interrupt_tools

    def _check(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        tool = call["name"]
        args = call.get("args") or {}
        decision = self.engine.decide(tool, args)
        arg = gated_arg(tool, args)

        if decision.effect == "allow":
            # Allows are recorded here too, not only denials. In auto mode
            # nothing is ever denied and nothing ever prompts, so a
            # deny-only middleware left the unattended run — the one whose
            # record matters most — with an empty audit log. AuditLog drops
            # the routine reads; this layer does not second-guess it.
            self.audit.record(tool, arg, decision, mode=self.mode, outcome="allow")
            return None
        if decision.effect != "deny":
            # "ask" is interrupt_on's; the prompt records what the user chose.
            if tool in self.interrupt_tools:
                return None
            # ...but only where an entry exists. Without one there is no
            # prompt and no denial, so the call would run unaudited (A1.51).
            # Fail closed rather than enumerate every tool deepagents might
            # register.
            self.audit.record(tool, arg, decision, mode=self.mode, outcome="deny")
            return ToolMessage(
                content=_unregistered_text(tool),
                tool_call_id=call.get("id", ""),
                name=tool,
                status="error",
            )

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
