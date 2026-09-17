"""Token streaming changes what the SINK sees, never what callers see.

approval.py's own docstring records why this has to be contained:
`__interrupt__` does not appear in "values" chunks, and switching stream
modes would change the shape both parse loops depend on. So the second
mode is consumed inside _stream_with_retry and yielded to nobody.

**Every test here drives a real langgraph graph (OPEN-124).** The double
these replaced yielded `("values", ((), {...}))` -- mode first, namespace
nested -- a shape langgraph never produces. With a list `stream_mode` and
`subgraphs=True` it yields `(namespace, mode, data)`, `_tagged` dropped
every chunk, and the suite passed for a month while `--stream` blinded
every guard in both parse loops. A double of an upstream stream shape is
a claim about upstream; these graphs are the check.

Off by default (S15.1 / D6): unmeasured against a 32B, and the flag is how
it gets measured.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool

from rudra.permissions.approval import run_with_approvals
from rudra.trace import TraceKind, TraceLevel
from rudra.trace.redact import REDACTED
from rudra.trace.sink import TraceSink
from rudra.trace.stream import StreamState

_TOOL_RESULT = "pong TOOLRESULTTEXT"


class _Streaming(GenericFakeChatModel):
    """Streams each scripted message in two deltas, split mid-line, with its
    tool calls on the last delta -- the way a provider streams a turn."""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        message = next(self.messages)
        half = len(message.content) // 2
        parts = (message.content[:half], message.content[half:])
        for index, part in enumerate(parts):
            calls = message.tool_calls if index == len(parts) - 1 else []
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=part,
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": position,
                        }
                        for position, call in enumerate(calls)
                    ],
                )
            )

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self


class _Whole(FakeMessagesListChatModel):
    """A model with no streaming implementation: langgraph's `messages` mode
    carries its answer as one whole `AIMessage`, never as chunks."""

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self


def _graph(model_class=_Streaming, outer=None, inner=None):
    """An agent whose tool runs a second agent: two namespaces, one stream."""
    inner_agent = create_agent(
        model_class(**_script(model_class, inner or ["inner says hello there"])),
        tools=[],
    )

    @tool
    async def ping(x: str) -> str:
        """Ask the inner agent."""
        await inner_agent.ainvoke({"messages": [("user", "sub")]})
        return _TOOL_RESULT

    outer = outer or [
        AIMessage(
            content="calling the tool now",
            tool_calls=[{"name": "ping", "args": {"x": "a"}, "id": "c1"}],
        ),
        "all done here\nsecond line",
    ]
    return create_agent(model_class(**_script(model_class, outer)), tools=[ping])


def _script(model_class, messages):
    messages = [m if isinstance(m, AIMessage) else AIMessage(content=m) for m in messages]
    if model_class is _Whole:
        return {"responses": messages}
    return {"messages": iter(messages)}


async def _drain(agent, **kwargs):
    inputs = {"messages": [("user", "hi")]}
    return [chunk async for chunk in run_with_approvals(agent, inputs, {}, None, None, **kwargs)]


async def _traced(agent, *, stream_tokens: bool):
    """Drive the stream the way `subagents/runner.py` does: every yielded
    chunk is fed to the sink before the next is pulled."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    state = StreamState(role="coder")
    inputs = {"messages": [("user", "hi")]}
    async for chunk in run_with_approvals(
        agent, inputs, {}, None, None, trace=sink, stream_tokens=stream_tokens, role="coder"
    ):
        sink.feed(chunk, state)
    return seen


def _shape(chunks):
    """What a parse loop reads off a chunk: whose it is, and what it holds."""
    shape = []
    for chunk in chunks:
        assert isinstance(chunk, tuple) and len(chunk) == 2, chunk
        namespace, event = chunk
        assert isinstance(namespace, tuple) and isinstance(event, dict), chunk
        shape.append((bool(namespace), [type(m).__name__ for m in event["messages"]]))
    return shape


def _prose(events):
    return [event for event in events if event.kind is TraceKind.AI_TEXT]


def _words(events):
    return " ".join(event.payload for event in events).split()


class _Recording:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.modes: Any = None

    def astream(self, payload, config, stream_mode=None, subgraphs=False):
        self.modes = stream_mode
        return self.agent.astream(payload, config, stream_mode=stream_mode, subgraphs=subgraphs)


async def test_without_the_flag_the_stream_mode_is_unchanged():
    agent = _Recording(_graph())
    await _drain(agent)
    assert agent.modes == "values"


async def test_with_the_flag_callers_see_exactly_the_values_stream():
    """OPEN-124. Before the fix the flagged side was EMPTY: every chunk was a
    3-tuple `_tagged` could not read, so both parse loops saw nothing while
    the graph ran its tools."""
    recording = _Recording(_graph())
    streamed = await _drain(recording, stream_tokens=True)
    plain = await _drain(_graph())

    assert recording.modes == ["values", "messages"]
    assert streamed, "no chunk reached the caller"
    assert _shape(streamed) == _shape(plain)


async def test_a_subgraph_chunk_keeps_its_namespace():
    streamed = await _drain(_graph(), stream_tokens=True)
    assert any(namespace for namespace, _event in streamed)


async def test_a_tool_result_is_never_streamed_as_the_models_prose():
    """langgraph's `messages` mode also carries the ToolMessage the tools node
    returns. Emitted as AI_TEXT it relabels every `read_file` body as prose."""
    seen = await _traced(_graph(), stream_tokens=True)

    assert not [event for event in _prose(seen) if "TOOLRESULTTEXT" in event.payload]
    assert [event.kind for event in seen if "TOOLRESULTTEXT" in event.payload] == [
        TraceKind.TOOL_RESULT
    ]


async def test_separate_messages_never_merge_into_one_line():
    """The held line was keyed by role alone and flushed only at a newline, so
    a message with no trailing newline waited for the next message's -- and
    the inner agent's and the tool's text landed in the same event."""
    seen = await _traced(_graph(), stream_tokens=True)

    assert _prose(seen)
    for event in _prose(seen):
        texts = ("calling the tool now", "inner says hello there", "all done here")
        assert sum(text in event.payload for text in texts) <= 1, event.payload


async def test_a_subgraphs_streamed_prose_carries_its_namespace():
    seen = await _traced(_graph(), stream_tokens=True)

    inner = [event for event in _prose(seen) if "inner says" in event.payload]
    outer = [event for event in _prose(seen) if "calling the tool" in event.payload]
    assert inner and all(event.namespace for event in inner)
    assert outer and all(event.namespace == () for event in outer)


async def test_streamed_prose_is_recorded_once_not_twice():
    """The whole AIMessage in the values chunk is the same text the deltas
    already delivered; recording both doubled every line on the console."""
    seen = await _traced(_graph(), stream_tokens=True)
    plain = await _traced(_graph(), stream_tokens=False)

    assert _words(_prose(seen)) == _words(_prose(plain))


async def test_streamed_prose_arrives_before_the_calls_it_announces():
    seen = await _traced(_graph(), stream_tokens=True)

    kinds = [(event.kind, event.payload) for event in seen]
    said = next(i for i, (kind, text) in enumerate(kinds) if "calling the tool" in text)
    called = next(i for i, (kind, _text) in enumerate(kinds) if kind is TraceKind.TOOL_CALL)
    assert said < called


async def test_a_model_that_does_not_stream_still_has_its_prose_recorded():
    """No deltas means nothing was streamed, so the values chunk's whole
    message is the only record and must not be skipped as a duplicate."""
    seen = await _traced(_graph(_Whole), stream_tokens=True)
    plain = await _traced(_graph(_Whole), stream_tokens=False)

    assert _words(_prose(seen)) == _words(_prose(plain))
    assert "calling" in _words(_prose(seen))


async def test_no_blank_line_is_emitted_for_an_empty_delta():
    """A tool-calling turn streams empty content deltas, and langgraph closes
    every stream with one; a blank line each would bury the trace."""
    seen = await _traced(_graph(), stream_tokens=True)
    assert _prose(seen)
    assert all(event.payload.strip() for event in _prose(seen))


async def test_a_credential_split_across_deltas_is_still_redacted():
    """CR-G6: a delta is redacted with the rest of its line, never alone --
    one key in three deltas once reached the console all but two characters
    whole."""
    key = "OPENROUTER_API_KEY=sk-or-v1-deadbeefcafebabe1234"
    seen = await _traced(_graph(outer=[f"here it is {key}\nand more"]), stream_tokens=True)

    text = " ".join(event.payload for event in seen)
    assert "deadbeef" not in text and "cafebabe" not in text
    assert REDACTED in text


async def test_the_flag_without_a_sink_is_harmless():
    assert _shape(await _drain(_graph(), stream_tokens=True)) == _shape(await _drain(_graph()))


def test_stream_tokens_defaults_to_off_in_config():
    from rudra.config.schema import DEFAULTS

    assert DEFAULTS["agent"]["stream_tokens"] is False
