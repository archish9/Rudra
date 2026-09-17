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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from rudra.trace.events import TraceEvent, TraceKind
from rudra.trace.redact import redact

_FIRST_LINE_MARKERS = (
    "Error:",
    "Cannot write to",
    "Traceback",
    "Errno",
    "[Errno",
    "BLOCKED:",
)
"""Markers a failure LEADS WITH. Matched as a prefix of the first line,
stripped, so a tool that merely *mentions* an error in its output is not
miscounted.

Prefix rather than `in`, since OPEN-16: `read_file` numbers the lines it
returns, so a file whose own first line reads `Error: ...` arrives as
`  1  Error: ...` and does not match, while a genuine unnumbered
`Error: File '/a.py' not found` from the tool itself still does. The
numbering does the disambiguating for free -- but only under a prefix
match.

`[Errno` is listed beside `Errno` because an OSError renders as
`[Errno 2] No such file`, and the bracket is part of the first token."""

REFUSAL_KEY = "rudra_refusal"
"""The `additional_kwargs` flag saying RUDRA invented this ToolMessage.

OPEN-94. `RepeatGuardMiddleware` answers a call instead of running it, and
its failure refusal LEADS WITH "Error:" because that is what the model must
read -- so the text check below counted a short-circuit as a tool failure,
and `subagents/runner.py` halted an invocation on three of them. Two of the
three that killed run 2cde3406f7d6's first coder at 11.67 s were this
string; no tool ran for either.

The classification could not move into the prose -- `repeat_guard.py`'s
docstring forbids prefixing the text to make it classifiable, and the model
must still read "that did not work". So it moved off the prose entirely:
one field on the message, one check here, and the subagent counter, the
planner counter and the renderer agree for free.

Set through `RepeatGuardMiddleware._as_message`, the single seam every
refusal is built at. Nothing that came back from a real handler carries it,
which is what keeps a gate's "BLOCKED:" counting."""


ERROR_MARKERS = _FIRST_LINE_MARKERS
"""The union of THREE copies that had drifted apart, counted 2026-08-20:

* `runner.py:37-44` -- 6 markers, first line only (the subagent guard).
* `planner_agent.py:555-560` -- 4 markers, first line only (the planner
  guard). It cannot see `BLOCKED:` at all, so a denied tool call never
  increments its consecutive-failure counter.
* `planner_agent.py:454-462` -- 8 conditions, two of them scanning the
  whole content (the planner renderer), so the planner *displayed* a
  failure its own guard was not counting.

One list, one opinion.

It used to carry two more -- `"not a valid tool"` and `"Input should be a
valid string"` -- matched against the WHOLE content on the grounds that
langchain buries them mid-message. That made any file containing either
phrase read as a failure, and Rudra writes one: the run transcript records
every tool error verbatim, so `read_file` on
`.rudra/run/transcripts/<id>.jsonl` came back as an ERROR with the file's
own contents as the payload, and three such reads halted the subagent on
`MAX_CONSECUTIVE_FAILURES` (OPEN-16). Both cases are covered structurally
by `message_is_error` instead."""


def looks_like_error(content: str) -> bool:
    """Does this tool result read as a failure, judging by its text alone?

    First line only, deliberately -- a tool that merely *mentions* an error
    in its output must not be miscounted, and Rudra's own logs mention
    plenty. Content is the fallback signal; `message_is_error` is the one
    to prefer where a whole message is in hand.
    """
    first_line = content.split("\n")[0].strip() if content else ""
    return first_line.startswith(_FIRST_LINE_MARKERS)


def is_rudra_refusal(message: Any) -> bool:
    """Did Rudra write this message instead of a tool answering?

    The structural half of OPEN-94, and the reason a consumer never has to
    string-match a refusal: `runner.py` string-matching one would be a
    fifth copy of a marker list that has already drifted three ways.
    """
    kwargs = getattr(message, "additional_kwargs", None)
    if not isinstance(kwargs, dict):
        return False
    return bool(kwargs.get(REFUSAL_KEY))


def message_is_error(message: Any) -> bool:
    """Did this ToolMessage fail?

    `status` is the structural answer and needs no string matching:
    langgraph sets `status="error"` on the invalid-tool-name message
    (`prebuilt/tool_node.py:1276-1278`) and on every tool exception, and
    langchain's own middleware does the same. Measured against the pinned
    versions; `status` defaults to `"success"`, so a tool that does not set
    it is not misread.

    The text check stays as a fallback because a deepagents filesystem tool
    reports a missing file by RETURNING `"Error: ... not found"` with a
    successful status, and Rudra's deny middleware returns `"BLOCKED: ..."`
    the same way. Those are failures the model must see counted.
    """
    if getattr(message, "status", None) == "error":
        return True
    if is_rudra_refusal(message):
        # OPEN-94: a short-circuit is not a failure. Checked AFTER
        # `status`, which is langgraph's own word about a tool that
        # actually ran -- nothing Rudra invents carries one, so the two
        # cannot legitimately co-occur and the structural signal wins if
        # they ever do.
        return False
    return looks_like_error(str(getattr(message, "content", "") or ""))


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
    streamed: str = "",
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
            # Redacted HERE, where the event is built, so the console, the
            # --debug log and 15c's transcript are covered by one rule
            # (A1.95). Doing it in render.py would have cleaned the screen
            # and left debug.jsonl holding the credential -- the more
            # dangerous of the two, since the docs ask users to attach it.
            payload=redact(payload),
            at=at,
        )

    if kind == "AIMessage":
        calls = getattr(message, "tool_calls", None) or []
        # Prose FIRST, then the calls, and both when the turn has both --
        # models routinely say what they are about to do in the same
        # message that does it, and dropping that sentence loses the only
        # statement of intent the trace ever gets. An empty content field
        # yields nothing: a tool-calling turn usually carries one, and a
        # blank AI line per call is noise.
        #
        # Unless the deltas already said it (OPEN-124): with `--stream` on,
        # `permissions/approval.py` emitted this text line by line while the
        # model wrote it, and `streamed` is what it delivered in this
        # namespace since the previous values chunk.
        said = content.strip()
        new_prose = bool(said) and not streamed.rstrip().endswith(said)
        events = [event(TraceKind.AI_TEXT, payload=content)] if new_prose else []
        events.extend(
            event(TraceKind.TOOL_CALL, call.get("name", "?"), str(call.get("args", {})))
            for call in calls
        )
        return events

    if kind == "ToolMessage":
        name = str(getattr(message, "name", "") or "?")
        which = TraceKind.TOOL_ERROR if message_is_error(message) else TraceKind.TOOL_RESULT
        return [event(which, name, content)]

    if kind == "HumanMessage":
        return [event(TraceKind.USER, payload=content)]

    return [event(TraceKind.OTHER, kind, content)]


def consume(
    chunk: Any,
    state: StreamState,
    streamed: Mapping[tuple[str, ...], str] | None = None,
) -> list[TraceEvent]:
    """Every message in `chunk` not already seen for its namespace.

    Accepts both shapes the stream yields: the `(namespace, event)` tuple
    that `subgraphs=True` produces, and a bare event dict, which is what
    an ungated caller or a test passes.

    `streamed` maps a namespace to the prose token streaming already
    delivered there since its previous values chunk (OPEN-124). An
    AIMessage whose text ENDS that string is not emitted as AI_TEXT again;
    its tool calls still are. A suffix rather than equality, because a
    model call re-issued mid-stream delivers the failed attempt's text
    first. **It can never lose prose**: a skipped message's text is, by
    that test, text already emitted -- so a model that does not stream,
    which delivers nothing, is recorded whole as it always was. Matched on
    text and never on message ids, which providers fill in differently
    between a delta and the message merged from them.
    """
    namespace, event = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
    key = tuple(namespace or ())
    messages = event.get("messages", []) if isinstance(event, dict) else []
    already = (streamed or {}).get(key, "")

    seen = state.counters.get(key, 0)
    produced: list[TraceEvent] = []
    while seen < len(messages):
        produced.extend(
            _events_for(
                messages[seen], state=state, namespace=key, index=seen + 1, streamed=already
            )
        )
        seen += 1
    state.counters[key] = seen
    return produced


__all__ = [
    "ERROR_MARKERS",
    "REFUSAL_KEY",
    "StreamState",
    "consume",
    "is_rudra_refusal",
    "looks_like_error",
    "message_is_error",
]
