"""A TraceEvent, as lines. Pure, and it escapes.

Replaces two hand-written renderers that had already drifted apart:
`planner_agent._log_message`, which printed only the planner, and
`main_agent._log_single_message`, which printed nothing at all -- it had
no callers left after Step 9c deleted the loop that called it. Neither
escaped, which is A1.67 (a task's `[pending]` status swallowed by Rich)
and A1.48 (a config error's `[skills]` swallowed the same way).

Truncation lives here as named constants rather than as literals at each
site, because "how much of a tool result is worth a screen" is one
decision and it had already been made twice, differently ([:300] and
[:400] in the two originals).
"""

from __future__ import annotations

from rich.markup import escape

from rudra.trace.events import TraceEvent, TraceKind, TraceLevel

CALL_ARGS_CHARS = 400
"""Tool-call arguments and tool results shown at NORMAL. Carried from the
deleted planner renderer's `[:400]`."""

AI_TEXT_CHARS = 300
"""Assistant prose, shown at VERBOSE. Carried from its `[:300]`."""

OTHER_CHARS = 200
"""Everything else. Carried from its `[:200]`."""

_MIN_LEVEL = {
    TraceKind.TOOL_ERROR: TraceLevel.QUIET,
    TraceKind.TOOL_CALL: TraceLevel.NORMAL,
    TraceKind.TOOL_RESULT: TraceLevel.NORMAL,
    TraceKind.AI_TEXT: TraceLevel.VERBOSE,
    TraceKind.USER: TraceLevel.VERBOSE,
    TraceKind.OTHER: TraceLevel.VERBOSE,
}


def _clip(text: str, limit: int, level: TraceLevel) -> str:
    """Escaped text, truncated with an honest marker.

    VERBOSE never truncates and never flattens newlines: the level exists
    so somebody debugging can see the whole thing, and a "verbose" mode
    that still hides the tail would send them to the debug log for what
    they just asked for.
    """
    if level >= TraceLevel.VERBOSE:
        return escape(text)
    flat = text.replace("\n", " ")
    if len(flat) <= limit:
        return escape(flat)
    return escape(flat[:limit]) + f" [dim]… +{len(flat) - limit} chars[/dim]"


def _tag(event: TraceEvent) -> str:
    """`[coder]`, or `[coder:task:1]` inside a subgraph.

    Escaped like everything else: the namespace comes from langgraph and
    the role from a spec, but a bracketed prefix printed raw is the exact
    shape of the bug this module exists to end.
    """
    who = event.role
    if event.namespace:
        who = f"{who}:{':'.join(event.namespace)}"
    return escape(f"[{who}]")


def render(event: TraceEvent, *, level: TraceLevel) -> list[str]:
    """Markup lines for one event, or [] when the level filters it out."""
    if level < _MIN_LEVEL[event.kind]:
        return []

    tag = _tag(event)
    index = event.index
    name = escape(event.name or "?")

    if event.kind is TraceKind.TOOL_CALL:
        args = _clip(event.payload, CALL_ARGS_CHARS, level)
        return [f"[bold cyan]{tag} → \\[{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args}"]

    if event.kind is TraceKind.TOOL_RESULT:
        body = _clip(event.payload, CALL_ARGS_CHARS, level)
        return [f"[green]{tag} ✓ \\[{index}] {name}:[/green] {body}"]

    if event.kind is TraceKind.TOOL_ERROR:
        body = _clip(event.payload, CALL_ARGS_CHARS, level)
        return [f"[bold red]{tag} ✗ \\[{index}] ERROR from {name}:[/bold red]\n[red]{body}[/red]"]

    if event.kind is TraceKind.AI_TEXT:
        body = _clip(event.payload, AI_TEXT_CHARS, level)
        return [f"[bold cyan]{tag} ← \\[{index}] AI[/bold cyan]  {body}"]

    if event.kind is TraceKind.USER:
        body = _clip(event.payload, OTHER_CHARS, level)
        return [f"[dim]{tag} \\[{index}] USER: {body}[/dim]"]

    body = _clip(event.payload, OTHER_CHARS, level)
    return [f"[dim]{tag} \\[{index}] {name}: {body}[/dim]"]


__all__ = ["AI_TEXT_CHARS", "CALL_ARGS_CHARS", "OTHER_CHARS", "render"]
