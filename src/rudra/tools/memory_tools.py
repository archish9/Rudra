"""What the model may do with long-term memory.

Two tools, and the split is S14.3's: Python records what it can prove
from the ledger and from git, and this is for the rest -- a preference
the user stated, an API that behaves unlike its documentation. Anything
`remember` writes is tagged `added_by="agent"`, so a reader, a search
result and 14c's `forget` can all tell it from the deterministic spine.

Neither tool raises. A model reads what comes back and tries again, so a
rejection is a sentence saying what would be acceptable -- the REJECTED
idiom interaction_tools.py and loop/tools.py already use.

The factory signature carries `gate`, `console` and `cfg` it mostly does
not use, because `_TOOL_FACTORIES` in subagents/build.py calls every
factory with the same four arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from rudra.memory.entry import ROOM_NAMES, EntryRejected, MemoryEntry
from rudra.memory.store import MemoryStore

MAX_HITS = 5


def create_memory_tools(
    project_path: Path,
    *,
    gate: Any = None,
    console: Any = None,
    cfg: Any = None,
) -> list[BaseTool]:
    """The memory tools for one run."""
    backend = getattr(getattr(cfg, "memory", None), "backend", "chroma")
    store = MemoryStore(project_path, backend=backend)
    rooms = ", ".join(ROOM_NAMES)

    @tool
    def remember(content: str, room: str) -> str:
        """Record something worth knowing on a future run of this project.

        Use this for what you established but nothing else will record:
        a preference the user stated, a constraint you discovered, an API
        that behaves unlike its documentation. Completed and blocked tasks
        are recorded automatically -- do not repeat them here.

        Args:
            content: One specific thing, in a sentence or two. Include why,
                not only what.
            room: One of: decisions, tasks, blockers, preferences.
        """
        try:
            entry = MemoryEntry(content=content, room=room, added_by="agent")
        except EntryRejected as exc:
            return f"REJECTED: {exc} Valid rooms: {rooms}."
        if not store.write(entry):
            return (
                "Memory is unavailable for this run, so nothing was recorded. "
                "This is not your error -- carry on with the task."
            )
        return f"Recorded in {room}."

    @tool
    def search_memory(query: str, room: str | None = None) -> str:
        """Search what earlier runs on this project recorded.

        Use this when you need history the prompt does not carry -- why a
        choice was made, what was tried before, what blocked last time.

        Args:
            query: A natural-language question or phrase.
            room: Optionally narrow to one of: decisions, tasks, blockers,
                preferences.
        """
        if room is not None and room not in ROOM_NAMES:
            return f"REJECTED: unknown room {room!r}. Valid rooms: {rooms}."
        hits = store.search(query, room=room, limit=MAX_HITS)
        if not hits:
            return "Nothing recorded on this project matches that."
        return "\n".join(f"- [{hit.room}] {hit.content}  ({hit.added_by})" for hit in hits)

    return [remember, search_memory]


__all__ = ["MAX_HITS", "create_memory_tools"]
