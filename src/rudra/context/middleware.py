"""The middleware that feeds RunUsage.

Separate from usage.py because this imports langchain and that module must
stay importable by a test asserting it imports nothing from Rudra -- and
because the accumulator is worth testing without a middleware at all.

Why a middleware rather than the invocation funnel: `_stream_with_retry`
(permissions/approval.py:129) is the one place every model call passes
through, but it receives `agent, payload, config` and cannot tell whose
call it is. A middleware is constructed per agent, so it knows its role,
and it goes into the same two assembly sites the eviction budget uses.

This middleware observes and never rewrites. Accounting must not be the
thing that ends a run.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

# The tool SummarizationToolMiddleware registers (C7.4). Counted here
# rather than inside that middleware because the tally is per role, and
# only this middleware knows the role.
COMPACTION_TOOL = "compact_conversation"


class UsageMiddleware(AgentMiddleware):
    """Records one role's model calls into a shared RunUsage."""

    def __init__(self, role: str, usage: Any) -> None:
        super().__init__()
        self.role = role
        self.usage = usage

    def _record(self, response: Any) -> None:
        messages = getattr(response, "result", None) or []
        if not messages:
            return
        metadata = getattr(messages[0], "usage_metadata", None) or {}
        self.usage.record(
            self.role,
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
        )

    def _record_tool(self, request: Any) -> None:
        name = (getattr(request, "tool_call", None) or {}).get("name")
        if name == COMPACTION_TOOL:
            self.usage.record_compaction(self.role)

    def wrap_model_call(self, request, handler):
        response = handler(request)
        self._record(response)
        return response

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        self._record(response)
        return response

    def wrap_tool_call(self, request, handler):
        self._record_tool(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._record_tool(request)
        return await handler(request)


__all__ = ["COMPACTION_TOOL", "UsageMiddleware"]
