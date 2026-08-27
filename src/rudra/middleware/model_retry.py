"""Retry ONE model call, inside the graph (OPEN-41).

`permissions/approval.py::_stream_with_retry` already retries, and it is
not enough. It wraps a whole `agent.astream` -- one graph invocation, which
is dozens of model calls -- and its `yielded` guard stops retrying the
moment the agent emits its first chunk. Measured on run7: 119 model calls,
of which exactly one sat inside that guard. At the ~33% per-call failure
rate curl-probed on 2026-08-27, the chance of a run finishing is
indistinguishable from zero, and re-running does not help.

This closes the gap at the granularity that matters. A middleware, not
`BaseChatModel.with_retry()`, for a measured reason: `with_retry()` returns
a `RunnableRetry`, which has neither `bind_tools` nor `profile` -- both of
which deepagents calls on the object `llm/factory.py::build_model` returns.
Wrapping there would break graph construction. `wrap_model_call` is
langchain's own documented place for this ("Can be called multiple times
for retry logic", `AgentMiddleware.wrap_model_call`), and it is the hook
`context/middleware.py::UsageMiddleware` already uses.

It is NOT a second policy. `is_transient` and `retry_delays` come from
`llm/retry.py`, the same two functions the stream-level retry calls, so a
status code is transient in exactly one place.

Retrying here is safe in a way retrying the stream is not: a model call
that raised produced no message, so no tool has run and no state has moved.
That is the whole reason this granularity works without resume (C7.2).

Composes with `_stream_with_retry` rather than replacing it: the stream
retry still covers a failure that kills the invocation before any chunk,
and this covers every call inside it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rudra.llm.retry import ProviderUnavailable, is_transient, retry_delays


class ModelRetryMiddleware(AgentMiddleware):
    """Re-issues a single transient-failed model call, then gives up loudly.

    `role` is carried only so the error names which endpoint stopped
    answering -- a run whose coder is failing and whose planner is fine is
    a different problem from one whose provider is down, and the message
    is the only place a user learns which they have.
    """

    def __init__(self, role: str = "agent") -> None:
        super().__init__()
        self.role = role

    def _provider(self) -> str:
        return f"the {self.role} model"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        delays = retry_delays()
        for attempt in range(len(delays) + 1):
            try:
                return handler(request)
            except Exception as error:  # noqa: BLE001 -- re-raised below
                if not is_transient(error) or attempt == len(delays):
                    if is_transient(error):
                        raise ProviderUnavailable(self._provider(), attempt + 1, error) from error
                    raise
                time.sleep(delays[attempt])
        raise AssertionError("unreachable")  # pragma: no cover

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        delays = retry_delays()
        for attempt in range(len(delays) + 1):
            try:
                return await handler(request)
            except Exception as error:  # noqa: BLE001 -- re-raised below
                if not is_transient(error) or attempt == len(delays):
                    if is_transient(error):
                        raise ProviderUnavailable(self._provider(), attempt + 1, error) from error
                    raise
                await asyncio.sleep(delays[attempt])
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ModelRetryMiddleware"]
