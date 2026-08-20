"""Rudra's run trace: what the user sees while an agent works (Step 15a).

One vocabulary (events.py), one renderer (render.py), one chunk consumer
(stream.py), one fan-out (sink.py). Before this package the planner
printed its own trace, the subagents printed nothing at all, and
`--verbose` reached neither (A1.90).
"""

from __future__ import annotations

from rudra.trace.events import TraceEvent, TraceKind, TraceLevel
from rudra.trace.render import render
from rudra.trace.stream import StreamState, consume

__all__ = ["StreamState", "TraceEvent", "TraceKind", "TraceLevel", "consume", "render"]
