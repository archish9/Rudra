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

import time
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

    def _record(self, response: Any, seconds: float) -> None:
        messages = getattr(response, "result", None) or []
        if not messages:
            # No message means no token counts, but the call still took
            # time -- and a provider that returns nothing slowly is
            # exactly what somebody would be trying to diagnose.
            self.usage.record(self.role, input_tokens=None, output_tokens=None, seconds=seconds)
            return
        metadata = getattr(messages[0], "usage_metadata", None) or {}
        self.usage.record(
            self.role,
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            seconds=seconds,
        )

    def _record_tool(self, request: Any) -> None:
        name = (getattr(request, "tool_call", None) or {}).get("name")
        if name == COMPACTION_TOOL:
            self.usage.record_compaction(self.role)

    def wrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = handler(request)
        except BaseException:
            # A call that raised still cost the user the wait. Recorded
            # before re-raising, because a run that dies slowly is when
            # "where did the time go" is hardest to answer afterwards.
            self._record_failure(time.perf_counter() - started)
            raise
        self._record(response, time.perf_counter() - started)
        return response

    async def awrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = await handler(request)
        except BaseException:
            self._record_failure(time.perf_counter() - started)
            raise
        self._record(response, time.perf_counter() - started)
        return response

    def _record_failure(self, seconds: float) -> None:
        self.usage.record(self.role, input_tokens=None, output_tokens=None, seconds=seconds)

    def wrap_tool_call(self, request, handler):
        self._record_tool(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._record_tool(request)
        return await handler(request)


__all__ = ["COMPACTION_TOOL", "UsageMiddleware"]
