"""Where events go: filtered once, then handed to every consumer.

Shared by reference across a run, for the reason the gate, the FactStore
and the Ledger are (subagents/runner.py:51-55): two sinks would mean two
levels and, once 15c lands, two transcripts of one run.

The filter is here rather than in each consumer on purpose. A consumer
that decided for itself what to keep would let the console and the debug
log disagree about what happened, which is the one thing a bug report
cannot survive.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rudra.trace.events import TraceEvent, TraceLevel
from rudra.trace.render import render
from rudra.trace.stream import StreamState, consume

Consumer = Callable[[TraceEvent], None]


@dataclass
class TraceSink:
    """One run's event bus."""

    level: TraceLevel = TraceLevel.NORMAL
    consumers: list[Consumer] = field(default_factory=list)

    def add(self, consumer: Consumer) -> None:
        self.consumers.append(consumer)

    def emit(self, event: TraceEvent) -> None:
        """Filter once, then fan out. A broken consumer is skipped.

        Swallowing follows write_usage_log (loop/engine.py:501-515): a run
        that did its work must not be reported failed because its own
        bookkeeping raised.
        """
        if not render(event, level=self.level):
            return
        for consumer in self.consumers:
            try:
                consumer(event)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                continue

    def feed(self, chunk: Any, state: StreamState) -> list[TraceEvent]:
        """Consume one stream chunk and emit everything new in it.

        Returns what it produced -- before filtering -- so a caller that
        wants the events for its own bookkeeping does not have to parse
        the chunk a second time.
        """
        events = consume(chunk, state)
        for event in events:
            self.emit(event)
        return events


def console_consumer(console: Any, level: TraceLevel = TraceLevel.NORMAL) -> Consumer:
    """Print events to a Rich console at `level`.

    The level is passed in rather than read back off the sink so this
    stays a plain function of (console, level): the sink decides WHAT is
    emitted, the consumer decides how much of it to draw, and neither
    reaches into the other.
    """

    def consume_event(event: TraceEvent) -> None:
        for line in render(event, level=level):
            console.print(line)

    return consume_event


def resolve_level(flag: bool | None, configured: bool) -> TraceLevel:
    """Three-state, the shape cli.py already uses for --verbose (A1.15).

    Absent -> consult config; --verbose -> VERBOSE; --no-verbose -> QUIET.
    QUIET rather than NORMAL for the explicit negative: a user who typed
    --no-verbose asked for less than the default, and errors are the floor
    because a silent failure is the one thing they did not ask for.
    """
    if flag is True:
        return TraceLevel.VERBOSE
    if flag is False:
        return TraceLevel.QUIET
    return TraceLevel.VERBOSE if configured else TraceLevel.NORMAL


__all__ = ["Consumer", "TraceSink", "console_consumer", "resolve_level"]
