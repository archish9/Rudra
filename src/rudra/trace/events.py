"""What one line of a run's trace *is*, before anyone decides how to show it.

Three consumers read these: the console renderer, the `--debug` JSONL log,
and 15c's transcript. They agree because they share this dataclass rather
than each parsing langgraph chunks their own way -- the failure C8.5 (a
cache layout reconstructed from the outside) and A1.87 (an exporter
resolving its own collection) both took.

Imports nothing from Rudra. tests/test_trace_wiring.py pins that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any


class TraceKind(StrEnum):
    """What happened. The whole vocabulary -- add here or nowhere.

    Five of these describe something the MODEL did. `NOTICE` is the one
    that describes something RUDRA did on its own account -- a guard that
    halted a subagent (OPEN-44), and whatever follows it. It exists as a
    kind rather than as `OTHER` with a telling `name` so an investigation
    can filter the debug log by kind instead of grepping a string, which
    is the "diagnose a note by its text" mistake three items have now been
    filed against.
    """

    AI_TEXT = "ai_text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    USER = "user"
    NOTICE = "notice"
    OTHER = "other"


class TraceLevel(IntEnum):
    """How much of it to show.

    Ordered, so a filter is `event_level <= sink_level` rather than a set
    membership test that has to be revised every time a kind is added.
    """

    QUIET = 0
    NORMAL = 1
    VERBOSE = 2


@dataclass(frozen=True)
class TraceEvent:
    """One thing an agent did, addressed by role and subgraph namespace.

    `namespace` is langgraph's subgraph path, empty for the parent. It is
    carried rather than flattened because it is what separates a
    subagent's messages from its parent's -- the distinction A1.20's
    single counter loses.

    `at` is seconds since the run started (monotonic), not a wall-clock
    timestamp: a trace is read as a sequence, and an offset stays
    meaningful when the run is replayed from a transcript on another day.
    """

    kind: TraceKind
    role: str
    namespace: tuple[str, ...] = ()
    index: int = 0
    name: str = ""
    payload: str = ""
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """A JSON-writable snapshot. Lists, not tuples -- json has no tuple."""
        return {
            "kind": self.kind.value,
            "role": self.role,
            "namespace": list(self.namespace),
            "index": self.index,
            "name": self.name,
            "payload": self.payload,
            "at": self.at,
        }


__all__ = ["TraceEvent", "TraceKind", "TraceLevel"]
