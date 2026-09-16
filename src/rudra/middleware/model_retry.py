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

Since OPEN-61 it also carries the run's evidence that a role's endpoint
works. A `404` is not transient on the first call of a run -- that is a
missing model, and retrying it four times buries the one message the user
could act on -- but it cannot mean "no such model" once that model has
answered, and `https://integrate.api.nvidia.com/v1` was measured returning
`404` and then `200` for the same id one second apart. So this middleware
records every answered call and passes `served=` to `is_transient`, which
still owns the policy: the fact is collected here because this is where a
model call succeeds, and it is judged there because that is where a status
code is judged.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rudra.llm.retry import (
    ProviderUnavailable,
    is_transient,
    retry_delays,
    retry_wait,
    status_of,
)


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
        self._served = False

    def _provider(self) -> str:
        return f"the {self.role} model"

    def _mark_served(self) -> None:
        """Record that this role's endpoint answered (OPEN-61).

        Both halves, because neither alone covers a run. The instance flag
        is what works with `usage=None`, which every OPEN-41 test builds and
        which the machinery must stay constructible as. The RunUsage set is
        what survives an agent REBUILD -- the planner constructs a fresh
        agent, and so a fresh middleware, per stage (`planner_agent.py:338`
        is called once per `create_planner_agent`), and RUN #7's first
        attempt died on the first call of the THIRD stage after two stages
        had been served. An instance flag alone would have been False there,
        which is the case this exists for.
        """
        self._served = True
        if self.usage is None:
            return
        try:
            self.usage.record_served(self.role)
        except Exception:  # noqa: BLE001 -- observability never ends a run
            pass

    def _has_served(self) -> bool:
        """Has anything in this run had a call to this role answered?"""
        if self._served:
            return True
        if self.usage is None:
            return False
        try:
            return bool(self.usage.has_served(self.role))
        except Exception:  # noqa: BLE001 -- a usage object that cannot
            # answer is not evidence, and the caller falls back to the
            # narrow policy rather than to a crash.
            return False

    @staticmethod
    def _detail(error: BaseException) -> str:
        """The exception CLASS and its status, and nothing else.

        Never `str(error)`: a provider error body can echo the request
        back, and trace/render.py escapes but does not redact.
        """
        status = status_of(error)
        detail = type(error).__name__
        if status is not None:
            detail += f" ({status})"
        return detail

    def _notice(self, payload: str, name: str) -> None:
        """Say one thing to the trace, or to nobody.

        Swallowing follows TraceSink.emit and write_usage_log
        (loop/engine.py): a run that is already surviving a provider
        failure must not then die of its own bookkeeping -- and here it
        must not die of the WRONG exception either, since the loop reads
        ProviderUnavailable.
        """
        if self.trace is None:
            return
        try:
            self.trace.notice(
                payload,
                role=self.role,
                # VERBOSE on screen, and in the debug log at every level --
                # a retry annotates a run rather than reporting on it, so
                # one console line per flaky call would bury the trace it
                # is annotating (OPEN-44's choice, inherited).
                name=name,
            )
        except Exception:  # noqa: BLE001 -- observability never ends a run
            pass

    def _report(
        self, error: BaseException, attempt: int, budget: int, directed: float | None = None
    ) -> None:
        """Say that a retry is about to happen, to whoever is listening.

        Called once per retry ACTUALLY MADE. The give-up is reported by
        `_report_exhaustion` instead, under its own name -- see there for
        why this function used to be the only one.

        `directed` is a wait the ENDPOINT chose through `Retry-After`
        (OPEN-115), and only then is it named: up to two minutes of silence
        is otherwise indistinguishable from a hang in the debug log, while
        the ordinary 1-4 s backoff is not worth a word.
        """
        if self.usage is not None:
            try:
                self.usage.record_retry(self.role)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                pass
        waiting = (
            ""
            if directed is None
            else f", after waiting {directed:g}s as the endpoint's Retry-After asked"
        )
        self._notice(
            f"{self._provider()} failed with {self._detail(error)}; "
            f"retrying, attempt {attempt} of {budget}{waiting}",
            "retry",
        )

    @staticmethod
    def _wait(error: BaseException, backoff: float) -> tuple[float, float | None]:
        """The sleep before the next attempt, and whether the endpoint chose it.

        Since OPEN-115 this layer is the only one that retries, so it waits
        out `Retry-After` the way the client SDKs used to (`retry_wait`).
        """
        wait = retry_wait(error, backoff)
        return wait, (wait if wait != backoff else None)

    def _report_exhaustion(self, error: BaseException, attempts: int) -> None:
        """Say that the budget ran out (OPEN-46, reopened).

        This used to report nothing. `_report`'s own docstring said the
        give-up "already raises ProviderUnavailable and reaches the user
        as a sentence", and that a second channel for one fact is how two
        descriptions drift. **The premise is false on the subagent path.**
        `subagents/runner.py:264` catches every exception into
        `SubagentResult.error`, and `loop/engine.py:463` writes it to
        `task.note` -- which every branch that finishes a task rewrites.
        So run14's exhaustion cost task t8 outright and left zero record
        in the debug log, and `p = retries / calls` was a lower bound
        with nothing saying by how much.

        The name is `retry-exhausted` and NOT `retry`, deliberately: the
        per-attempt histograms in this project's own ledger scripts count
        `retry` notices, and a give-up filed under that name would be
        absorbed into the very rate it exists to correct.

        The payload carries the class and status only, on `_detail`'s
        rule, and the count of attempts -- which is what separates a
        budget that was nearly enough from an endpoint that never
        answered at all.
        """
        if self.usage is not None:
            try:
                self.usage.record_exhaustion(self.role)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                pass
        self._notice(
            f"{self._provider()} failed with {self._detail(error)}; "
            f"giving up after {attempts} attempt(s)",
            "retry-exhausted",
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        delays = retry_delays()
        for attempt in range(len(delays) + 1):
            try:
                response = handler(request)
            except Exception as error:  # noqa: BLE001 -- re-raised below
                transient = is_transient(error, served=self._has_served())
                if not transient or attempt == len(delays):
                    if transient:
                        # Reported before it is raised: on the subagent
                        # path nothing downstream will (OPEN-46).
                        self._report_exhaustion(error, attempt + 1)
                        raise ProviderUnavailable(self._provider(), attempt + 1, error) from error
                    raise
                wait, directed = self._wait(error, delays[attempt])
                self._report(error, attempt + 1, len(delays), directed)
                time.sleep(wait)
            else:
                self._mark_served()
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        delays = retry_delays()
        for attempt in range(len(delays) + 1):
            try:
                response = await handler(request)
            except Exception as error:  # noqa: BLE001 -- re-raised below
                transient = is_transient(error, served=self._has_served())
                if not transient or attempt == len(delays):
                    if transient:
                        # The async half is the SUBAGENT path -- the one
                        # where the sentence never reaches a user at all.
                        self._report_exhaustion(error, attempt + 1)
                        raise ProviderUnavailable(self._provider(), attempt + 1, error) from error
                    raise
                wait, directed = self._wait(error, delays[attempt])
                self._report(error, attempt + 1, len(delays), directed)
                await asyncio.sleep(wait)
            else:
                self._mark_served()
                return response
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ModelRetryMiddleware"]
