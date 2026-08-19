"""The palace's durable copy, and the way back in.

Rudra writes both halves rather than calling mempalace's exporter, which
resolves collection_name and backend from ~/.mempalace/config.json
(exporter.py:83) and would export a collection Rudra never wrote to --
reporting success while doing it (A1.87).

Owning the writer means owning the reader, so the round trip is exact.
Routing an export back through miner.mine would re-chunk it and re-detect
rooms: a lossy import dressed as a restore.

The format is markdown with an HTML-comment header per entry. A comment
rather than YAML frontmatter because there are many entries per file and
frontmatter is a whole-document construct; a comment is invisible in a
rendered diff and trivially parsed back.

Imports nothing from mempalace -- everything goes through MemoryStore.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rudra.memory.entry import ROOM_NAMES, EntryRejected, MemoryEntry

_HEADER = re.compile(r"^<!-- rudra-memory added_by=(\S+) filed_at=(\S*) -->$")


def export_memory(store: Any, out_dir: Path) -> dict[str, int]:
    """Write every drawer to `<out_dir>/<room>.md`. Returns counts."""
    out_dir = Path(out_dir)
    rows = store.list_entries(limit=10_000)
    if not rows:
        return {"drawers": 0, "rooms": 0}

    out_dir.mkdir(parents=True, exist_ok=True)
    by_room: dict[str, list] = {}
    for row in rows:
        by_room.setdefault(row.room, []).append(row)

    for room, entries in by_room.items():
        lines = [f"# {room}", ""]
        for entry in entries:
            lines.append(
                f"<!-- rudra-memory added_by={entry.added_by} filed_at={entry.filed_at} -->"
            )
            lines.append(entry.content)
            lines.append("")
        (out_dir / f"{room}.md").write_text("\n".join(lines), encoding="utf-8")

    return {"drawers": len(rows), "rooms": len(by_room)}


def import_memory(store: Any, in_dir: Path) -> dict[str, int]:
    """Read an exported tree back into the palace. Returns counts.

    Idempotent: drawer ids are content-addressed, so importing the same
    tree twice writes the same ids twice and the collection holds one copy.
    """
    in_dir = Path(in_dir)
    if not in_dir.is_dir():
        return {"drawers": 0, "rooms": 0}

    written = 0
    rooms = 0
    for path in sorted(in_dir.glob("*.md")):
        room = path.stem
        if room not in ROOM_NAMES:
            continue
        rooms += 1
        for added_by, content in _parse(path.read_text(encoding="utf-8")):
            try:
                entry = MemoryEntry(content=content, room=room, added_by=added_by)
            except EntryRejected:
                # A hand-edited export is a normal thing for a durable text
                # file to become. Skip the entry rather than failing the
                # restore -- the rest of the file is still good.
                continue
            if store.write(entry):
                written += 1
    return {"drawers": written, "rooms": rooms}


def _parse(text: str) -> list[tuple[str, str]]:
    """(added_by, content) for each entry in one room file."""
    entries: list[tuple[str, str]] = []
    added_by: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        match = _HEADER.match(line.strip())
        if match:
            if added_by is not None:
                entries.append((added_by, "\n".join(body).strip()))
            added_by = match.group(1)
            body = []
        elif added_by is not None:
            body.append(line)
    if added_by is not None:
        entries.append((added_by, "\n".join(body).strip()))
    return [(author, content) for author, content in entries if content]


__all__ = ["export_memory", "import_memory"]
