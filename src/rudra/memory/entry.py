"""One memory, and what may be in it.

Pure data. Imports nothing from Rudra, so its tests need no model, no
backend and no gate -- the rule facts/store.py and loop/ledger.py follow.

MAX_CONTENT is 8000 rather than mempalace's 800-char chunk_size because
store.py chunks: an 8000-character architecture summary is a legitimate
memory, an unbounded one is a way to put a whole transcript in a vector
store, which C8.3 forbids by name.
"""

from __future__ import annotations

from dataclasses import dataclass

# The four rooms are a closed set (spec §4.1). Rooms are a search filter,
# and a taxonomy the model invents per run filters nothing.
ROOM_NAMES = ("decisions", "tasks", "blockers", "preferences")

# Who wrote it. The deterministic Python spine writes `rudra`; the
# `remember` tool writes `agent`. Every listing and every `forget` in 14c
# keys off this, which is the whole reason it is stored per drawer.
VALID_ADDED_BY = ("rudra", "agent")

MAX_CONTENT = 8000


class EntryRejected(ValueError):
    """An entry failed validation.

    The message names both the limit and what was given, because the next
    thing that happens may be a model reading it and trying again.
    """


@dataclass(frozen=True)
class MemoryEntry:
    """One thing worth remembering across runs."""

    content: str
    room: str
    added_by: str
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            msg = f"content must be a non-empty string, got {self.content!r}."
            raise EntryRejected(msg)
        stripped = self.content.strip()
        if len(stripped) > MAX_CONTENT:
            msg = f"content must be at most {MAX_CONTENT} characters, got {len(stripped)}."
            raise EntryRejected(msg)
        object.__setattr__(self, "content", stripped)

        if self.room not in ROOM_NAMES:
            msg = f"room must be one of {', '.join(ROOM_NAMES)}, got {self.room!r}."
            raise EntryRejected(msg)
        if self.added_by not in VALID_ADDED_BY:
            msg = f"added_by must be one of {', '.join(VALID_ADDED_BY)}, got {self.added_by!r}."
            raise EntryRejected(msg)


__all__ = ["MAX_CONTENT", "ROOM_NAMES", "VALID_ADDED_BY", "EntryRejected", "MemoryEntry"]
