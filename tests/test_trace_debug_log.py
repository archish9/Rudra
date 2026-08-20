"""--debug is for a bug report, not for the screen (Step 15a, C9.7).

The console keeps the human trace; this writes machine-readable JSONL to
the volatile subtree (D15), beside permissions.jsonl, verify.log and
usage.json. Mixing the two would mean the flag you set to diagnose a
problem is the flag that buries its symptom.
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


def test_the_sinks_level_still_governs_what_is_logged(tmp_path: Path, detached):
    """One filter, stated once. --debug on a QUIET sink logs errors only,
    which is why the help text names --debug --verbose together."""
    path = tmp_path / "debug.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    detached.append(handler)

    sink = TraceSink(level=TraceLevel.QUIET, consumers=[debug_consumer()])
    sink.emit(TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file"))
    sink.emit(TraceEvent(kind=TraceKind.TOOL_ERROR, role="coder", name="execute", payload="Error"))
    handler.flush()

    kinds = [json.loads(line)["kind"] for line in path.read_text().splitlines() if line]
    assert kinds == ["tool_error"]
