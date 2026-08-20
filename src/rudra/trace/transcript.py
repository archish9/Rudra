"""One run's record, on disk, readable afterwards (C9.5, Step 15c).

A second consumer on the sink the console and `--debug` already share --
not a second pipeline. All three read the same TraceEvent, so the live
view and the replay cannot disagree about what happened; they differ only
in how much of it each keeps.

**Written by default**, unlike `--debug`, because the point of a record is
that it exists when somebody wants it, which is always after the fact.
That is only safe because A1.95's redaction happens where the event is
BUILT (`stream.py`), so every payload arriving here is already clean.
**There is deliberately no second redaction pass**: a redactor applied
twice implies the first one is optional, and if a future change moves
redaction into the renderer this file silently starts collecting
credentials again. `test_the_writer_does_not_redact_because_the_event_already_is`
is the tripwire.

A transcript is a HUMAN record, never a replay (S15.3). Nothing reads it
back into a model, `--continue` does not know it exists, and no
`thread_id` is reused. Continuity is the ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rudra.trace.events import TraceEvent, TraceKind

PAYLOAD_CAP = 2000
"""Characters of one payload kept in the record.

Applied HERE and not in the event, because the console and the debug log
both want the whole thing -- only the always-on record trades completeness
for size. The number matters: one failing-pytest tool result was measured
at a third of a 32B context window (A1.47), and an uncapped file written
on every run is S12.4's capped-Session-Log lesson unlearned."""

KEEP_RUNS = 20
"""Transcripts retained per project, newest first. Matches the Session Log
cap for the same reason: a directory that grows without bound is a tax
nobody agreed to pay."""


def transcript_path(paths: Any, session_id: str) -> Path:
    """Where this run's record goes. One file per run, named for the run."""
    return Path(paths.transcripts) / f"{session_id}.jsonl"


def _capped(payload: str) -> str:
    if len(payload) <= PAYLOAD_CAP:
        return payload
    return payload[:PAYLOAD_CAP] + f" … +{len(payload) - PAYLOAD_CAP} chars"


class TranscriptWriter:
    """A sink consumer that appends one JSON object per event.

    Opened lazily and flushed per event. Flushing every time costs one
    small write and buys the case that matters: a run that was killed, ran
    out of memory, or hung is exactly the run somebody wants the record
    of, and a buffered file loses precisely that.

    A file it cannot open disables the record rather than ending the run --
    write_usage_log's rule (loop/engine.py).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Any = None
        self._broken = False

    def _open(self) -> Any:
        if self._handle is None and not self._broken:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handle = self.path.open("a", encoding="utf-8")
            except OSError:
                self._broken = True
        return self._handle

    def __call__(self, event: TraceEvent) -> None:
        handle = self._open()
        if handle is None:
            return
        record = event.as_dict()
        record["payload"] = _capped(record["payload"])
        try:
            handle.write(json.dumps(record) + "\n")
            handle.flush()
        except (OSError, TypeError, ValueError):
            # A record that cannot be written is a record that is missing,
            # never a run that failed.
            self._broken = True

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None


def read_transcript(path: Path) -> list[TraceEvent]:
    """Read a transcript back as events, skipping anything unreadable.

    Tolerant on purpose. A run killed mid-write leaves a truncated final
    line, and a reader that refuses the whole file over its last line is
    worse than one that returns a line fewer.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    events: list[TraceEvent] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            events.append(
                TraceEvent(
                    kind=TraceKind(record["kind"]),
                    role=record.get("role", ""),
                    namespace=tuple(record.get("namespace", ())),
                    index=record.get("index", 0),
                    name=record.get("name", ""),
                    payload=record.get("payload", ""),
                    at=record.get("at", 0.0),
                )
            )
        except (ValueError, KeyError, TypeError):
            continue
    return events


def prune_transcripts(directory: Path, keep: int = KEEP_RUNS) -> list[Path]:
    """Keep the `keep` newest transcripts; delete the rest. Returns the kept.

    Called once when a run registers its writer, not per event: retention
    is a per-run decision and doing it per event would stat the directory
    thousands of times to reach the same answer.
    """
    directory = Path(directory)
    try:
        found = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []

    kept, extra = found[:keep], found[keep:]
    for path in extra:
        try:
            path.unlink()
        except OSError:
            continue
    return kept


__all__ = [
    "KEEP_RUNS",
    "PAYLOAD_CAP",
    "TranscriptWriter",
    "prune_transcripts",
    "read_transcript",
    "transcript_path",
]
