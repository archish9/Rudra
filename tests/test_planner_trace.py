"""The planner prints through the shared renderer now (Step 15a, C9.1).

Its own loop carried A1.20's hole and said so in its docstring
(planner_agent.py:497-499, "It still carries A1.20's remaining half").
This asserts the hole is closed on the planner path too, and that its two
guards -- the planning-call limit and the consecutive-failure counter --
still fire.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.agent.planner_agent import _stream_planner_turn
from rudra.trace import TraceKind, TraceLevel
from rudra.trace.sink import TraceSink


async def _run(monkeypatch, chunks, sink):
    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr("rudra.agent.planner_agent.run_with_approvals", fake_stream)

    return await _stream_planner_turn(
        object(),
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=sink,
    )


async def test_planner_messages_reach_the_sink(monkeypatch):
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])

    ok = await _run(
        monkeypatch, [((), {"messages": [AIMessage(content="here is the plan")]})], sink
    )

    assert ok
    assert [event.payload for event in seen] == ["here is the plan"]
    assert seen[0].role == "planner"


async def test_a_planner_subagent_does_not_swallow_the_parents_messages(monkeypatch):
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    parent = [AIMessage(content="p1"), AIMessage(content="p2")]
    chunks = [
        ((), {"messages": parent[:1]}),
        (("task:1",), {"messages": [AIMessage(content="c1")]}),
        ((), {"messages": parent}),
    ]

    await _run(monkeypatch, chunks, sink)

    assert [event.payload for event in seen] == ["p1", "c1", "p2"]


async def test_the_planning_call_guard_still_halts(monkeypatch):
    call = {"name": "add_tasks", "args": {}, "id": "1"}
    messages = [AIMessage(content="", tool_calls=[call]) for _ in range(4)]
    chunks = [((), {"messages": messages[: n + 1]}) for n in range(4)]

    ok = await _run(monkeypatch, chunks, TraceSink(level=TraceLevel.NORMAL))

    assert ok is False


async def test_the_consecutive_failure_guard_still_halts(monkeypatch):
    failures = [
        ToolMessage(content="Error: boom", tool_call_id=str(n), name="write_file") for n in range(3)
    ]
    chunks = [((), {"messages": failures[: n + 1]}) for n in range(3)]

    ok = await _run(monkeypatch, chunks, TraceSink(level=TraceLevel.NORMAL))

    assert ok is False


async def test_a_denied_tool_call_now_counts_as_a_failure(monkeypatch):
    """The planner's own marker list could not see `BLOCKED:` at all
    (planner_agent.py:555-560 -- four markers, none of them that one), so
    three consecutive denials never tripped its guard. The shared list
    does see it."""
    denials = [
        ToolMessage(content="BLOCKED: permission denied", tool_call_id=str(n), name="write_file")
        for n in range(3)
    ]
    chunks = [((), {"messages": denials[: n + 1]}) for n in range(3)]

    ok = await _run(monkeypatch, chunks, TraceSink(level=TraceLevel.NORMAL))

    assert ok is False


async def test_a_turn_without_a_sink_still_runs(monkeypatch):
    """consult_planner's callers built no sink before Step 15a."""
    ok = await _run(monkeypatch, [((), {"messages": [AIMessage(content="fine")]})], None)
    assert ok


async def test_tool_errors_are_rendered_as_errors(monkeypatch):
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET, consumers=[seen.append])
    chunks = [
        ((), {"messages": [ToolMessage(content="Error: boom", tool_call_id="1", name="add_tasks")]})
    ]

    await _run(monkeypatch, chunks, sink)

    assert [event.kind for event in seen] == [TraceKind.TOOL_ERROR]
