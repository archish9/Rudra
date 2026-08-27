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

from rudra.llm.retry import ProviderUnavailable, is_transient, retry_delays, status_of


class ModelRetryMiddleware(AgentMiddleware):
    """Re-issues a single transient-failed model call, then gives up loudly.

    `role` is carried only so the error names which endpoint stopped
    answering -- a run whose coder is failing and whose planner is fine is
    a different problem from one whose provider is down, and the message
    is the only place a user learns which they have.

    `trace` and `usage` are what stop this from being a silent guard
    (OPEN-45). It shipped with neither, so a run absorbing a third of its
    requests was indistinguishable from a slow model, and RUN #2 could not
    confirm the very item it was being run to confirm. Both are optional
    for the reason SubagentContext.trace is: the machinery must stay
    constructible without a run, and every OPEN-41 test builds it that way.
    """

    def __init__(self, role: str = "agent", *, trace: Any = None, usage: Any = None) -> None:
        super().__init__()
        self.role = role
        self.trace = trace
        self.usage = usage

    def _provider(self) -> str:
        return f"the {self.role} model"

    def _report(self, error: BaseException, attempt: int, budget: int) -> None:
        """Say that a retry is about to happen, to whoever is listening.

        Called once per retry ACTUALLY MADE -- never on the give-up, which
        already raises ProviderUnavailable and reaches the user as a
        sentence. A second channel for one fact is how two descriptions of
        one event drift.

        The payload carries the exception CLASS and its status, never
        `str(error)`: a provider error body can echo the request back, and
        trace/render.py escapes but does not redact.

        Swallowing follows TraceSink.emit and write_usage_log
        (loop/engine.py): a run that is already surviving a provider
        failure must not then die of its own bookkeeping.
        """
        if self.usage is not None:
            try:
                self.usage.record_retry(self.role)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                pass
        if self.trace is None:
            return
        status = status_of(error)
        detail = type(error).__name__
        if status is not None:
            detail += f" ({status})"
        try:
            self.trace.notice(
                f"{self._provider()} failed with {detail}; retrying, attempt {attempt} of {budget}",
                role=self.role,
                # VERBOSE on screen, and in the debug log at every level --
                # a retry annotates a run rather than reporting on it, so
                # one console line per flaky call would bury the trace it
                # is annotating (OPEN-44's choice, inherited).
                name="retry",
            )
        except Exception:  # noqa: BLE001 -- observability never ends a run
            pass

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
                self._report(error, attempt + 1, len(delays))
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
                self._report(error, attempt + 1, len(delays))
                await asyncio.sleep(delays[attempt])
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ModelRetryMiddleware"]
