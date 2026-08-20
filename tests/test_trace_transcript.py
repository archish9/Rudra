"""One run, one file, written by a second consumer on the same sink (C9.5).

Not a second pipeline: the console, the `--debug` log and this all read
the same TraceEvent, so the live view and the replay cannot disagree about
what happened.

Redaction is NOT repeated here. A1.95 is fixed at the event boundary in
stream.py, so by the time an event reaches this writer its payload is
already clean -- and a second pass would imply the first one is optional.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rudra.trace import TraceEvent, TraceKind, TraceLevel
from rudra.trace.sink import TraceSink
from rudra.trace.transcript import (
    KEEP_RUNS,
    PAYLOAD_CAP,
    TranscriptWriter,
    prune_transcripts,
    read_transcript,
)


def _event(payload="hi", kind=TraceKind.TOOL_RESULT, name="write_file", role="coder"):
    return TraceEvent(kind=kind, role=role, index=1, name=name, payload=payload)


def test_every_emitted_event_reaches_the_file(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[writer])

    sink.emit(_event("first"))
    sink.emit(_event("second"))
    writer.close()

    lines = [json.loads(line) for line in path.read_text().splitlines() if line]
    assert [line["payload"] for line in lines] == ["first", "second"]


def test_events_are_readable_before_the_writer_is_closed(tmp_path: Path):
    """A transcript that only survives a clean exit is useless for the runs
    people actually want to inspect -- the ones that died."""
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)

    writer(_event("mid-run"))

    assert "mid-run" in path.read_text()
    writer.close()


def test_a_huge_payload_is_capped_with_an_honest_marker(tmp_path: Path):
    """A failing-pytest tool result is the payload Step 12a measured at a
    third of a 32B window (A1.47). An uncapped always-on record of it is
    S12.4's Session Log lesson unlearned."""
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    writer(_event("x" * (PAYLOAD_CAP * 3)))
    writer.close()

    written = json.loads(path.read_text().splitlines()[0])["payload"]
    assert len(written) < PAYLOAD_CAP * 2
    assert "+" in written and "chars" in written


def test_a_payload_under_the_cap_is_untouched(tmp_path: Path):
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    writer(_event("short"))
    writer.close()

    assert json.loads(path.read_text().splitlines()[0])["payload"] == "short"


def test_the_round_trip_returns_real_events(tmp_path: Path):
    """`rudra log` renders these through the SAME render() the run used, so
    they have to come back as TraceEvents, not as dicts."""
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    writer(_event("first", kind=TraceKind.TOOL_CALL, name="execute"))
    writer.close()

    events = read_transcript(path)

    assert isinstance(events[0], TraceEvent)
    assert events[0].kind is TraceKind.TOOL_CALL
    assert events[0].name == "execute"


def test_a_namespace_survives_the_round_trip(tmp_path: Path):
    """The namespace is what tells a delegated subagent's line apart from
    its parent's, and it is a tuple on the way out and a list in JSON."""
    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    writer(
        TraceEvent(
            kind=TraceKind.TOOL_CALL,
            role="coder",
            namespace=("tools", "task:1"),
            name="write_file",
        )
    )
    writer.close()

    assert read_transcript(path)[0].namespace == ("tools", "task:1")


def test_a_corrupt_line_does_not_lose_the_rest(tmp_path: Path):
    """A run killed with -9 mid-write leaves a partial last line. A record
    that refuses to open because its final line is truncated is worse than
    one that is a line short."""
    path = tmp_path / "s1.jsonl"
    path.write_text('{"kind": "ai_text", "role": "coder", "payload": "ok"}\n{"kind": "ai_te')

    assert [event.payload for event in read_transcript(path)] == ["ok"]


def test_reading_a_file_that_is_not_there_returns_nothing(tmp_path: Path):
    assert read_transcript(tmp_path / "nope.jsonl") == []


def test_retention_keeps_the_newest_and_prunes_the_rest(tmp_path: Path):
    for index in range(KEEP_RUNS + 5):
        path = tmp_path / f"run{index:03d}.jsonl"
        path.write_text("{}\n")
        stamp = time.time() + index
        os.utime(path, (stamp, stamp))

    kept = prune_transcripts(tmp_path, keep=KEEP_RUNS)

    assert len(list(tmp_path.glob("*.jsonl"))) == KEEP_RUNS
    assert tmp_path / f"run{KEEP_RUNS + 4:03d}.jsonl" in kept
    assert not (tmp_path / "run000.jsonl").exists()


def test_retention_does_nothing_when_under_the_limit(tmp_path: Path):
    for index in range(3):
        (tmp_path / f"run{index}.jsonl").write_text("{}\n")

    prune_transcripts(tmp_path, keep=KEEP_RUNS)

    assert len(list(tmp_path.glob("*.jsonl"))) == 3


def test_pruning_a_missing_directory_is_harmless(tmp_path: Path):
    assert prune_transcripts(tmp_path / "nope") == []


def test_a_writer_that_cannot_open_its_file_does_not_raise(tmp_path: Path):
    """write_usage_log's rule: bookkeeping must not end a run that did its
    work."""
    occupied = tmp_path / "not-a-dir"
    occupied.write_text("i am a file")

    writer = TranscriptWriter(occupied / "s1.jsonl")
    writer(_event("hi"))
    writer.close()


def test_the_writer_does_not_redact_because_the_event_already_is(tmp_path: Path):
    """A1.95 is fixed where the event is BUILT. This asserts the boundary
    rather than duplicating it: a payload arriving here dirty would mean
    stream.py stopped redacting, and that is the bug to catch."""
    from langchain_core.messages import ToolMessage

    from rudra.trace.stream import StreamState, consume

    chunk = (
        (),
        {
            "messages": [
                ToolMessage(
                    content="NVIDIA_API_KEY=nvapi-aaaaaaaaaaaaaaaaaaaa",
                    tool_call_id="1",
                    name="read_file",
                )
            ]
        },
    )
    event = consume(chunk, StreamState(role="planner"))[0]

    path = tmp_path / "s1.jsonl"
    writer = TranscriptWriter(path)
    writer(event)
    writer.close()

    assert "nvapi-aaa" not in path.read_text()
