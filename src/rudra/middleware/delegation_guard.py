"""DelegationGuardMiddleware -- a subagent that should not delegate cannot.

**Why this is a middleware and not a spec field passed to deepagents.**
`create_deep_agent` registers the `task` tool whenever ANY subagent spec is
supplied (`graph.py:827-828`), and Rudra always supplies one: the gated
`general-purpose` spec that stops deepagents auto-adding its own ungated
version (OPEN-14, `build.py:_nested_subagents`). Passing `subagents=None`
would remove `task` -- and restore the ungated agent, because the auto-add
skips only when a supplied spec is literally NAMED `general-purpose`
(`graph.py:750-751`). The only other switch,
`general_purpose_subagent.enabled`, lives on a harness profile resolved from
the model (`graph.py:605`) with no `create_deep_agent` parameter, and
reaching it means a per-model profile in a project that is provider-agnostic
by rule (CLAUDE.md §5).

So the spec stays passed and the TOOL is withheld from the model's view.
Same shape as `execute_guard.py`, and the same reason: **absence is the
enforcement** (U.17). A prompt cannot outrank a tool that exists (OPEN-17).

**What it cost before.** Measured 2026-08-26 (run `run2`), six of twelve
tasks blocked with "the coder wrote nothing" in about twenty seconds each.
The coder wanted to run tests, has no `execute` (`registry.py:179`), and
called `task` instead. Its delegate is the general-purpose agent -- read-only
and also without `execute` -- which replied "I cannot run unit tests as I do
not have the ability to execute code or run commands." The coder took that as
the outcome and stopped without writing, so the empty-diff guard (OPEN-13)
blocked a task that had real work to do.

`_CODER_PROMPT` already said to finish the writing work rather than look for
another way. `task` was the other way, and it was in the tool list.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

DELEGATION_TOOL = "task"
"""deepagents' name for the subagent-dispatch tool (graph.py:829)."""


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class DelegationGuardMiddleware(AgentMiddleware):
    """Withhold `task` from a subagent whose spec does not grant delegation.

    Stateless. Constructed per agent from its spec, so the decision is made
    once at assembly and read on every model call.
    """

    def __init__(self, *, can_delegate: bool) -> None:
        super().__init__()
        self.can_delegate = can_delegate

    def _request(self, request):
        if self.can_delegate:
            return request
        tools = getattr(request, "tools", None)
        if not tools:
            return request
        kept = [tool for tool in tools if _tool_name(tool) != DELEGATION_TOOL]
        # Identity on the common path: every agent makes many model calls and
        # almost none of them carry `task`, so rebuilding the list each time
        # would churn objects for nothing.
        if len(kept) == len(tools):
            return request
        return request.override(tools=kept)

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))


__all__ = ["DELEGATION_TOOL", "DelegationGuardMiddleware"]
