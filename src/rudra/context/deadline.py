"""One span's seconds budget, and the variable a guard reads it from (OPEN-114).

A "span" is a planner stage or a subagent invocation -- the two things
`[agent] max_invocation_seconds` bounds. Both used to read the clock at the top
of their chunk loop and `break`, and a `values` chunk carrying an `AIMessage`
with tool calls is yielded **after the model node returns and before the tool
node has run**. So the bound fired at the one moment where stopping destroys the
most: immediately after paying for an answer, immediately before using it.

Run `8f160d92c6da` lost two finished `record_fact` calls that way -- 733.7 s
plus a 218.1 s retry in clarify, 549.4 s in architect -- and its clarify stage
ended with zero facts having produced one. `break` closes the async generator
langgraph's step loop lives inside, so the step that would have executed the
tools was cancelled or never scheduled.

The deadline is therefore read by a `before_model` hook instead
(`middleware/span_deadline.py`), which fires after the previous answer's tools
ran and before the next call is paid for. Checked against that run: the same
halt times (1888 s, 1467 s), both facts kept.

**Pure by rule: nothing here imports from Rudra** -- the `loop/bounds.py`
convention -- so the bound is testable without a graph.

The clock is injectable and defaults to this module's `time`, which is what
lets a test drive it: `SpanDeadline` is built by the span owners rather than
by their callers, so a test controls their clock by replacing the global they
default to. A monotonic clock and not a wall one, deliberately: this bounds
RUNNING time, the same question `usage.json`'s `counted_seconds` answers, and
a laptop that slept must not read as an agent that looped (OPEN-53).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class SpanDeadline:
    """How long a span may run, and whether a guard has stopped it yet.

    `limit` is in seconds; **0.0 means no bound**, which is how both
    `agent/planner_agent.py::_stage_time_limit` and
    `subagents/runner.py::_invocation_limit` already report a disabled one.

    `tripped_at` is the answer to "did the bound fire, and how far in" -- it
    is what the span owner announces with, so the sentence quotes the moment
    the guard acted rather than whenever the announcement was built.
    """

    limit: float
    clock: Callable[[], float] | None = None
    tripped_at: float | None = field(default=None, init=False)
    started: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.started = self._now()

    def _now(self) -> float:
        # Resolved through the module global so a test can replace the clock
        # for the two owners that build their own deadline.
        return self.clock() if self.clock is not None else time.monotonic()

    def elapsed(self) -> float:
        """Seconds since the span began."""
        return self._now() - self.started

    def expired(self) -> bool:
        """Is the span over its limit? Always False when there is no limit."""
        return bool(self.limit) and self.elapsed() >= self.limit

    def trip(self) -> float:
        """Record that a guard stopped the span, and say how far in.

        The FIRST trip wins. A jump to `end` leaves the graph, but a delegate
        subgraph shares this deadline (see the middleware), so two hooks can
        reach it -- and the elapsed seconds a halt reports must be when the
        bound fired, not when the last thing noticed.
        """
        if self.tripped_at is None:
            self.tripped_at = self.elapsed()
        return self.tripped_at


SPAN_DEADLINE: ContextVar[SpanDeadline | None] = ContextVar(
    "rudra_span_deadline",
    default=None,
)
"""The active span's deadline, or None.

A ContextVar rather than a constructor argument because the agents are
compiled before the span starts: planner stages in `agent/main_agent.py`
(per consult since OPEN-32) and subagents in `subagents/build.py`, while the
span begins later in `_stream_planner_turn` / `run_subagent`. A variable set
in the task that iterates `astream` is copied into the asyncio tasks langgraph
creates for its nodes, so neither builder needs a new parameter -- measured
through a real graph in `tests/test_span_deadline.py`, on both the plain
`create_agent` stack and the `create_deep_agent` one.

**Default None is the inert case and is load-bearing**: every agent built
outside a run streams with no span around it, and must behave exactly as it
did before this existed.
"""


@contextmanager
def span_deadline(
    limit: float, *, clock: Callable[[], float] | None = None
) -> Iterator[SpanDeadline]:
    """Run a span under `limit` seconds, resetting the variable on the way out.

    The reset lives here rather than at each owner because a leak is silent
    and expensive: `run_subagent` reports a raised exception rather than
    raising it, so a deadline left set would bound the NEXT invocation from
    the previous one's start.
    """
    deadline = SpanDeadline(limit, clock=clock)
    token = SPAN_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        SPAN_DEADLINE.reset(token)


__all__ = ["SPAN_DEADLINE", "SpanDeadline", "span_deadline"]
