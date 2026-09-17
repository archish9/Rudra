"""One sink, many consumers (Step 15a, C9.1 / C9.7; 15c adds a third).

The level filter lives here and NOT in each consumer, so two rendering
consumers cannot disagree about what happened -- only about how much of
it to draw.

Recorders are the deliberate exception (OPEN-7): they are fed every event
before the filter runs. That is not the disagreement this design rules
out, because a recorder decides nothing -- it takes all of it, so it can
only ever be a superset of what the console drew.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from rich.console import Console

from rudra.trace import TraceEvent, TraceKind, TraceLevel
from rudra.trace.sink import TraceSink, console_consumer, resolve_level
from rudra.trace.stream import StreamState


def _event(kind=TraceKind.TOOL_CALL, **kwargs):
    base = {"role": "coder", "namespace": (), "index": 1, "name": "write_file", "payload": "{}"}
    return TraceEvent(kind=kind, **{**base, **kwargs})


def test_the_sink_fans_one_event_out_to_every_consumer():
    seen_a, seen_b = [], []
    sink = TraceSink(level=TraceLevel.NORMAL, consumers=[seen_a.append, seen_b.append])
    sink.emit(_event())
    assert len(seen_a) == 1 and len(seen_b) == 1


def test_quiet_filters_before_the_consumers_see_it():
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET, consumers=[seen.append])
    sink.emit(_event(TraceKind.TOOL_CALL))
    sink.emit(_event(TraceKind.TOOL_ERROR, payload="Error: boom"))
    assert [event.kind for event in seen] == [TraceKind.TOOL_ERROR]


def test_a_recorder_receives_what_the_level_filter_rejects():
    """OPEN-7, the defect in one assertion: the run log is registered here,
    and on a QUIET run it used to contain errors only -- a display setting
    silently deciding what a bug report would contain."""
    recorded, rendered = [], []
    sink = TraceSink(level=TraceLevel.QUIET, consumers=[rendered.append])
    sink.add_recorder(recorded.append)

    sink.emit(_event(TraceKind.TOOL_CALL))
    sink.emit(_event(TraceKind.TOOL_ERROR, payload="Error: boom"))

    assert [event.kind for event in rendered] == [TraceKind.TOOL_ERROR]
    assert [event.kind for event in recorded] == [TraceKind.TOOL_CALL, TraceKind.TOOL_ERROR]


def test_a_recorder_is_always_a_superset_of_the_consumers():
    """The property that makes the exception safe: a recorder cannot
    disagree with the console, only hold more than it."""
    for level in (TraceLevel.QUIET, TraceLevel.NORMAL, TraceLevel.VERBOSE):
        recorded, rendered = [], []
        sink = TraceSink(level=level, consumers=[rendered.append])
        sink.add_recorder(recorded.append)

        for kind in (TraceKind.TOOL_CALL, TraceKind.TOOL_RESULT, TraceKind.TOOL_ERROR):
            sink.emit(_event(kind, payload="Error: boom" if kind is TraceKind.TOOL_ERROR else "{}"))

        assert set(rendered) <= set(recorded), level


def test_a_broken_recorder_does_not_stop_the_consumers():
    """Same rule as a broken consumer, and it runs first, so a raising
    recorder must not swallow the console output behind it."""
    seen = []
    sink = TraceSink(level=TraceLevel.NORMAL, consumers=[seen.append])

    def explode(event):
        raise RuntimeError("recorder is broken")

    sink.add_recorder(explode)
    sink.emit(_event())

    assert len(seen) == 1


def test_a_consumer_that_raises_does_not_stop_the_run():
    """Observability must never be the thing that ends a run -- the rule
    UsageMiddleware states in its own docstring (context/middleware.py:14)
    and write_usage_log follows (loop/engine.py:501-515)."""

    def explode(event):
        raise RuntimeError("consumer is broken")

    seen = []
    sink = TraceSink(level=TraceLevel.NORMAL, consumers=[explode, seen.append])
    sink.emit(_event())
    assert len(seen) == 1


def test_feed_turns_a_chunk_into_emitted_events():
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    sink.feed(((), {"messages": [AIMessage(content="hello")]}), StreamState(role="coder"))
    assert [event.payload for event in seen] == ["hello"]


def test_feed_returns_what_it_produced_even_with_nothing_watching():
    """The return value is unfiltered on purpose: a caller that wants the
    events for its own bookkeeping should not have to parse the chunk a
    second time. NORMAL hides AI text, so nothing is drawn -- and the
    event still comes back."""
    sink = TraceSink(level=TraceLevel.NORMAL, consumers=[])
    events = sink.feed(((), {"messages": [AIMessage(content="hi")]}), StreamState(role="coder"))
    assert [event.payload for event in events] == ["hi"]


def test_the_console_consumer_prints_and_escapes():
    console = Console(record=True, width=200)
    sink = TraceSink(level=TraceLevel.NORMAL)
    sink.add(console_consumer(console, sink.level))
    sink.emit(_event(TraceKind.TOOL_RESULT, name="read_ledger", payload="t1  [pending]  parse"))
    assert "[pending]" in console.export_text()


def test_resolve_level_is_three_state():
    """--verbose wins, --no-verbose means errors only, absent consults config."""
    assert resolve_level(True, False) is TraceLevel.VERBOSE
    assert resolve_level(False, True) is TraceLevel.QUIET
    assert resolve_level(None, True) is TraceLevel.VERBOSE
    assert resolve_level(None, False) is TraceLevel.NORMAL


def test_feed_skips_prose_the_stream_already_delivered_in_that_namespace():
    """OPEN-124: `permissions/approval.py` notes what it streamed per namespace
    just before yielding that namespace's values chunk, so the chunk's whole
    AIMessage is not recorded a second time."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    state = StreamState(role="coder")

    sink.note_streamed((), "already said")
    sink.feed(((), {"messages": [AIMessage(content="already said")]}), state)
    sink.feed(
        ((), {"messages": [AIMessage(content="already said"), AIMessage(content="new")]}), state
    )

    assert [event.payload for event in seen] == ["new"]


def test_a_streamed_note_never_outlives_the_chunk_fed_after_it():
    """A note is about the ONE values chunk yielded right after it. Left in
    place, a later invocation that did not stream -- same namespace `()`,
    same closing words -- would lose its prose to it."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])

    sink.note_streamed((), "Done.")
    sink.feed(((), {"messages": [AIMessage(content="Done.")]}), StreamState(role="coder"))
    sink.feed(((), {"messages": [AIMessage(content="Done.")]}), StreamState(role="tester"))

    assert [(event.role, event.payload) for event in seen] == [("tester", "Done.")]
