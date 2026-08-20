"""langgraph chunks in, TraceEvents out -- once per message, per namespace.

The counter is keyed by namespace and that is the whole point. Both
existing stream loops (subagents/runner.py:157, agent/planner_agent.py:501)
kept one integer while streaming with subgraphs=True, so a subagent's
short `messages` list advanced the counter past its parent's longer one
and the difference was never seen again. That is A1.20's surviving half,
and it went unnoticed for four steps because nothing rendered these
messages at all -- only the guards read them, and a guard that misses a
repeat fails quietly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rudra.trace.events import TraceEvent, TraceKind

_FIRST_LINE_MARKERS = (
    "Error:",
    "Cannot write to",
    "Traceback",
    "Errno",
    "BLOCKED:",
)
"""Markers a failure leads with. Checked against the first line only, so a
tool that merely *mentions* an error in its output is not miscounted."""

_ANYWHERE_MARKERS = (
    "not a valid tool",
    "Input should be a valid string",
)
"""Markers langchain buries inside a validation message, which never
arrives on the first line."""

ERROR_MARKERS = _FIRST_LINE_MARKERS + _ANYWHERE_MARKERS
"""The union of the two lists that had drifted apart: runner.py:37-46 held
all seven, planner_agent.py:454-462 held four -- so the planner's
consecutive-failure guard could not see `BLOCKED:` or either validation
message. One list, one opinion."""


def looks_like_error(content: str) -> bool:
    """Does this tool result read as a failure?"""
    first_line = content.split("\n")[0] if content else ""
    if any(marker in first_line for marker in _FIRST_LINE_MARKERS):
        return True
    return any(marker in content for marker in _ANYWHERE_MARKERS)


@dataclass
class StreamState:
    """Per-run streaming bookkeeping: who is talking, and what was seen.

    One per agent invocation, held by the caller's loop. `counters` is the
    fix for A1.20: a namespace's position is its own.
    """

    role: str
    started: float = field(default_factory=time.monotonic)
    counters: dict[tuple[str, ...], int] = field(default_factory=dict)


def _events_for(
    message: Any,
    *,
    state: StreamState,
    namespace: tuple[str, ...],
    index: int,
) -> list[TraceEvent]:
    kind = type(message).__name__
    at = time.monotonic() - state.started
    content = str(getattr(message, "content", "") or "")

    def event(event_kind: TraceKind, name: str = "", payload: str = "") -> TraceEvent:
        return TraceEvent(
            kind=event_kind,
            role=state.role,
            namespace=namespace,
            index=index,
            name=name,
            payload=payload,
            at=at,
        )

    if kind == "AIMessage":
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            return [
                event(TraceKind.TOOL_CALL, call.get("name", "?"), str(call.get("args", {})))
                for call in calls
            ]
        # A tool-calling turn often carries empty prose; a blank AI line
        # is noise, not information.
        return [event(TraceKind.AI_TEXT, payload=content)] if content.strip() else []

    if kind == "ToolMessage":
        name = str(getattr(message, "name", "") or "?")
        which = TraceKind.TOOL_ERROR if looks_like_error(content) else TraceKind.TOOL_RESULT
        return [event(which, name, content)]

    if kind == "HumanMessage":
        return [event(TraceKind.USER, payload=content)]

    return [event(TraceKind.OTHER, kind, content)]


def consume(chunk: Any, state: StreamState) -> list[TraceEvent]:
    """Every message in `chunk` not already seen for its namespace.

    Accepts both shapes the stream yields: the `(namespace, event)` tuple
    that `subgraphs=True` produces, and a bare event dict, which is what
    an ungated caller or a test passes.
    """
    namespace, event = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
    key = tuple(namespace or ())
    messages = event.get("messages", []) if isinstance(event, dict) else []

    seen = state.counters.get(key, 0)
    produced: list[TraceEvent] = []
    while seen < len(messages):
        produced.extend(_events_for(messages[seen], state=state, namespace=key, index=seen + 1))
        seen += 1
    state.counters[key] = seen
    return produced


__all__ = ["ERROR_MARKERS", "StreamState", "consume", "looks_like_error"]
