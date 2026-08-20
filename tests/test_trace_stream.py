"""Chunk -> events, with one counter per subgraph namespace (A1.20).

Rudra streams `subgraphs=True` (permissions/approval.py:156), so chunks
arrive as `(namespace, event)`. Parent and subagent carry SEPARATE
`messages` lists. One shared counter -- what runner.py:157 and
planner_agent.py:501 both did -- advances past the shorter list and
silently drops messages.

`test_interleaved_namespaces_lose_nothing` is that bug, executable: no
single-counter implementation passes it.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from rudra.trace import TraceKind
from rudra.trace.stream import StreamState, consume, looks_like_error


def _chunk(namespace, messages):
    return (namespace, {"messages": messages})


def test_a_bare_dict_chunk_is_treated_as_the_parent_namespace():
    state = StreamState(role="planner")
    events = consume({"messages": [HumanMessage(content="build a parser")]}, state)
    assert [event.kind for event in events] == [TraceKind.USER]
    assert events[0].namespace == ()


def test_a_tool_call_becomes_one_event_per_call():
    state = StreamState(role="coder")
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"},
            {"name": "execute", "args": {"command": "pytest"}, "id": "2"},
        ],
    )
    events = consume(_chunk((), [message]), state)
    assert [event.name for event in events] == ["write_file", "execute"]
    assert all(event.kind is TraceKind.TOOL_CALL for event in events)


def test_an_error_tool_message_is_an_error_event():
    state = StreamState(role="coder")
    chunk = _chunk((), [ToolMessage(content="Error: boom", tool_call_id="1", name="execute")])
    assert consume(chunk, state)[0].kind is TraceKind.TOOL_ERROR


def test_a_successful_tool_message_is_a_result_event():
    state = StreamState(role="coder")
    chunk = _chunk((), [ToolMessage(content="Wrote a.py", tool_call_id="1", name="write_file")])
    assert consume(chunk, state)[0].kind is TraceKind.TOOL_RESULT


def test_a_message_already_seen_is_never_emitted_twice():
    state = StreamState(role="coder")
    first = AIMessage(content="one")
    consume(_chunk((), [first]), state)
    events = consume(_chunk((), [first, AIMessage(content="two")]), state)
    assert [event.payload for event in events] == ["two"]


def test_interleaved_namespaces_lose_nothing():
    """A1.20, executable. A single counter fails this and nothing else does."""
    state = StreamState(role="coder")
    parent = [AIMessage(content="p1"), AIMessage(content="p2"), AIMessage(content="p3")]
    child = [AIMessage(content="c1")]

    seen = []
    seen += [event.payload for event in consume(_chunk((), parent[:2]), state)]
    seen += [event.payload for event in consume(_chunk(("task:1",), child), state)]
    seen += [event.payload for event in consume(_chunk((), parent), state)]

    assert seen == ["p1", "p2", "c1", "p3"]


def test_the_namespace_rides_on_every_event_from_a_subgraph():
    state = StreamState(role="coder")
    events = consume(_chunk(("task:1", "tools"), [AIMessage(content="hi")]), state)
    assert events[0].namespace == ("task:1", "tools")


def test_offsets_increase_within_a_run():
    state = StreamState(role="coder")
    first = consume(_chunk((), [AIMessage(content="a")]), state)[0]
    second = consume(_chunk((), [AIMessage(content="a"), AIMessage(content="b")]), state)[0]
    assert second.at >= first.at


def test_an_empty_assistant_turn_produces_nothing():
    """A tool-calling turn with no prose is not a blank AI line."""
    state = StreamState(role="coder")
    assert consume(_chunk((), [AIMessage(content="   ")]), state) == []


def test_the_error_markers_are_one_list_not_two():
    """runner.py carried seven markers, planner_agent.py four -- so the
    planner's failure counter could not see BLOCKED: at all."""
    assert looks_like_error("Error: boom")
    assert looks_like_error("BLOCKED: permission denied")
    assert looks_like_error("some preamble\nInput should be a valid string")
    assert not looks_like_error("Wrote a.py")
