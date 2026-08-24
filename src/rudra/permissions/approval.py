"""The terminal approval prompt and the interrupt/resume loop.

The prompt's reader is injected so every branch is testable without a TTY.
`run_with_approvals` wraps `astream` rather than changing its stream mode,
because `__interrupt__` never appears in "values" chunks and switching modes
would force a rewrite of both parse loops in main_agent.py -- the same two
functions carrying A1.20's unfixed counter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langgraph.types import Command
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from rudra.permissions.audit import AuditLog
from rudra.permissions.diff import render
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import (
    SUBSTITUTION_MARKS,
    PermissionEngine,
    Rule,
    command_segments,
    gated_arg,
)
from rudra.ui import Cancelled, Choice, Chosen, initial
from rudra.ui.prompt import ask as ask_selection

MAX_APPROVAL_ROUNDS = 50

_APPROVAL_CHOICES = (
    Choice(value="approve", label="Approve", description="this one call", hotkey="a"),
    Choice(value="reject", label="Reject", description="refuse it", hotkey="r"),
    # `description` is replaced per request with the grant it would add.
    Choice(value="always", label="Always", description="", hotkey="A"),
    Choice(value="diff", label="Show full diff", description="", hotkey="d"),
)

_REJECT_MESSAGE = (
    "The user rejected this call. Do not retry it. Choose a different "
    "approach, or stop and explain what you need."
)


class ApprovalLoopExceeded(RuntimeError):
    """More approval rounds than any real run needs -- something is wrong."""


def suggest_grant(tool: str, arg: str | None) -> Rule:
    """The rule `always` would add for this call.

    For a command, the first word plus `*`: approving `pytest -q` once
    should cover `pytest -q tests/x`, which is the case that makes `always`
    worth having. For a path, the exact path -- widening a write grant by
    guessing a directory would grant more than the user saw.
    """
    if arg is None:
        return Rule(tool, None)
    if tool == "execute":
        # A chained command gets a grant for the exact string and nothing
        # more. `{first}*` on `pytest -q; rm -rf ~` would mint a rule whose
        # own prefix is innocent, and the user approved this command, not
        # this command's first word plus anything (CR-B1).
        if len(command_segments(arg)) > 1 or any(mark in arg for mark in SUBSTITUTION_MARKS):
            return Rule(tool, arg.strip())
        first = arg.strip().split()
        return Rule(tool, f"{first[0]}*") if first else Rule(tool, None)
    return Rule(tool, arg)


def _render_request(
    console: Console, tool: str, args: dict[str, Any], project_root: Path, *, full: bool
) -> None:
    preview = render(tool, args, project_root, full=full)
    console.print(Panel(escape(preview.header), title="approval required", border_style="yellow"))
    if preview.body:
        console.print(escape(preview.body))


def decide_action_requests(
    action_requests: Iterable[dict[str, Any]],
    *,
    engine: PermissionEngine,
    grants: SessionGrants,
    audit: AuditLog,
    console: Console,
    project_root: Path,
    mode: str,
    reader: Callable[[], str],
) -> list[dict[str, Any]]:
    """Turn one batched interrupt's requests into a `decisions` list.

    Each request is re-checked against the engine first. `interrupt_on`'s
    `when` predicate fires per AI message, and a grant added while deciding
    an earlier request in the same batch must take effect for a later one --
    otherwise `always` would still prompt for the rest of the batch.
    """
    decisions: list[dict[str, Any]] = []

    for request in action_requests:
        tool = request.get("name", "")
        args = request.get("args") or {}
        arg = gated_arg(tool, args)
        decision = engine.decide(tool, args)

        if decision.effect == "allow":
            audit.record(tool, arg, decision, mode=mode, outcome="allow")
            decisions.append({"type": "approve"})
            continue
        if decision.effect == "deny":
            audit.record(tool, arg, decision, mode=mode, outcome="deny")
            decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
            continue

        grant = suggest_grant(tool, arg)
        full = False
        while True:
            _render_request(console, tool, args, project_root, full=full)

            # The grant is per request, so the "Always" row is built here
            # rather than held in the module-level tuple.
            choices = tuple(
                replace(choice, description=escape(str(grant)))
                if choice.value == "always"
                else choice
                for choice in _APPROVAL_CHOICES
            )
            outcome = ask_selection(initial(choices), console=console, reader=reader)

            # Cancelled is REJECT here, never approve. An interrupt at a
            # write prompt is the one moment where guessing wrong puts
            # bytes on the user's disk, and before OPEN-11 a Ctrl-C here
            # was not caught at all -- it propagated out of the run.
            if isinstance(outcome, Cancelled) or not isinstance(outcome, Chosen):
                audit.record(tool, arg, decision, mode=mode, outcome="reject")
                decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
                break

            key = outcome.values[0] if outcome.values else "reject"
            if key == "approve":
                audit.record(tool, arg, decision, mode=mode, outcome="approve")
                decisions.append({"type": "approve"})
                break
            if key == "reject":
                audit.record(tool, arg, decision, mode=mode, outcome="reject")
                decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
                break
            if key == "always":
                grants.add(grant)
                granted = engine.decide(tool, args)
                audit.record(tool, arg, granted, mode=mode, outcome="allow")
                decisions.append({"type": "approve"})
                break
            # "diff": not terminal. Re-render with the full diff and ask
            # again -- the loop this function has always had.
            full = True

    return decisions


async def _stream_with_retry(
    agent: Any,
    payload: Any,
    config: dict[str, Any],
    *,
    trace: Any = None,
    stream_tokens: bool = False,
    role: str = "agent",
) -> AsyncIterator[Any]:
    """`agent.astream`, surviving a transient provider failure (A1.39).

    Retries only while **nothing has been yielded**. Once a chunk is out,
    the caller's parse loop has already seen it and a retry would re-emit
    the run from the start -- so a mid-stream failure is surfaced instead.
    Rescuing that case needs resume (C7.2), not a retry.

    That limit costs less than it sounds: every failure measured on
    2026-08-17 -- a 429 on the first planner call, a 502, a 500, an
    APIConnectionError -- struck before or at the first chunk.

    This is the one funnel for model invocation: the planner streams
    through it, and so does every subagent (subagents/runner.py:150).

    It is also where token streaming is CONTAINED (C9.1). With
    `stream_tokens` on, the agent is asked for ["values", "messages"] and
    langgraph tags each chunk with its mode; message-chunks are emitted to
    the trace and **only value-chunks are yielded**, unwrapped to exactly
    the shape the single-mode call produces. Every caller's parse loop is
    untouched, which is the constraint this module's docstring sets out
    and which both parse loops depend on.
    """
    import asyncio

    from rudra.llm.retry import ProviderUnavailable, is_transient, retry_delays

    delays = retry_delays()
    last: BaseException | None = None

    mode: Any = ["values", "messages"] if stream_tokens else "values"
    # Partial line per role, so redaction sees whole credentials (CR-G6).
    held: dict[str, str] = {}

    for attempt in range(len(delays) + 1):
        yielded = False
        try:
            async for chunk in agent.astream(payload, config, stream_mode=mode, subgraphs=True):
                if stream_tokens:
                    tagged = _tagged(chunk)
                    if tagged is None:
                        continue
                    kind, chunk = tagged
                    if kind == "messages":
                        _emit_token(chunk, trace=trace, role=role, held=held)
                        continue
                yielded = True
                yield chunk
            # Whatever the last line never terminated.
            _flush_tokens(held.pop(role, ""), trace=trace, role=role)
            return
        except Exception as error:  # noqa: BLE001 -- re-raised below
            if yielded or not is_transient(error) or attempt == len(delays):
                if is_transient(error) and not yielded:
                    raise ProviderUnavailable("the model provider", attempt + 1, error) from error
                raise
            last = error
            await asyncio.sleep(delays[attempt])

    if last is not None:  # pragma: no cover -- loop always returns or raises
        raise ProviderUnavailable("the model provider", len(delays) + 1, last) from last


def _tagged(chunk: Any) -> tuple[str, Any] | None:
    """Split langgraph's (mode, chunk) pair, or None if it is not one.

    A list stream_mode makes every chunk a 2-tuple whose first element is
    the mode name. `subgraphs=True` adds a namespace in front for some
    shapes, so the namespace form is passed through as a value chunk --
    that is what the parse loops already understand.
    """
    if not (isinstance(chunk, tuple) and len(chunk) == 2):
        return None
    first, rest = chunk
    if isinstance(first, str):
        return first, rest
    return "values", chunk


# A secret never spans a newline, so a line is the smallest unit that can be
# redacted correctly -- see _emit_token.
_MAX_HELD_CHARS = 4000


def _emit_token(chunk: Any, *, trace: Any, role: str, held: dict[str, str] | None = None) -> None:
    """Turn streamed message deltas into AI_TEXT events, one line at a time.

    Deltas arrive as (message_chunk, metadata). Empty content is dropped:
    a tool-calling turn streams empty deltas, and one blank line each
    would bury the trace this exists to improve.

    Buffered to a line boundary because redaction happens here, and
    redacting each delta in isolation does not work: the patterns need the
    whole credential in one string. Measured -- one key split across three
    deltas gave `OPENROUTER_API_KEY=<redacted>` then `-or-v1-dead` then
    `beefcafebabe1234` unchanged, so the rendered trace carried all but two
    characters of the key into the console, the always-on transcript and
    debug.jsonl. Transcripts are safe to write by default ONLY because
    redaction happens where the event is built, and this was the second
    build site (CR-G6). A line is the right unit: redact.py's patterns
    (`sk-...`, `NAME = value`) cannot span a newline, and holding less than
    a line is what let the key through.
    """
    if trace is None:
        return
    message = chunk[0] if isinstance(chunk, tuple) and chunk else chunk
    text = str(getattr(message, "content", "") or "")
    if not text:
        return

    if held is None:
        _flush_tokens(text, trace=trace, role=role)
        return

    buffered = held.get(role, "") + text
    # A provider that streams a very long line must not be buffered without
    # bound; flushing early risks a split credential, so the cut is made at
    # the last whitespace, which no pattern's value crosses.
    if "\n" not in buffered and len(buffered) > _MAX_HELD_CHARS:
        cut = buffered.rfind(" ")
        if cut > 0:
            _flush_tokens(buffered[:cut], trace=trace, role=role)
            held[role] = buffered[cut:]
            return

    lines = buffered.split("\n")
    held[role] = lines.pop()
    if lines:
        _flush_tokens("\n".join(lines) + "\n", trace=trace, role=role)


def _flush_tokens(text: str, *, trace: Any, role: str) -> None:
    """Redact one complete span of streamed text and emit it."""
    if trace is None or not text.strip():
        return
    from rudra.trace.events import TraceEvent, TraceKind
    from rudra.trace.redact import redact

    trace.emit(TraceEvent(kind=TraceKind.AI_TEXT, role=role, payload=redact(text)))


async def run_with_approvals(
    agent: Any,
    inputs: Any,
    config: dict[str, Any],
    gate: Any,
    console: Console,
    *,
    trace: Any = None,
    stream_tokens: bool = False,
    role: str = "agent",
) -> AsyncIterator[Any]:
    """Stream an agent, pausing for approval and resuming, transparently.

    Yields exactly what `agent.astream(...)` yields, so a caller's existing
    parse loop needs no change beyond the call itself.

    The interrupt is read from `get_state` after the stream drains rather
    than from the stream, because `__interrupt__` does not appear in
    "values" chunks and switching stream modes would change the chunk shape
    both of main_agent.py's parse loops depend on.
    """
    payload: Any = inputs
    streaming = {"trace": trace, "stream_tokens": stream_tokens, "role": role}

    if gate is None:
        async for chunk in _stream_with_retry(agent, payload, config, **streaming):
            yield chunk
        return

    for _ in range(MAX_APPROVAL_ROUNDS):
        async for chunk in _stream_with_retry(agent, payload, config, **streaming):
            yield chunk

        # aget_state, not get_state: Rudra's checkpointer is AsyncSqliteSaver,
        # which refuses synchronous calls from the loop's own thread. An
        # InMemorySaver tolerates either, so unit tests alone never catch
        # this — the acceptance run did.
        state = await agent.aget_state(config)
        interrupts = getattr(state, "interrupts", ()) or ()
        if not interrupts:
            return

        requests: list[dict[str, Any]] = []
        for interrupt in interrupts:
            value = getattr(interrupt, "value", None) or {}
            requests.extend(value.get("action_requests", []))

        payload = Command(resume={"decisions": gate.prompt(requests, console)})

    raise ApprovalLoopExceeded(
        f"Stopped after {MAX_APPROVAL_ROUNDS} approval rounds. A real run needs "
        f"a handful; this many means the agent is looping rather than progressing."
    )


__all__ = [
    "MAX_APPROVAL_ROUNDS",
    "ApprovalLoopExceeded",
    "decide_action_requests",
    "run_with_approvals",
    "suggest_grant",
]
