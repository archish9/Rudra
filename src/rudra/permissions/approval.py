"""The terminal approval prompt and the interrupt/resume loop.

The prompt's reader is injected so every branch is testable without a TTY.
`run_with_approvals` wraps `astream` rather than changing its stream mode,
because `__interrupt__` never appears in "values" chunks and switching modes
would force a rewrite of both parse loops in main_agent.py -- the same two
functions carrying A1.20's unfixed counter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path
from typing import Any

from langgraph.types import Command
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from rudra.permissions.audit import AuditLog
from rudra.permissions.diff import render
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import PermissionEngine, Rule, gated_arg

MAX_APPROVAL_ROUNDS = 50

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
            console.print(
                f"[a]pprove  [r]eject  [A]lways ({escape(str(grant))})  [d]iff (full)",
                markup=False,
            )
            key = (reader() or "").strip()
            if key == "a":
                audit.record(tool, arg, decision, mode=mode, outcome="approve")
                decisions.append({"type": "approve"})
                break
            if key == "r":
                audit.record(tool, arg, decision, mode=mode, outcome="reject")
                decisions.append({"type": "reject", "message": _REJECT_MESSAGE})
                break
            if key == "A":
                grants.add(grant)
                granted = engine.decide(tool, args)
                audit.record(tool, arg, granted, mode=mode, outcome="allow")
                decisions.append({"type": "approve"})
                break
            if key == "d":
                full = True
                continue
            console.print(f"[red]Unrecognised key {key!r}. Choose a, r, A, or d.[/red]")

    return decisions


async def run_with_approvals(
    agent: Any,
    inputs: Any,
    config: dict[str, Any],
    gate: Any,
    console: Console,
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

    if gate is None:
        async for chunk in agent.astream(payload, config, stream_mode="values", subgraphs=True):
            yield chunk
        return

    for _ in range(MAX_APPROVAL_ROUNDS):
        async for chunk in agent.astream(payload, config, stream_mode="values", subgraphs=True):
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
