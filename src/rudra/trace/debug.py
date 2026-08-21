"""`--debug`: the machine-readable half of the trace (C9.7).

One configuration function, in one place -- the `deep_merge` precedent
(config/loader.py). A second place that configures logging is a second
answer to "why is this line missing".

Scoped to the `rudra` logger tree on purpose. Two Rudra modules already
log (llm/factory.py:32, memory/degrade.py:32) and had nowhere to be seen;
third-party loggers are left alone because httpx at DEBUG buries Rudra's
own lines in request noise.

The console is deliberately untouched: the human trace is the sink's
console consumer, and this file is what gets attached to a bug report.
A flag that helps you diagnose must not be the flag that hides the
symptom.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rudra.trace.events import TraceEvent

LOGGER_NAME = "rudra"
"""The tree this attaches to. Rudra's own modules and nothing else."""

EVENT_LOGGER = "rudra.trace.event"
"""Where TraceEvents are logged, so they can be filtered apart from
ordinary log records by anyone reading the file."""


class _JsonLines(logging.Formatter):
    """One JSON object per line.

    A TraceEvent keeps its own shape -- the same dict the transcript will
    write in 15c, because both read `TraceEvent.as_dict()` rather than
    formatting their own. Anything else is wrapped so the file has exactly
    one grammar.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = getattr(record, "event", None) or {
            "kind": "log",
            "logger": record.name,
            "level": record.levelname,
            "payload": record.getMessage(),
        }
        # The traceback, when there is one. memory/degrade.py logs with
        # exc_info=True and this dropped it, so the line a user attached to
        # a bug report -- as the troubleshooting docs ask -- carried the
        # message and none of the diagnosis (CR-G8).
        if record.exc_info and "traceback" not in payload:
            payload = {**payload, "traceback": self.formatException(record.exc_info)}
        return json.dumps(payload)


def configure_debug_logging(path: Path, *, enabled: bool) -> logging.Handler | None:
    """Attach a JSONL handler to the `rudra` tree, or do nothing.

    Returns the handler so a caller -- and a test -- can flush and detach
    it. Failure to open the file is swallowed and reported as None: a
    debug log that cannot be written must not end a run that is otherwise
    fine, which is write_usage_log's rule (loop/engine.py:501-515).
    """
    if not enabled:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        return None

    handler.setFormatter(_JsonLines())
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    # Replace, never accumulate. The REPL builds a fresh agent per input, so
    # this ran once per turn with no removal path and nothing detaching the
    # previous handler -- at turn N the logger held N handlers on the same
    # file, wrote every record N times, and kept N descriptors open. That
    # file is the one the docs tell users to attach to a bug report
    # (CR-G5). Matching by the resolved filename rather than by identity so
    # a handler left by an earlier agent is also cleared.
    resolved = str(Path(handler.baseFilename).resolve())
    for existing in list(logger.handlers):
        if isinstance(existing, logging.FileHandler) and (
            str(Path(existing.baseFilename).resolve()) == resolved
        ):
            logger.removeHandler(existing)
            existing.close()
    logger.addHandler(handler)
    # Rudra's own records stop here rather than climbing to the root
    # logger, whose handlers belong to whoever embedded Rudra.
    logger.propagate = False
    return handler


def debug_consumer() -> Any:
    """A TraceSink consumer that logs each event as one JSON line.

    The sink filters before this runs, so `--debug` on a QUIET level logs
    errors only. That is one filter stated once rather than two that can
    disagree -- and it is why `--debug --verbose` is the pair a bug report
    wants.
    """
    logger = logging.getLogger(EVENT_LOGGER)

    def consume_event(event: TraceEvent) -> None:
        logger.debug(event.name or event.kind.value, extra={"event": event.as_dict()})

    return consume_event


__all__ = ["EVENT_LOGGER", "LOGGER_NAME", "configure_debug_logging", "debug_consumer"]
