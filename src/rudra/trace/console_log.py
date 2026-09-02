"""What Rudra says about ITSELF, into the run log (OPEN-76).

The debug log is fed by `TraceSink` events and by three modules that call
`logging` -- and by nothing else. Everything else Rudra tells the user goes
through `rich.Console.print`, of which there are 163 call sites, and lands
in a terminal that scrolls away:

    the rendered plan · the task panel with the effective models and the
    permission notice · the result panel · "Cancelled. The ledger is
    intact" · the committed-key warning · "Could not open the run log at
    ..." · and `RudraAgent.run()`'s own crash traceback

That last one is the sharpest: an unhandled exception prints
`traceback.format_exc()` to the console and re-raises, so the one thing a
bug report most needs is the one thing the bug-report file could not
contain.

**A tee under `Console.file`, not 163 edits.** `rich.Console.file` is a
settable property, so the whole console is captured at one seam and no
call site changes. Rich asks the file whether it is a terminal and for its
descriptor when it picks colour and width, so the wrapper delegates both:
a wrapper that answers differently silently reformats every panel.

**Written to the debug log only.** The transcript is the readable record
of what the MODEL did (`trace/transcript.py`), and console lines are what
RUDRA said -- the same split `TraceKind.NOTICE` draws inside the event
vocabulary.

**Redacted here, because this is where the record is built** (A1.95).
Console text is not automatically safe: `_provider_failure_message` prints
`str(error)`, and a provider's own exception can carry a URL with
credentials in it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any

from rudra.trace.redact import redact

CONSOLE_LOGGER = "rudra.trace.console"
"""Where console lines are logged, so a reader can filter them apart from
TraceEvents and from ordinary log records -- the reason `EVENT_LOGGER`
exists, one kind over."""

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
"""Escape sequences Rich emits. The log holds what a human saw, not what
the terminal was driven with: ANSI inside a JSON payload is unreadable in
exactly the situation the file is opened."""


_LOCAL = threading.local()
"""Per-thread suppression depth. Thread-local rather than global because
the plan gate prints from a daemon thread while the loop thread streams
(`_approve_off_loop`), and a global would let one silence the other."""


@contextmanager
def suppress_console_record() -> Any:
    """Print without recording, for text that is ALREADY an event.

    The sink's console consumer renders every TraceEvent to this same
    console, so without this the log would hold each of those twice: once
    as the event, once as its own rendering. The event is the complete
    record -- uncapped payload, real arguments -- and the rendering is a
    wrapped, truncated view of it, so the copy costs size and adds
    nothing.

    What is left is exactly the half that had no record at all: the plan,
    the panels, the warnings, the crash traceback.
    """
    depth = getattr(_LOCAL, "depth", 0)
    _LOCAL.depth = depth + 1
    try:
        yield
    finally:
        _LOCAL.depth = depth


def _recording() -> bool:
    return getattr(_LOCAL, "depth", 0) == 0


class _ConsoleTee:
    """A file object that writes on and reports each complete line.

    Line-buffered rather than per-write because Rich calls `write` many
    times for one visual line -- once per style run -- and a record per
    fragment is not a record of anything.
    """

    def __init__(self, wrapped: Any, emit: Any) -> None:
        self._wrapped = wrapped
        self._emit = emit
        self._buffer = ""

    # -- the half Rich writes through ------------------------------------

    def write(self, text: str) -> int:
        written = self._wrapped.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            self._report(line)
        return written

    def flush(self) -> None:
        self._wrapped.flush()

    def close_line(self) -> None:
        """Report a trailing fragment that never got its newline."""
        if self._buffer:
            self._report(self._buffer)
            self._buffer = ""

    def _report(self, line: str) -> None:
        if not _recording():
            return
        plain = _ANSI.sub("", line).rstrip()
        if not plain.strip():
            # Blank lines are spacing. A log of them is a log of nothing.
            return
        try:
            self._emit(plain)
        except Exception:  # noqa: BLE001 - recording must not break printing
            return

    # -- the half Rich ASKS ----------------------------------------------
    #
    # Console consults these to decide colour, width and whether it may
    # move the cursor. Anything not answered here is delegated, so a
    # future Rich asking a new question gets the wrapped file's answer
    # rather than an AttributeError.

    def isatty(self) -> bool:
        isatty = getattr(self._wrapped, "isatty", None)
        return bool(isatty()) if isatty is not None else False

    def fileno(self) -> int:
        return self._wrapped.fileno()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def console_emitter() -> Any:
    """A callable that logs one console line into the run log."""
    logger = logging.getLogger(CONSOLE_LOGGER)

    def emit(line: str) -> None:
        # `ts` because a console line is the one record whose whole value is
        # "what did the user see, and when" (OPEN-77). It is built here
        # rather than by TraceEvent, so it carries its own clock.
        logger.debug(
            line,
            extra={"event": {"kind": "console", "payload": redact(line), "ts": time.time()}},
        )

    return emit


def install_console_recorder(console: Any, emit: Any = None) -> Any:
    """Tee `console` into the run log. Returns the console, for chaining.

    Idempotent: installing twice would record every line twice, and the
    REPL builds a fresh agent per turn against ONE console object.
    """
    if isinstance(getattr(console, "file", None), _ConsoleTee):
        return console
    try:
        console.file = _ConsoleTee(console.file, emit or console_emitter())
    except Exception:  # noqa: BLE001 - a console that refuses keeps printing
        return console
    return console


def remove_console_recorder(console: Any) -> None:
    """Put the original file back, reporting any unterminated line.

    Never raises, and does nothing to a console that was never wrapped:
    `close()` runs on every path, including the ones that got here by
    failing.
    """
    tee = getattr(console, "file", None)
    if not isinstance(tee, _ConsoleTee):
        return
    try:
        tee.close_line()
        console.file = tee._wrapped
    except Exception:  # noqa: BLE001 - bookkeeping must not end a run
        return


__all__ = [
    "CONSOLE_LOGGER",
    "suppress_console_record",
    "console_emitter",
    "install_console_recorder",
    "remove_console_recorder",
]
