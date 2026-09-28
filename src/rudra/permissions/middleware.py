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
from rudra.permissions.rules import PermissionEngine, gated_arg, gated_arg_names

# What a refused call did NOT do, and how to re-send it, per tool that can
# reach the fail-closed branch (`rules.py`, MUTATING_TOOLS with a path).
_NOT_DONE = {"write_file": "written", "edit_file": "changed", "delete": "deleted"}
_RESEND = {
    "write_file": "the file to write, and the same content",
    "edit_file": "the file to edit",
    "delete": "the file to delete",
}


def _unresolvable_text(tool: str, arg: str | None, args: dict[str, Any], registered: bool) -> str:
    """Why `decide` could not judge this call, and the correction (OPEN-163).

    Denying is right (CR-B2, CR-B7); blaming a rule was not. The marker
    `<unresolvable-path>` used to reach the user-rule branch below, so run
    1808f56c8329's coder, having sent `write_file` with `content` and no
    `file_path`, was told it matched a deny rule "in this project's
    configuration" -- whose `deny` was empty -- and not to retry, where
    re-sending WITH the path is the whole correction. Every hit in the
    archive threw a generated body away; a refusal without the correction is
    answered by regenerating it (OPEN-95, OPEN-100).

    `registered` is False when the agent does not hold `tool` at all: one gate
    sits on every stack, and the planner, the reviewer and general-purpose
    hold no write tool. Telling them to send it again would name a route
    through a tool they lack (OPEN-15), so they are told only what happened.
    """
    names = gated_arg_names(tool)
    name = names[0] if names else "path"
    if arg is not None:
        fact = f"the path {arg!r} given to {tool} could not be resolved"
        resend = "Send the call again with a plain project path."
    else:
        # gated_arg answers None for a missing path and for one that is not a
        # string; only the first is "called without". A null reads as missing.
        wrong = next(
            ((key, args[key]) for key in names if args.get(key) is not None),
            None,
        )
        if wrong is not None:
            fact = (
                f"{tool} was called with a {wrong[0]} that is not a string "
                f"(it was {type(wrong[1]).__name__})"
            )
        else:
            received = sorted(key for key, value in args.items() if value is not None)
            got = f"it received: {', '.join(received)}" if received else "it received no arguments"
            fact = f"{tool} was called without a {name} argument ({got})"
        resend = f"Send the call again with {name} set to {_RESEND.get(tool, 'a project path')}."
    not_done = f"Nothing was {_NOT_DONE.get(tool, 'done')}."
    if not registered:
        return f"Permission denied: {fact}, and {tool} is not one of this agent's tools. {not_done}"
    return f"Permission denied: {fact}. {not_done} {resend}"


def _denial_text(
    tool: str,
    arg: str | None,
    rule: str | None,
    *,
    args: dict[str, Any] | None = None,
    registered: bool = True,
) -> str:
    target = f" on {arg!r}" if arg else ""
    if rule == "<unresolvable-path>":
        return _unresolvable_text(tool, arg, args or {}, registered)
    if rule == "<auto:shell-not-opted-in>":
        return (
            "Permission denied: running commands is disabled in unattended "
            "('auto') mode unless the user opts in, because nobody reads the "
            "command before it runs. Do not retry this call — write the files "
            "you need and describe what should be run instead. The user can "
            "enable it with --allow-shell or [tools] shell_in_auto = true."
        )
    if rule == "<auto:mcp-not-opted-in>":
        return (
            "Permission denied: MCP tools are disabled in unattended ('auto') "
            "mode unless the user opts in, because an MCP server is a separate "
            "process and nobody reads the call before it runs. Do not retry "
            "this call — do the work with your own tools and say what you "
            "would have called. The user can enable it with --allow-mcp or "
            "[mcp] mcp_in_auto = true."
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
    A1.53.
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
            # prompt and no denial, so the call would run unaudited (A1.53).
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
            content=_denial_text(
                tool,
                arg,
                decision.rule,
                args=args,
                # langgraph hands over a call to a tool this agent does not
                # hold with `request.tool` None (tool_route.py reads it too).
                registered=getattr(request, "tool", None) is not None,
            ),
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
