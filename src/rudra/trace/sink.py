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

from rudra.trace.events import TraceEvent, TraceKind, TraceLevel
from rudra.trace.redact import redact
from rudra.trace.render import render
from rudra.trace.stream import StreamState, consume

Consumer = Callable[[TraceEvent], None]


@dataclass
class TraceSink:
    """One run's event bus.

    Two fan-outs, because two kinds of consumer want different things:

    `consumers` render, and are filtered to `level` -- the console draws
    what the user asked to see.

    `recorders` record, and are fed **every** event whatever the level.
    That does not reopen what this module's docstring rules out. The
    objection there is to a consumer *deciding for itself* what to keep,
    which lets two views of one run disagree; a recorder decides nothing
    and keeps all of it, so it can only ever be a superset of the console.
    Without this, the file `--debug` writes could not contain more than the
    console printed, and `--no-verbose` silently reduced the bug-report log
    to errors (OPEN-7).
    """

    level: TraceLevel = TraceLevel.NORMAL
    consumers: list[Consumer] = field(default_factory=list)
    recorders: list[Consumer] = field(default_factory=list)
    # Prose token streaming delivered per namespace since that namespace's
    # last values chunk -- see `note_streamed`.
    streamed: dict[tuple[str, ...], str] = field(default_factory=dict)

    def add(self, consumer: Consumer) -> None:
        self.consumers.append(consumer)

    def add_recorder(self, consumer: Consumer) -> None:
        """Register a consumer that receives every event, unfiltered."""
        self.recorders.append(consumer)

    def note_streamed(self, namespace: tuple[str, ...], text: str) -> None:
        """Record what token streaming said in `namespace` (OPEN-124).

        `permissions/approval.py` calls this for every values chunk, just
        before yielding it, with the prose it emitted there since the
        namespace's previous one -- so `feed` does not record the chunk's
        whole AIMessage a second time. A note belongs to the one chunk the
        caller feeds next, and `feed` clears every note once it has read
        them: a stale one would skip a message nobody streamed. A caller
        that never streams never calls this, and `feed` behaves as it
        always did.
        """
        self.streamed[tuple(namespace)] = text

    def emit(self, event: TraceEvent) -> None:
        """Feed the recorders, then filter once and fan out. Broken ones skip.

        Swallowing follows write_usage_log (loop/engine.py:501-515): a run
        that did its work must not be reported failed because its own
        bookkeeping raised.
        """
        for recorder in self.recorders:
            try:
                recorder(event)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                continue
        if not render(event, level=self.level):
            return
        for consumer in self.consumers:
            try:
                consumer(event)
            except Exception:  # noqa: BLE001 -- observability never ends a run
                continue

    def notice(
        self,
        payload: str,
        *,
        role: str,
        name: str = "",
        namespace: tuple[str, ...] = (),
        index: int = 0,
        at: float = 0.0,
    ) -> TraceEvent:
        """Emit one thing RUDRA did, and return it (OPEN-44).

        `feed` is for langgraph chunks; a guard halt or a retry has no
        chunk to parse, and before this there was no way to say one at
        all -- so the debug log, which is meant to be the COMPLETE record
        (CLAUDE.md §3), held nothing about six killed coder invocations in
        run bf6be7525991.

        Redacted here for the reason stream.py redacts where it builds
        (A1.95): one rule covering the console, the debug log and the
        transcript, rather than a clean screen over a debug file holding
        a credential.
        """
        event = TraceEvent(
            kind=TraceKind.NOTICE,
            role=role,
            namespace=namespace,
            index=index,
            name=name,
            payload=redact(payload),
            at=at,
        )
        self.emit(event)
        return event

    def feed(self, chunk: Any, state: StreamState) -> list[TraceEvent]:
        """Consume one stream chunk and emit everything new in it.

        Returns what it produced -- before filtering -- so a caller that
        wants the events for its own bookkeeping does not have to parse
        the chunk a second time.
        """
        events = consume(chunk, state, self.streamed)
        # A note describes the one chunk yielded right after it (OPEN-124).
        self.streamed.clear()
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
        # Printed, not recorded: this text IS an event, and the debug log
        # already holds it whole (OPEN-76). Recording the rendering too
        # would store a wrapped, truncated copy of a complete record.
        from rudra.trace.console_log import suppress_console_record

        with suppress_console_record():
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
