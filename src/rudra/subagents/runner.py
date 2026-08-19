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

# Carried from _stream_coder (main_agent.py:241-243), which Step 9c
# deletes. These are per-invocation guards: one subagent looping on itself.
# The across-invocation bounds -- max fix attempts, no-progress detection --
# are C6.5a and belong to the loop, not here.
MAX_CONSECUTIVE_FAILURES = 3
MAX_REPEATED_CALLS = 3

# Tool calls worth counting repeats for. `task` is included and now
# genuinely reachable, which is what closes A1.20.
_WATCHED_TOOLS = frozenset({"write_file", "edit_file", "read_file", "task"})

_ERROR_MARKERS = (
    "Error:",
    "Cannot write to",
    "Traceback",
    "Errno",
    "BLOCKED:",
    "not a valid tool",
)


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


def _looks_like_error(content: str) -> bool:
    first_line = content.split("\n")[0] if content else ""
    return any(marker in first_line for marker in _ERROR_MARKERS)


def _call_key(tool_call: dict) -> tuple[str, str]:
    args = tool_call.get("args") or {}
    identifier = args.get("file_path") or args.get("path") or args.get("subagent_type") or ""
    return tool_call.get("name", ""), str(identifier)


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
        agent = build_agent(spec, context)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text="", ok=False, error=str(exc))

    thread = thread_id or f"{context.session_id}-{name}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread}}

    processed = 0
    consecutive_failures = 0
    repeated: dict[tuple[str, str], int] = {}
    halted: str | None = None
    last_text = ""

    try:
        async for chunk in run_with_approvals(
            agent,
            {"messages": [{"role": "user", "content": prompt}]},
            config,
            context.gate,
            context.console,
        ):
            if halted is not None:
                break

            _namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            messages = event.get("messages", []) if isinstance(event, dict) else []

            while processed < len(messages):
                message = messages[processed]
                kind = type(message).__name__

                if kind == "AIMessage":
                    text = str(getattr(message, "content", "") or "").strip()
                    if text:
                        last_text = text
                    for tool_call in getattr(message, "tool_calls", []) or []:
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
                    if _looks_like_error(str(getattr(message, "content", ""))):
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            halted = f"{consecutive_failures} consecutive tool failures -- stopping"
                    else:
                        consecutive_failures = 0

                if halted is not None:
                    break
                processed += 1
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return SubagentResult(name=name, text=last_text, ok=False, error=str(exc))

    if halted is not None:
        return SubagentResult(name=name, text=last_text, ok=False, halted_reason=halted)
    return SubagentResult(name=name, text=last_text, ok=True)


__all__ = ["SubagentContext", "SubagentResult", "run_subagent"]
