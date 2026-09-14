"""The complete machine-readable record of a run (C9.7, OPEN-7).

**Written on every run**, not behind `--debug`, since OPEN-7. The flag
survives to force it back on when `[agent] debug_log = false`. The
reasoning is `transcript.py`'s, which this file did not originally
inherit: the point of a record is that it exists when somebody wants it,
which is always after the run that went wrong.

Two records, and the split is deliberate:

* the **transcript** is the readable one -- filtered to the trace level,
  payloads capped at 2000 chars, replayed by `rudra log`.
* this file is the **complete** one -- registered as a sink *recorder* so
  no level filter applies, payloads uncapped, plus every `rudra.*` log
  record and its traceback. It is what a bug report attaches.

Uncapped means unbounded, so retention is the bound: one file per run and
`prune_debug_logs` keeps the newest 20.

One configuration function, in one place -- the `deep_merge` precedent
(config/loader.py). A second place that configures logging is a second
answer to "why is this line missing".

Scoped to the `rudra` logger tree on purpose. Two Rudra modules already
log (llm/factory.py:32, memory/degrade.py:32) and had nowhere to be seen;
third-party loggers are left alone because httpx at DEBUG buries Rudra's
own lines in request noise. **One exception, at INFO only** (OPEN-113): the
provider clients' own request lines are forwarded under
`rudra.llm.transport`, because the openai SDK re-issues a failed call by
itself and says so nowhere this file could see.

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
from rudra.trace.redact import redact

LOGGER_NAME = "rudra"
"""The tree this attaches to. Rudra's own modules and nothing else."""

EVENT_LOGGER = "rudra.trace.event"
"""Where TraceEvents are logged, so they can be filtered apart from
ordinary log records by anyone reading the file."""

FILE_PREFIX = "debug-"
"""One file per run, named for the run, like transcripts. The prefix is
load-bearing: `prune_debug_logs` globs it specifically, and a bare
`*.jsonl` over this directory would delete `permissions.jsonl`."""

KEEP_RUNS = 20
"""Debug logs retained per project, newest first. Matches
`transcript.KEEP_RUNS` -- these files are uncapped, so bounding the
directory is the only thing bounding the disk they take."""


def debug_log_path(paths: Any, session_id: str) -> Path:
    """Where this run's log goes. One file per run, named for the run."""
    return Path(paths.logs) / f"{FILE_PREFIX}{session_id}.jsonl"


def prune_debug_logs(directory: Path, keep: int = KEEP_RUNS) -> list[Path]:
    """Keep the `keep` newest debug logs; delete the rest. Returns the kept.

    Called once when a run opens its log, not per record: retention is a
    per-run decision, and per record it would stat the directory thousands
    of times to reach the same answer (`transcript.prune_transcripts`).
    """
    directory = Path(directory)
    try:
        found = sorted(
            directory.glob(f"{FILE_PREFIX}*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    kept, extra = found[:keep], found[keep:]
    for path in extra:
        try:
            path.unlink()
        except OSError:
            continue
    return kept


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
        # Every line in this file carries a wall clock (OPEN-77), including
        # the ones that are not TraceEvents. `record.created` is the time
        # logging already stamped, so an ordinary log line and a trace event
        # are on the same clock rather than nearly on it.
        if "ts" not in payload:
            payload = {**payload, "ts": record.created}
        # The traceback, when there is one. memory/degrade.py logs with
        # exc_info=True and this dropped it, so the line a user attached to
        # a bug report -- as the troubleshooting docs ask -- carried the
        # message and none of the diagnosis (CR-G8).
        #
        # REDACTED, since OPEN-109. A traceback's last line is the
        # exception's own message, and a provider's exception carries
        # whatever it was given -- a base_url with a key in the query
        # string, an echoed request body. Every other writer of this file
        # redacts where the record is BUILT (A1.95, `trace/redact.py`), and
        # this was the one payload reaching it raw: measured, an exception
        # reading `api_key=sk-...` was redacted in `error_detail` and
        # printed in full three lines below it.
        if record.exc_info and "traceback" not in payload:
            payload = {**payload, "traceback": redact(self.formatException(record.exc_info))}
        return json.dumps(payload)


class _RunLogHandler(logging.FileHandler):
    """The handler THIS module attached, marked so it can be found again.

    Marked rather than matched by filename (OPEN-75) and rather than "every
    FileHandler on the tree": one run's log is one run's, but a handler an
    embedder attached to the `rudra` logger is not Rudra's to close.
    """


TRANSPORT_LOGGER = "rudra.llm.transport"
"""Where a provider client's own request lines are re-emitted (OPEN-113).

`grep '"logger": "rudra.llm.transport"'` reads every HTTP attempt behind a
model call, which the `model_call` line after them cannot show: that line
times the whole call, and inside it the client may have tried three times.
"""

TRANSPORT_SOURCES = ("openai._base_client", "anthropic._base_client", "httpx")
"""The client loggers forwarded, at INFO and above.

At INFO each says one line per event worth having. `openai._base_client`
logs `Retrying request to <path> in <n> seconds` for every re-issue its own
`max_retries` makes (`openai/_base_client.py:1801`; anthropic's client is
the same generator's output), and httpx logs `HTTP Request: POST <url>
"HTTP/1.1 500 Internal Server Error"` once per response
(`httpx/_client.py:1740`). Run `8f160d92c6da` recorded a 733.7 s call that
ended in a 500 and could not say whether it was one attempt or three. At
DEBUG the same loggers print every request body -- the noise this module
keeps out -- so the forwarder's own level is INFO whatever the logger's is.
"""


class _TransportForward(logging.Handler):
    """Re-emit one provider-client record under `TRANSPORT_LOGGER`.

    Redacted, because a request URL can carry a key and this file is what a
    user attaches to a public issue.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            logging.getLogger(TRANSPORT_LOGGER).debug(
                "%s: %s", record.name, redact(record.getMessage())
            )
        except Exception:  # noqa: BLE001 -- logging about logging never raises
            return


def _forward_transport_logs() -> None:
    """Attach the forwarder to each client logger, once per process.

    Idempotent by handler type, `telemetry/langfuse_sink.py`'s reason: the
    REPL opens a run log per input against one process, and a handler added
    per turn would multiply every line.

    A logger's level is lowered to INFO only when it is above INFO, so an
    `OPENAI_LOG=debug` the user set is left as they set it. `propagate` is
    NOT touched, unlike the langfuse loggers: `openai`'s own `OPENAI_LOG`
    handler sits on a parent of `openai._base_client`, and cutting
    propagation would silence the one switch a user debugging the SDK has.
    """
    for name in TRANSPORT_SOURCES:
        logger = logging.getLogger(name)
        if not any(isinstance(handler, _TransportForward) for handler in logger.handlers):
            logger.addHandler(_TransportForward(level=logging.INFO))
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)


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
        handler = _RunLogHandler(path, encoding="utf-8")
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
    # (CR-G5).
    #
    # By MARKER, not by filename (OPEN-75). The filename test could only
    # ever see the case CR-G5 was written for -- two runs sharing a path.
    # Every REPL turn is a new run with a new `debug-<id>.jsonl`, so turn
    # N's handler never matched, stayed attached, and wrote turn N+1's
    # records into turn N's file. Measured: run 510dc12d4bec's log held
    # its own 50 lines plus, byte for byte, the whole of the next run's.
    _detach_run_logs(logger)
    logger.addHandler(handler)
    # Rudra's own records stop here rather than climbing to the root
    # logger, whose handlers belong to whoever embedded Rudra.
    logger.propagate = False
    _forward_transport_logs()
    return handler


def _detach_run_logs(logger: logging.Logger) -> None:
    """Remove and close every run log this module attached to `logger`."""
    for existing in list(logger.handlers):
        if isinstance(existing, _RunLogHandler):
            logger.removeHandler(existing)
            existing.close()


def detach_debug_logging(handler: logging.Handler | None) -> None:
    """Release the run log at the end of the run that opened it.

    `configure_debug_logging` clears the previous run's handler on the way
    in, which needs a next run to happen. This is the other end, so a
    single run in a long-lived process -- the REPL, or an embedder -- does
    not leave a descriptor open on a file it has finished with.

    Called AFTER `archive_run` (OPEN-69's ordering), so `archive.py`'s own
    "archiving run ..." record still reaches the file it describes.

    `None` is the normal argument when `[agent] debug_log = false` or the
    path could not be opened, and it does nothing. Nothing here raises:
    bookkeeping must not end a run.
    """
    if handler is None:
        return
    logger = logging.getLogger(LOGGER_NAME)
    try:
        logger.removeHandler(handler)
        handler.close()
    except (OSError, ValueError):  # pragma: no cover - a closed handler
        return


def debug_consumer() -> Any:
    """A TraceSink consumer that logs each event as one JSON line.

    Register this with `TraceSink.add_recorder`, never `add`. As an
    ordinary consumer it sat behind the sink's level filter, so this file
    held only what the console had already printed and `--no-verbose` cut
    the bug-report log down to errors (OPEN-7). A recorder is fed every
    event, which is what makes this the complete record and the transcript
    the readable one.
    """
    logger = logging.getLogger(EVENT_LOGGER)

    def consume_event(event: TraceEvent) -> None:
        logger.debug(event.name or event.kind.value, extra={"event": event.as_dict()})

    return consume_event


__all__ = [
    "EVENT_LOGGER",
    "FILE_PREFIX",
    "KEEP_RUNS",
    "LOGGER_NAME",
    "TRANSPORT_LOGGER",
    "configure_debug_logging",
    "debug_consumer",
    "debug_log_path",
    "detach_debug_logging",
    "prune_debug_logs",
]
