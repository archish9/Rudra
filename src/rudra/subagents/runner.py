"""Invoking one subagent and reporting what it did.

Deterministic on purpose: the caller names the subagent, so no model
decides who runs. D9 requires the loop to be deterministic, and a planner
choosing when to review would put an LLM back in control of it.

Nothing here raises for a runtime failure. Step 9c's loop must act on a
bad subagent run the way it acts on a failed gate, and an exception
escaping into that loop is A1.39's shape -- one transient provider error
killing a run and discarding completed work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.approval import run_with_approvals
from rudra.subagents.build import build_agent
from rudra.subagents.registry import REGISTRY
from rudra.trace.stream import StreamState
from rudra.trace.stream import message_is_error as _message_is_error

# Carried from _stream_coder (main_agent.py:241-243), which Step 9c
# deletes. These are per-invocation guards: one subagent looping on itself.
# The across-invocation bounds -- max fix attempts, no-progress detection --
# are C6.5a and belong to the loop, not here.
MAX_CONSECUTIVE_FAILURES = 3
MAX_REPEATED_CALLS = 3

MAX_TOTAL_CALLS = 80
"""Tool calls one subagent invocation may make, of any kind (A1.92).

The other two guards catch *failing* and catch *repeating*. Neither
catches spending. Measured on Step 15c's acceptance: denied a `bash` tool
it never had, a delegated subagent issued **204 successful `glob` calls
over 12 minutes** looking for a Python interpreter -- ending on
`**/python` against `/` -- and nothing stopped it. `MAX_REPEATED_CALLS`
keys on `(tool, target)`, so 204 different patterns are 204 firsts;
`MAX_CONSECUTIVE_FAILURES` never fired because every glob *succeeded*
(`No files found` is a successful result).

80 is chosen against observed work, not guessed: a healthy coder
invocation in that same acceptance ran to well under 20 calls, and the
planner's busiest stage to about 30. It is a runaway bound, not a budget
-- if real work ever approaches it, raise it rather than teaching people
to expect halts."""

# Tool calls worth counting repeats for. `task` is included and now
# genuinely reachable, which is what closes A1.20.
_WATCHED_TOOLS = frozenset({"write_file", "edit_file", "read_file", "task"})

# The error markers moved to rudra.trace.stream in Step 15a. Three copies
# of this list existed and all three had drifted -- see that module's
# ERROR_MARKERS docstring for what each one was missing.


@dataclass(frozen=True)
class SubagentContext:
    """Everything one run of subagents needs, built once.

    Bundled for the reason Gate itself is (permissions/__init__.py:50-56):
    these must be the *same* objects across calls. A second Gate would hold
    different session grants and re-prompt for something the user already
    answered `always` to.
    """

    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = ""
    # The run's FactStore, shared by reference so a fact recorded during
    # one task reaches the next dispatch's prompt (Step 10a). Optional
    # because the subagent machinery must stay constructible without a
    # run -- 9b's tests build a context with no facts at all.
    facts: Any = None
    # The run's skill sources, or None when skills are off. Shared by
    # reference for the same reason the gate and FactStore are: one run,
    # one cache. Only specs with wants_skills actually receive them.
    skills_sources: tuple[str, ...] | None = None
    # The run's RunUsage, shared by reference for the same reason the gate
    # and the FactStore are: one run, one tally. Optional because the
    # subagent machinery must stay constructible without a run -- 9b's
    # tests build a context with no facts either.
    usage: Any = None
    # The run's McpClient, or None when MCP is off or nothing is configured.
    # Shared by reference for the reason the gate and FactStore are: one run,
    # one set of server processes and one cached catalog.
    mcp: Any = None
    # The run's MemoryStore, or None when it could not be built. Shared by
    # reference for the reason the gate and FactStore are: one run, one
    # palace handle and one cached collection. Optional because the
    # subagent machinery must stay constructible without a run.
    memory: Any = None
    # The run's TraceSink, or None when nothing is watching. Shared by
    # reference for the reason the gate and the FactStore are: one run,
    # one level and one record of it. Optional because the subagent
    # machinery must stay constructible without a run -- every 9b-era
    # test builds a context with no facts either.
    #
    # Before Step 15a there was no field here and no printing: this loop
    # consumed every chunk to drive the guards below and rendered none of
    # them, so the coder, tester and reviewer were invisible while they
    # did the run's actual work.
    trace: Any = None


@dataclass(frozen=True)
class SubagentResult:
    """What one subagent invocation produced.

    `text` is the final assistant message because that is all a delegating
    parent would have seen too (subagents.py:117) -- the direct and
    delegated paths return the same thing.

    ok=False with halted_reason means the subagent misbehaved; with error
    means it never ran. Step 9c needs to tell those apart for the same
    reason 9a's report splits `escalate` from a plain failure.
    """

    name: str
    text: str
    ok: bool
    halted_reason: str | None = None
    error: str | None = None


def _wants_token_stream(context: Any) -> bool:
    """Is token streaming on for this run?

    Read defensively: 9b-era callers -- and several tests -- build a
    SubagentContext with `cfg=None`, and an observability setting must not
    be the thing that makes those unbuildable.
    """
    agent_cfg = getattr(getattr(context, "cfg", None), "agent", None)
    return bool(getattr(agent_cfg, "stream_tokens", False))


def _call_key(tool_call: dict) -> tuple[str, ...]:
    """What makes two tool calls "the same call" for the repeat guard.

    The read window is part of it (OPEN-35). `read_file` pages -- 100 lines
    at a time, `offset`/`limit` on deepagents' ReadFileSchema -- so keying
    on the path alone made page 3 of a file the third repeat of page 1, and
    `MAX_REPEATED_CALLS` killed the subagent. No agent could read a file
    past ~200 lines; run 36023bb8bdd1 ended with the reviewer asking for
    `offset: 200` of a 400-line test file.

    The guard's target is unchanged: the identical call made again. Tools
    with no window -- write_file, edit_file, task -- carry an empty one and
    key exactly as before.
    """
    args = tool_call.get("args") or {}
    identifier = args.get("file_path") or args.get("path") or args.get("subagent_type") or ""
    window = (str(args.get("offset", "")), str(args.get("limit", "")))
    return (tool_call.get("name", ""), str(identifier), *window)


async def run_subagent(
    name: str,
    prompt: str,
    *,
    context: SubagentContext,
    thread_id: str | None = None,
) -> SubagentResult:
    """Run one subagent to completion and report what it said.

    Args:
        name: A REGISTRY key. An unknown name raises -- that is a
            programming error, not a runtime one.
        prompt: The task, as a user message.
        context: The shared per-run objects.
        thread_id: Override the generated one. Each invocation otherwise
            gets a fresh thread, matching what the orchestrator does for
            retries (main_agent.py:351) so a re-run starts clean. This does
            not fix A1.2 -- checkpoints are still never resumed.

    Returns:
        A SubagentResult. Never raises for a runtime failure.
    """
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        msg = f"unknown subagent '{name}'; available: {known}"
        raise ValueError(msg)

    spec = REGISTRY[name]

    try:
        # The prompt IS the task: passing it through is what makes the
        # subagent's memory recall about the work rather than about its own
        # name (CR-C3).
        agent = build_agent(spec, context, prompt)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text="", ok=False, error=str(exc))

    thread = thread_id or f"{context.session_id}-{name}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread}}

    # One counter per subgraph namespace, not one for the run (A1.20).
    # Parent and subagent carry separate `messages` lists, so a shared
    # counter advances past the shorter one and the guards below stop
    # seeing messages they are meant to count.
    seen: dict[tuple[str, ...], int] = {}
    state = StreamState(role=name)
    consecutive_failures = 0
    total_calls = 0
    repeated: dict[tuple[str, ...], int] = {}
    halted: str | None = None
    last_text = ""

    try:
        async for chunk in run_with_approvals(
            agent,
            {"messages": [{"role": "user", "content": prompt}]},
            config,
            context.gate,
            context.console,
            trace=context.trace,
            stream_tokens=_wants_token_stream(context),
            role=name,
        ):
            if halted is not None:
                break

            namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            messages = event.get("messages", []) if isinstance(event, dict) else []

            if context.trace is not None:
                context.trace.feed(chunk, state)

            # `where`, not `key`: the guard body below binds `key` to a
            # (tool, target) pair, and a shared name would file this
            # namespace's position under a tool call.
            where = tuple(namespace or ())
            processed = seen.get(where, 0)
            while processed < len(messages):
                message = messages[processed]
                kind = type(message).__name__

                if kind == "AIMessage":
                    text = str(getattr(message, "content", "") or "").strip()
                    if text:
                        last_text = text
                    for tool_call in getattr(message, "tool_calls", []) or []:
                        # Counted for EVERY tool, before the _WATCHED_TOOLS
                        # filter: A1.92's runaway was 204 successful globs,
                        # and glob is not watched and never failed.
                        total_calls += 1
                        if total_calls >= MAX_TOTAL_CALLS:
                            halted = (
                                f"{total_calls} tool calls in one invocation -- stopping. "
                                f"The last was '{tool_call.get('name', '?')}'."
                            )
                            break
                        if tool_call.get("name") not in _WATCHED_TOOLS:
                            continue
                        key = _call_key(tool_call)
                        repeated[key] = repeated.get(key, 0) + 1
                        if repeated[key] >= MAX_REPEATED_CALLS:
                            halted = (
                                f"'{key[0]}' on '{key[1]}' repeated {repeated[key]}x -- stopping"
                            )
                            break
                elif kind == "ToolMessage":
                    if _message_is_error(message):
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            halted = f"{consecutive_failures} consecutive tool failures -- stopping"
                    else:
                        consecutive_failures = 0

                if halted is not None:
                    break
                processed += 1
            seen[where] = processed
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text=last_text, ok=False, error=str(exc))

    if halted is not None:
        return SubagentResult(name=name, text=last_text, ok=False, halted_reason=halted)
    return SubagentResult(name=name, text=last_text, ok=True)


__all__ = ["SubagentContext", "SubagentResult", "run_subagent"]
