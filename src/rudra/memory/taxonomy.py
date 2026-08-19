"""Where a memory goes: which wing, which room.

Wing = the project. Room = the category, a closed set of four.

The regex here mirrors mempalace's sanitize_name (config.py:73) rather
than calling it, for the reason every pure module in Rudra gives: this
file imports nothing, so its tests need no ChromaDB and no 80 MB model
download. test_memory_taxonomy.py checks the mirror against the real
pattern, so the two cannot drift silently.

Trimming rather than rejecting is deliberate. A user does not choose
their directory name to please a vector store, and `_scratch` is a
perfectly ordinary directory. Only a name with nothing usable left is an
error, and then it says so loudly -- because the alternative is every
project called `___` sharing one wing.
"""

from __future__ import annotations

import re
from pathlib import Path

from rudra.memory.entry import ROOM_NAMES

ROOMS = ROOM_NAMES

MAX_WING = 128

# Anything mempalace's safe-name set does not contain. Its pattern allows
# word characters, space, dot, apostrophe and hyphen.
_ILLEGAL = re.compile(r"[^\w .'-]")

# Its ends rule: first and last character must be alphanumeric, which
# excludes underscore because `[^\W_]` subtracts it from `\w`.
_TRIM_ENDS = re.compile(r"^[\W_]+|[\W_]+$")


class TaxonomyError(ValueError):
    """A project name cannot be turned into a usable wing."""


def wing_for(project_root: Path) -> str:
    """The wing for one project, sanitized to mempalace's contract."""
    raw = Path(project_root).resolve().name
    cleaned = _ILLEGAL.sub("-", raw)
    cleaned = _TRIM_ENDS.sub("", cleaned)[:MAX_WING]
    cleaned = _TRIM_ENDS.sub("", cleaned)
    if not cleaned:
        msg = (
            f"Project directory name {raw!r} has no characters a memory wing "
            f"can use. A wing must start and end with a letter or digit."
        )
        raise TaxonomyError(msg)
    return cleaned


__all__ = ["MAX_WING", "ROOMS", "TaxonomyError", "wing_for"]
