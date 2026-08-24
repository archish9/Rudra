"""The run log is for a bug report, not for the screen (Step 15a, C9.7).

The console keeps the human trace; this writes machine-readable JSONL to
the volatile subtree (D15), beside permissions.jsonl, verify.log and
usage.json. Mixing the two would mean the flag you set to diagnose a
problem is the flag that buries its symptom.

Since OPEN-7 it is written on every run rather than behind `--debug`, and
registered as a sink *recorder* so no display setting decides what a bug
report contains. Uncapped, therefore bounded by retention instead:
`prune_debug_logs` keeps the newest 20 and must never touch the other
files in that directory.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from rudra.trace import TraceEvent, TraceKind, TraceLevel
from rudra.trace.debug import LOGGER_NAME, configure_debug_logging, debug_consumer
from rudra.trace.sink import TraceSink


@pytest.fixture
def detached() -> Iterator[list[logging.Handler]]:
    """Undo whatever the test attached to the `rudra` logger tree.

    The logger tree is process-global, so a handler left behind writes
    another test's records into a deleted tmp_path -- and the failure
    surfaces in whichever test runs next, not this one.
    """
    logger = logging.getLogger(LOGGER_NAME)
    before_handlers = list(logger.handlers)
    before_level, before_propagate = logger.level, logger.propagate
    attached: list[logging.Handler] = []
    yield attached
    for handler in attached:
        logger.removeHandler(handler)
        handler.close()
    logger.handlers = before_handlers
    logger.level, logger.propagate = before_level, before_propagate


def test_nothing_is_written_when_the_flag_is_off(tmp_path: Path):
    path = tmp_path / "debug.jsonl"
    assert configure_debug_logging(path, enabled=False) is None
    assert not path.exists()


def test_every_line_is_valid_json_and_carries_the_event(tmp_path: Path, detached):
    path = tmp_path / "debug.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[debug_consumer()])
    sink.emit(
        TraceEvent(
            kind=TraceKind.TOOL_CALL,
            role="coder",
            index=1,
            name="write_file",
            payload="{'file_path': 'a.py'}",
        )
    )
    handler.flush()

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert lines[0]["name"] == "write_file"
    assert lines[0]["kind"] == "tool_call"
    assert lines[0]["role"] == "coder"


def test_an_existing_rudra_logger_also_lands_in_the_file(tmp_path: Path, detached):
    """rudra.llm.factory and rudra.memory.degrade already log and had
    nowhere to be seen; --debug is where that output was always meant to
    go."""
    path = tmp_path / "debug.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    logging.getLogger("rudra.memory.degrade").debug("palace unavailable")
    handler.flush()

    assert "palace unavailable" in path.read_text(encoding="utf-8")


def test_third_party_loggers_are_not_captured(tmp_path: Path, detached):
    """httpx at DEBUG would bury Rudra's own lines in request noise."""
    path = tmp_path / "debug.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    logging.getLogger("httpx").debug("GET /v1/chat/completions")
    handler.flush()

    assert "chat/completions" not in path.read_text(encoding="utf-8")


def test_an_unwritable_path_disables_the_log_rather_than_failing(tmp_path: Path, detached):
    """write_usage_log's rule: bookkeeping that cannot be written must not
    end a run that is otherwise fine."""
    occupied = tmp_path / "not-a-dir"
    occupied.write_text("i am a file")

    assert configure_debug_logging(occupied / "debug.jsonl", enabled=True) is None


def test_the_sinks_level_does_not_govern_what_is_logged(tmp_path: Path, detached):
    """OPEN-7 inverted this test's predecessor, which asserted that a QUIET
    sink logged errors only and called that "one filter, stated once".

    It was one filter in the wrong place. Registered with `add_recorder`,
    the log holds every event on the quietest possible run -- which is what
    "save everything" has to mean if the file is worth attaching to a bug
    report at all."""
    path = tmp_path / "debug-run1.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    sink = TraceSink(level=TraceLevel.QUIET)
    sink.add_recorder(debug_consumer())
    sink.emit(TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file"))
    sink.emit(TraceEvent(kind=TraceKind.TOOL_ERROR, role="coder", name="execute", payload="Error"))
    handler.flush()

    kinds = [json.loads(line)["kind"] for line in path.read_text().splitlines() if line]
    assert kinds == ["tool_call", "tool_error"]


def test_a_payload_larger_than_the_transcripts_cap_survives_whole(tmp_path: Path, detached):
    """The difference between this file and the transcript, asserted. A
    failing-pytest tool result was measured at a third of a 32B context
    window (A1.47) -- the transcript caps it at 2000 chars, and this is the
    copy that still has the rest of it."""
    from rudra.trace.transcript import PAYLOAD_CAP

    payload = "x" * (PAYLOAD_CAP * 3)
    path = tmp_path / "debug-run1.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    sink = TraceSink(level=TraceLevel.NORMAL)
    sink.add_recorder(debug_consumer())
    sink.emit(TraceEvent(kind=TraceKind.TOOL_RESULT, role="coder", name="execute", payload=payload))
    handler.flush()

    records = [json.loads(line) for line in path.read_text().splitlines() if line]
    logged = [r for r in records if r.get("kind") == "tool_result"]
    assert logged and logged[0]["payload"] == payload


# --- retention ------------------------------------------------------------


def _make_logs(directory: Path, count: int) -> None:
    import os
    import time

    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for index in range(count):
        path = directory / f"debug-run{index:03d}.jsonl"
        path.write_text("{}\n")
        # Explicit mtimes: the writes are far faster than the filesystem's
        # timestamp resolution, so "newest" is otherwise a coin toss.
        os.utime(path, (now + index, now + index))


def test_pruning_keeps_the_newest_and_deletes_the_rest(tmp_path: Path):
    from rudra.trace.debug import prune_debug_logs

    _make_logs(tmp_path, 25)

    kept = prune_debug_logs(tmp_path, keep=20)

    assert len(kept) == 20
    remaining = sorted(p.name for p in tmp_path.glob("debug-*.jsonl"))
    assert remaining == sorted(f"debug-run{i:03d}.jsonl" for i in range(5, 25))


def test_pruning_never_touches_the_other_logs(tmp_path: Path):
    """The reason the filenames carry a prefix at all: `permissions.jsonl`
    lives in this directory, and a `*.jsonl` glob would delete the audit
    log every run."""
    from rudra.trace.debug import prune_debug_logs

    _make_logs(tmp_path, 25)
    for name in ("permissions.jsonl", "usage.json", "verify.log"):
        (tmp_path / name).write_text("keep me")

    prune_debug_logs(tmp_path, keep=1)

    for name in ("permissions.jsonl", "usage.json", "verify.log"):
        assert (tmp_path / name).exists(), name


def test_pruning_a_missing_directory_is_not_an_error(tmp_path: Path):
    """Bookkeeping never ends a run (loop/engine.py:501-515)."""
    from rudra.trace.debug import prune_debug_logs

    assert prune_debug_logs(tmp_path / "nope") == []
