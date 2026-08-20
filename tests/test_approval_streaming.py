"""Token streaming changes what the SINK sees, never what callers see.

approval.py's own docstring records why this has to be contained:
`__interrupt__` does not appear in "values" chunks, and switching stream
modes would change the shape both parse loops depend on. So the second
mode is consumed inside _stream_with_retry and yielded to nobody.

Off by default (S15.1 / D6): unmeasured against a 32B, and the flag is how
it gets measured.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk

from rudra.permissions.approval import run_with_approvals
from rudra.trace import TraceKind, TraceLevel
from rudra.trace.sink import TraceSink


class _Agent:
    """Records the stream_mode it was asked for and replays fixed chunks."""

    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.modes: Any = None

    async def astream(self, payload, config, stream_mode=None, subgraphs=False):
        self.modes = stream_mode
        for chunk in self.chunks:
            yield chunk


async def _drain(agent, **kwargs):
    return [chunk async for chunk in run_with_approvals(agent, {}, {}, None, None, **kwargs)]


async def test_without_the_flag_the_stream_mode_is_unchanged():
    agent = _Agent([((), {"messages": []})])
    await _drain(agent)
    assert agent.modes == "values"


async def test_with_the_flag_message_chunks_reach_the_sink_and_not_the_caller():
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    value_chunk = ((), {"messages": []})
    message_chunk = ("messages", (AIMessageChunk(content="par"), {"langgraph_node": "model"}))
    agent = _Agent([("values", value_chunk), message_chunk])

    yielded = await _drain(agent, trace=sink, stream_tokens=True, role="coder")

    assert agent.modes == ["values", "messages"]
    assert yielded == [value_chunk], "callers must see exactly the values shape"
    assert [event.kind for event in seen] == [TraceKind.AI_TEXT]
    assert seen[0].payload == "par"
    assert seen[0].role == "coder"


async def test_empty_token_chunks_are_not_emitted():
    """A tool-calling turn streams empty content deltas; a blank line per
    delta would bury the trace it is meant to improve."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    agent = _Agent(
        [
            ("messages", (AIMessageChunk(content=""), {})),
            ("messages", (AIMessageChunk(content="hi"), {})),
        ]
    )

    await _drain(agent, trace=sink, stream_tokens=True)

    assert [event.payload for event in seen] == ["hi"]


async def test_the_flag_without_a_sink_is_harmless():
    agent = _Agent([("values", ((), {"messages": []}))])
    assert await _drain(agent, stream_tokens=True) == [((), {"messages": []})]


def test_stream_tokens_defaults_to_off_in_config():
    from rudra.config.schema import DEFAULTS

    assert DEFAULTS["agent"]["stream_tokens"] is False
