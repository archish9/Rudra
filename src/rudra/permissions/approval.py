"""The terminal approval prompt and the interrupt/resume loop.

The prompt's reader is injected so every branch is testable without a TTY.
`run_with_approvals` wraps `astream` rather than changing its stream mode,
because `__interrupt__` never appears in "values" chunks and switching modes
would force a rewrite of both parse loops in main_agent.py -- the same two
functions carrying A1.20's unfixed counter.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessageChunk
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
    Choice(
        value="auto",
        label="Auto-accept",
        description="stop asking for the rest of this session",
        hotkey="!",
    ),
    Choice(value="diff", label="Show full diff", description="", hotkey="d"),
)

_AUTO_ACCEPT_NOTICE = (
    "[yellow]Auto-accept on for the rest of this session — no further "
    "approval prompts.[/yellow] Deny rules and Rudra's built-in floor still "
    "apply, and every call is still written to the audit log."
)

_REJECT_MESSAGE = (
    "The user rejected this call. Do not retry it. Choose a different "
    "approach, or stop and explain what you need."
)


class ApprovalLoopExceeded(RuntimeError):
    """More approval rounds than any real run needs -- something is wrong."""


# The blanks a POSIX shell splits words on -- its default IFS. Not `\s`:
# a no-break space is part of a word to the shell, and splitting on it
# would name a shorter first word than the one that runs.
_BLANKS = " \t\n"

# One word as written: unquoted non-blank characters, '...' spans, "..."
# spans with their backslash escapes, and backslash escapes, glued together.
_WRITTEN_WORD = re.compile(r"""(?:[^ \t\n'"\\]|'[^']*'|"(?:[^"\\]|\\.)*"|\\.)+""", re.DOTALL)


def _written_words(command: str) -> list[re.Match[str]]:
    """The leading whole words of `command`, as a shell would split them.

    Spans, not text, and in the command's OWN spelling: a grant is matched
    against the command as written (`rules.py::_execute_matches`), so the
    quotes are part of what it must name. Stops at the first word that does
    not end at a blank -- an open quote, or a lone trailing backslash --
    because the shell would not read that word where it stops here.
    """
    words: list[re.Match[str]] = []
    position = 0
    while True:
        while position < len(command) and command[position] in _BLANKS:
            position += 1
        word = _WRITTEN_WORD.match(command, position)
        if word is None:
            return words
        if word.end() < len(command) and command[word.end()] not in _BLANKS:
            return words
        words.append(word)
        position = word.end()


def _literal(text: str) -> str:
    """An `fnmatch` pattern matching exactly `text`: `[*]`, `[?]`, `[[]`."""
    return re.sub(r"([*?\[])", r"[\1]", text)


def suggest_grant(tool: str, arg: str | None) -> Rule:
    """The rule `always` would add for this call.

    For a command, the first word plus `*`: approving `pytest -q` once
    should cover `pytest -q tests/x`, which is the case that makes `always`
    worth having. The first word is read as a shell reads it and matched
    literally (OPEN-144), so a quoted path is one word and a `*` in it is a
    character. For a path, the exact path -- widening a write grant by
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
        # The first word AS WRITTEN, and literally (OPEN-144). `str.split`
        # cut the gate's own `shlex.join`ed command inside a quoted path --
        # `'/Users/a/My Projects/…/pytest'` granted `'/Users/a/My*` -- and a
        # `*`, `?` or `[` in a path was pasted in as a wildcard.
        command = arg.strip()
        words = _written_words(command)
        if not words:
            # Empty, or an open quote in the first word: no word to name, so
            # the grant is the text shown and nothing more. The patternless
            # rule this used to return covered every command.
            return Rule(tool, _literal(command))
        if len(words) >= 3 and words[1].group() == "-m":
            # `<interpreter> -m <module>`: the module is the command. The
            # interpreter alone would also cover `-m pip install` and `-c
            # <anything>`, which is more than the user was shown -- and since
            # OPEN-140 the gate's own test command has exactly this shape.
            return Rule(tool, f"{_literal(command[: words[2].end()])}*")
        return Rule(tool, f"{_literal(command[: words[0].end()])}*")
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
            if key == "auto":
                # No rule is added. The engine reads `approve_all` directly,
                # so the rest of THIS batch stops prompting for free -- each
                # request is re-checked against the engine at the top of this
                # loop -- and so does every later call, because
                # interrupts._predicate asks the same engine per call.
                grants.grant_all()
                granted = engine.decide(tool, args)
                audit.record(tool, arg, granted, mode=mode, outcome="allow")
                # Said out loud, once. Escalating what the agent may do
                # without a line saying so is the failure mode this guards.
                console.print(_AUTO_ACCEPT_NOTICE)
                decisions.append({"type": "approve"})
                break
            # "diff": not terminal. Re-render with the full diff and ask
            # again -- the loop this function has always had.
            full = True

    return decisions


# Replaces `ProviderUnavailable`'s "Nothing was written." clause when the
# failure struck after the stream had begun (OPEN-41). Deliberately says
# "may": this funnel carries the planner AND every subagent, and cannot see
# whether the agent it was streaming had written anything yet. Claiming
# either way would be a guess; the ledger is the thing that actually knows,
# and `RudraAgent` adds the resume line from it.
# The clause `ProviderUnavailable` prints in place of "Nothing was written."
# Only the files half, because the class now says the run had begun on its
# own account (OPEN-83) -- saying it twice is how the sentence got long
# enough that the count in front of it was the only part users read.
_MID_RUN_PROGRESS = "Any files written before this point are on disk."


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

    **The user-visible consequence, because it was mistaken for a defect
    once (OPEN-83).** On this path `ProviderUnavailable` carries
    `attempts == 1` always -- the loop leaves on its first pass -- and the
    message therefore prints no attempt count at all. A run that dies here
    spent ZERO of its four budgeted tries, and that is the design rather
    than a retry that failed. Anything that changes when the count is
    printed belongs in `llm/retry.py`, which owns both spellings so they
    cannot drift.

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

    **A values chunk is also the flush point for its namespace's streamed
    prose (OPEN-124).** It arrives after the node that produced it returned,
    so any message that namespace was streaming is complete: its held line
    is emitted, and the text streamed since the namespace's previous values
    chunk is noted on the sink, which then does not record the same message
    whole a second time. Both happen before the chunk is yielded, because
    the caller feeds it to the sink before pulling the next one.
    """
    import asyncio

    from rudra.llm.retry import ProviderUnavailable, is_transient, retry_delays

    delays = retry_delays()
    last: BaseException | None = None

    mode: Any = ["values", "messages"] if stream_tokens else "values"

    for attempt in range(len(delays) + 1):
        yielded = False
        # Partial line per NAMESPACE, so redaction sees whole credentials
        # (CR-G6) and a subagent's text never lands in its parent's line.
        held: dict[tuple[str, ...], str] = {}
        # Raw text streamed per namespace since its last values chunk.
        streamed: dict[tuple[str, ...], str] = {}
        try:
            async for chunk in agent.astream(payload, config, stream_mode=mode, subgraphs=True):
                if stream_tokens:
                    kind, namespace, data = _tagged(chunk)
                    if kind == "messages":
                        _emit_token(
                            data,
                            trace=trace,
                            role=role,
                            namespace=namespace or (),
                            held=held,
                            streamed=streamed,
                        )
                        continue
                    if namespace is not None:
                        _flush_tokens(
                            held.pop(namespace, ""), trace=trace, role=role, namespace=namespace
                        )
                        _note_streamed(trace, namespace, streamed.pop(namespace, ""))
                        chunk = (namespace, data)
                yielded = True
                yield chunk
            return
        except Exception as error:  # noqa: BLE001 -- re-raised below
            if yielded or not is_transient(error) or attempt == len(delays):
                if is_transient(error):
                    # OPEN-41: reachable on BOTH paths now. It used to be
                    # the not-yielded one only, so the case that costs the
                    # user more -- the run is further along -- was the one
                    # case with no message, and run `82fa4385bb22` reached
                    # the terminal as 850 lines of openai/langgraph frames.
                    # Still not RETRIED when yielded: the caller's parse
                    # loop has consumed chunks and replaying them needs
                    # resume (C7.2). Reporting it is a separate question
                    # from rescuing it, and only the reporting is fixed.
                    raise ProviderUnavailable(
                        "the model provider",
                        attempt + 1,
                        error,
                        progress=_MID_RUN_PROGRESS if yielded else None,
                    ) from error
                raise
            last = error
            await asyncio.sleep(delays[attempt])
        finally:
            # Whatever a line never terminated -- at the end, on a failure,
            # or when the caller stops pulling. Already paid for; a record
            # that drops it is §8a's failure shape 1.
            for namespace, text in held.items():
                _flush_tokens(text, trace=trace, role=role, namespace=namespace)

    if last is not None:  # pragma: no cover -- loop always returns or raises
        raise ProviderUnavailable("the model provider", len(delays) + 1, last) from last


def _tagged(chunk: Any) -> tuple[str, tuple[str, ...] | None, Any]:
    """Split langgraph's `(namespace, mode, data)` into its three parts.

    A list `stream_mode` with `subgraphs=True` -- the only way this module
    streams -- yields a 3-tuple for every chunk, parent graph included
    (namespace `()`). This used to expect `(mode, data)`, a shape langgraph
    does not produce with `subgraphs=True`, and returned None for the real
    one, so every chunk was `continue`d and both parse loops ran blind for
    as long as `--stream` was on (OPEN-124).

    **Anything else is passed through as a values chunk, never dropped.** A
    shape this does not recognise reaches the parse loops exactly as the
    single-mode stream would have yielded it; dropping it is what turned a
    shape mismatch into guards that saw nothing. The namespace is None then,
    which tells the caller there is nothing to flush and nothing to unwrap.
    """
    if isinstance(chunk, tuple) and len(chunk) == 3 and isinstance(chunk[1], str):
        namespace, mode, data = chunk
        return mode, tuple(namespace or ()), data
    return "values", None, chunk


# A secret never spans a newline, so a line is the smallest unit that can be
# redacted correctly -- see _emit_token.
_MAX_HELD_CHARS = 4000


def _emit_token(
    chunk: Any,
    *,
    trace: Any,
    role: str,
    namespace: tuple[str, ...] = (),
    held: dict[tuple[str, ...], str] | None = None,
    streamed: dict[tuple[str, ...], str] | None = None,
) -> None:
    """Turn streamed message deltas into AI_TEXT events, one line at a time.

    Deltas arrive as (message_chunk, metadata). Empty content is dropped:
    a tool-calling turn streams empty deltas, and one blank line each
    would bury the trace this exists to improve.

    **Only a model's `AIMessageChunk` is prose (OPEN-124).** langgraph's
    `messages` mode also carries each whole message a node returns -- the
    tools node's `ToolMessage`, and the answer of a model that does not
    stream -- and those already reach the trace from the values chunk, as
    what they are. Taken here, every `read_file` body was an AI_TEXT line.

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
    if not isinstance(message, AIMessageChunk):
        return
    text = str(message.content or "")
    if not text:
        return
    if streamed is not None:
        streamed[namespace] = streamed.get(namespace, "") + text

    if held is None:
        _flush_tokens(text, trace=trace, role=role, namespace=namespace)
        return

    buffered = held.get(namespace, "") + text
    # A provider that streams a very long line must not be buffered without
    # bound; flushing early risks a split credential, so the cut is made at
    # the last whitespace, which no pattern's value crosses.
    if "\n" not in buffered and len(buffered) > _MAX_HELD_CHARS:
        cut = buffered.rfind(" ")
        if cut > 0:
            _flush_tokens(buffered[:cut], trace=trace, role=role, namespace=namespace)
            held[namespace] = buffered[cut:]
            return

    lines = buffered.split("\n")
    held[namespace] = lines.pop()
    if lines:
        _flush_tokens("\n".join(lines) + "\n", trace=trace, role=role, namespace=namespace)


def _flush_tokens(text: str, *, trace: Any, role: str, namespace: tuple[str, ...] = ()) -> None:
    """Redact one complete span of streamed text and emit it."""
    if trace is None or not text.strip():
        return
    from rudra.trace.events import TraceEvent, TraceKind
    from rudra.trace.redact import redact

    trace.emit(
        TraceEvent(kind=TraceKind.AI_TEXT, role=role, namespace=namespace, payload=redact(text))
    )


def _note_streamed(trace: Any, namespace: tuple[str, ...], text: str) -> None:
    """Tell the sink what this namespace streamed since its last values chunk.

    Called for every values chunk, just before it is yielded: the sink
    compares that chunk's whole AIMessages against the note
    (`trace/stream.py::consume`) and discards it once fed. Tolerates a trace
    that is not a `TraceSink`, the way the rest of this module treats
    `trace` as optional.
    """
    note = getattr(trace, "note_streamed", None)
    if note is not None:
        note(namespace, text)


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
