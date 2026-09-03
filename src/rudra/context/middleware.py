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

import logging
import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

# The tool SummarizationToolMiddleware registers (C7.4). Counted here
# rather than inside that middleware because the tally is per role, and
# only this middleware knows the role.
COMPACTION_TOOL = "compact_conversation"

_LOG = logging.getLogger("rudra.context.usage")
"""Where one line per model call goes (OPEN-87).

Under the `rudra` tree, so `trace/debug.py` picks it up with no new
plumbing and it lands in `debug-<id>.jsonl` -- the file the troubleshooting
docs tell a user to attach to a bug report. It is NOT a TraceEvent: a
trace event describes something the model or Rudra *did*, and this is a
measurement of how long the doing took. `_JsonLines.format` promotes
`record.event` to the top level of the object, so this is a first-class
line in that file rather than a string somebody has to parse out of a
message.
"""

MODEL_CALL_KIND = "model_call"
"""The `kind` these lines carry, so a reader can filter them apart from
trace events with `grep '"kind": "model_call"'` and nothing cleverer.

Named rather than spelled inline because it is the string a maintainer
will be told to grep for, and a second spelling of it is a second answer
to "why is my filter empty".
"""


def log_model_call(
    role: str,
    seconds: float,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    ok: bool = True,
    error: str = "",
) -> None:
    """Write one model call's cost to the debug log. Never raises.

    OPEN-87: `RoleUsage.seconds` was the only place this number existed,
    it was a per-role total, and it reached disk once, at run end. So the
    question every slow-run report asks -- *which* calls were slow, and
    what was the agent doing when they were -- could only be answered by
    differencing `ts` across adjacent lines of the debug log and knowing
    which adjacencies were model calls. Run `fc543fb2b82f` was diagnosed
    exactly that way, with a throwaway parser; a user filing a bug will
    not write one.

    Swallowing follows write_usage_log's rule (loop/engine.py): a run that
    did its work must not fail because its own bookkeeping could not be
    written.
    """
    try:
        _LOG.debug(
            "model call",
            extra={
                "event": {
                    "kind": MODEL_CALL_KIND,
                    "role": role,
                    # Milliseconds are noise at this scale and full floats
                    # make the file harder to read by eye, which is the
                    # whole point of the item.
                    "seconds": round(float(seconds), 2),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "ok": ok,
                    "error": error,
                }
            },
        )
    except Exception:  # noqa: BLE001 - accounting must not end a run
        return


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
            log_model_call(self.role, seconds)
            return
        metadata = getattr(messages[0], "usage_metadata", None) or {}
        self.usage.record(
            self.role,
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            seconds=seconds,
        )
        # Beside the tally, never instead of it. usage.json answers "what
        # did this role cost in total"; this answers "which call, and when"
        # (OPEN-87). A per-role total cannot show that one invocation spent
        # 2,704 s on 55 calls while its five siblings spent 255 s on five.
        log_model_call(
            self.role,
            seconds,
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
        )

    def _record_tool(self, request: Any) -> None:
        name = (getattr(request, "tool_call", None) or {}).get("name")
        if name == COMPACTION_TOOL:
            self.usage.record_compaction(self.role)

    def wrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = handler(request)
        except BaseException as exc:
            # A call that raised still cost the user the wait. Recorded
            # before re-raising, because a run that dies slowly is when
            # "where did the time go" is hardest to answer afterwards.
            self._record_failure(time.perf_counter() - started, type(exc).__name__)
            raise
        self._record(response, time.perf_counter() - started)
        return response

    async def awrap_model_call(self, request, handler):
        started = time.perf_counter()
        try:
            response = await handler(request)
        except BaseException as exc:
            self._record_failure(time.perf_counter() - started, type(exc).__name__)
            raise
        self._record(response, time.perf_counter() - started)
        return response

    def _record_failure(self, seconds: float, error: str = "") -> None:
        self.usage.record(self.role, input_tokens=None, output_tokens=None, seconds=seconds)
        # A failed call is the one most worth naming: it cost the wait AND
        # bought nothing, and `RoleUsage` cannot tell it apart from a
        # successful one that reported no tokens. Run `fc543fb2b82f` took
        # four provider 500s and the debug log recorded the retry notices
        # with no cost attached to either the failure or the re-issue.
        log_model_call(self.role, seconds, ok=False, error=error)

    def wrap_tool_call(self, request, handler):
        self._record_tool(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._record_tool(request)
        return await handler(request)


__all__ = ["COMPACTION_TOOL", "MODEL_CALL_KIND", "UsageMiddleware", "log_model_call"]
