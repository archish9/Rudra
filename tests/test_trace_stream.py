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
from rudra.trace.stream import (
    REFUSAL_KEY,
    StreamState,
    consume,
    is_rudra_refusal,
    looks_like_error,
    message_is_error,
)


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
    """Three copies had drifted: runner's 6-marker guard list, the
    planner's 4-marker guard list, and the planner renderer's 8
    conditions. The planner's guard could not see BLOCKED: at all."""
    assert looks_like_error("Error: boom")
    assert looks_like_error("BLOCKED: permission denied")
    assert not looks_like_error("Wrote a.py")


def test_a_failed_tool_is_read_from_its_status_not_its_text():
    """OPEN-16: langgraph sets status="error"; no string matching needed."""
    failed = ToolMessage(content="anything at all", tool_call_id="1", name="x", status="error")
    assert message_is_error(failed)


def test_a_tool_that_returns_an_error_string_still_counts():
    """deepagents' filesystem tools report a missing file by returning the
    text with a successful status, and the deny middleware returns
    "BLOCKED:" the same way. The text fallback is what catches those."""
    assert message_is_error(
        ToolMessage(content="Error: File '/a.py' not found", tool_call_id="1", name="read_file")
    )
    assert message_is_error(
        ToolMessage(content="BLOCKED: permission denied", tool_call_id="1", name="execute")
    )


def test_reading_a_file_that_quotes_a_tool_error_is_not_an_error():
    """OPEN-16, the defect itself.

    `"not a valid tool"` and `"Input should be a valid string"` used to be
    matched against the whole content. Rudra's own transcript records every
    tool error verbatim, so reading
    `.rudra/run/transcripts/<id>.jsonl` came back as an ERROR whose payload
    was the file -- and `runner.py`'s MAX_CONSECUTIVE_FAILURES counted it,
    so three successful reads halted the subagent.
    """
    transcript_line = (
        '{"kind": "tool_error", "role": "coder", "name": "execute", "payload": '
        '"Error: execute is not a valid tool, try one of [ls, read_file]."}'
    )
    quoting = ToolMessage(
        content=f"  1  {transcript_line}\n  2  Input should be a valid string\n",
        tool_call_id="1",
        name="read_file",
    )

    assert not message_is_error(quoting)

    state = StreamState(role="tester")
    (event,) = consume(_chunk((), [quoting]), state)
    assert event.kind is TraceKind.TOOL_RESULT


def test_prose_and_tool_calls_in_one_message_both_survive():
    """Models routinely say what they are about to do in the same message
    that does it. Emitting only the calls loses the one statement of
    intent the trace ever gets -- found by reading rendered output, not by
    a failing test."""
    state = StreamState(role="coder")
    message = AIMessage(
        content="I'll write the parser first.",
        tool_calls=[{"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"}],
    )

    events = consume(_chunk((), [message]), state)

    assert [event.kind for event in events] == [TraceKind.AI_TEXT, TraceKind.TOOL_CALL]
    assert events[0].payload == "I'll write the parser first."


def test_an_empty_content_field_beside_tool_calls_adds_no_line():
    state = StreamState(role="coder")
    message = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "1"}])
    assert [event.kind for event in consume(_chunk((), [message]), state)] == [TraceKind.TOOL_CALL]


def test_a_marked_refusal_is_not_a_tool_failure_whatever_its_text():
    """OPEN-94: a refusal is Rudra speaking, not a tool answering.

    `RepeatGuardMiddleware` short-circuits a call rather than running it,
    so no tool failed -- but its failure refusal leads with "Error:"
    because that is what the MODEL must read, and the text check counted
    it. Two of the three failures that halted coder invocation 1 of run
    2cde3406f7d6 at 11.67 s were this string. The classification moves off
    the prose and onto the message, so the model-facing text is untouched.
    """
    refusal = ToolMessage(
        content=(
            "Error: ls has already failed 2 times with these exact arguments, "
            "and was not run again."
        ),
        tool_call_id="1",
        name="ls",
        additional_kwargs={REFUSAL_KEY: True},
    )

    assert is_rudra_refusal(refusal) is True
    assert message_is_error(refusal) is False


def test_an_ordinary_tool_message_is_not_a_refusal():
    """The marker is opt-in: nothing Rudra did not invent carries it."""
    assert (
        is_rudra_refusal(ToolMessage(content="Error: boom", tool_call_id="1", name="ls")) is False
    )
    assert is_rudra_refusal(ToolMessage(content="fine", tool_call_id="1", name="ls")) is False


def test_a_marked_message_that_really_failed_is_still_a_failure():
    """The marker says "Rudra invented this", not "ignore this". A status
    of "error" comes from langgraph and outranks it -- nothing Rudra
    invents carries one, so the two cannot legitimately co-occur, and if
    they ever do the structural signal wins over the marker."""
    failed = ToolMessage(
        content="anything",
        tool_call_id="1",
        name="ls",
        status="error",
        additional_kwargs={REFUSAL_KEY: True},
    )
    assert message_is_error(failed) is True


# --- prose already streamed (OPEN-124) --------------------------------------


def test_prose_already_streamed_in_its_namespace_is_not_emitted_again():
    """With `--stream` the deltas ARE the prose record; the whole message in
    the values chunk would print every line a second time."""
    state = StreamState(role="coder")
    message = AIMessage(
        content="reading it now", tool_calls=[{"name": "ls", "args": {}, "id": "1"}]
    )

    events = consume(_chunk((), [message]), state, streamed={(): "reading it now"})

    assert [event.kind for event in events] == [TraceKind.TOOL_CALL]


def test_prose_streamed_in_another_namespace_is_still_emitted():
    state = StreamState(role="coder")
    events = consume(
        _chunk(("task:1",), [AIMessage(content="same words")]),
        state,
        streamed={(): "same words"},
    )
    assert [event.payload for event in events] == ["same words"]


def test_prose_that_was_not_what_streamed_is_still_emitted():
    """Nothing is skipped on a guess: a message is only dropped when its text
    is exactly what was already delivered, so the record can never lose it."""
    state = StreamState(role="coder")
    events = consume(
        _chunk((), [AIMessage(content="the full answer")]), state, streamed={(): "the"}
    )
    assert [event.payload for event in events] == ["the full answer"]


def test_a_retry_that_restreamed_the_message_is_still_recognised():
    """A model call re-issued mid-stream delivers its text twice in one node;
    the message in state is the second copy, which ends the streamed text."""
    state = StreamState(role="coder")
    events = consume(
        _chunk((), [AIMessage(content="final words")]),
        state,
        streamed={(): "final wofinal words\n"},
    )
    assert events == []
