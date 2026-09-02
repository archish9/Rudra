"""What Rudra says about itself reaches the run log (OPEN-76).

163 `console.print` call sites against three loggers: the plan, every
panel, every error message and `run()`'s own crash traceback were printed
to a terminal and recorded nowhere. The session that filed this had a
complete log folder and still could not tell what the run had said.

The tee sits under `rich.Console.file`, which is a settable property, so
none of the 163 call sites change. It writes to the debug log ONLY -- the
transcript stays the readable record of what the MODEL did.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from rudra.trace.console_log import install_console_recorder, remove_console_recorder
from rudra.trace.debug import LOGGER_NAME, configure_debug_logging


@pytest.fixture
def detached() -> Iterator[list[logging.Handler]]:
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


def _console_lines(path: Path) -> list[str]:
    return [
        json.loads(line)["payload"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "console"
    ]


def test_a_printed_line_reaches_the_run_log(tmp_path: Path, detached):
    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    console = Console(force_terminal=False, width=80)

    install_console_recorder(console)
    console.print("Plan not executed (cancel): 15 task(s) declared")
    remove_console_recorder(console)

    assert "Plan not executed (cancel): 15 task(s) declared" in _console_lines(path)


def test_markup_is_rendered_and_the_escapes_are_stripped(tmp_path: Path, detached):
    """The log holds what a human saw, not what Rich was handed. ANSI in a
    JSON payload is unreadable in exactly the situation it is needed."""
    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    console = Console(force_terminal=True, width=80)

    install_console_recorder(console)
    console.print("[bold red]Agent crashed[/bold red]")
    remove_console_recorder(console)

    assert "Agent crashed" in _console_lines(path)
    assert not any("\x1b" in line for line in _console_lines(path))


def test_the_user_still_sees_it(tmp_path: Path, detached):
    """A tee, not a redirect. Recording must never cost the terminal."""
    import io

    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    sink = io.StringIO()
    console = Console(file=sink, force_terminal=False, width=80)

    install_console_recorder(console)
    console.print("hello")
    remove_console_recorder(console)

    assert "hello" in sink.getvalue()


def test_a_credential_in_printed_text_is_redacted(tmp_path: Path, detached):
    """A1.95's rule -- redaction happens where the record is built -- and
    this is a new place one is built. `_provider_failure_message` prints
    `str(error)`, and a provider's exception can carry its own URL."""
    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    console = Console(force_terminal=False, width=200)

    install_console_recorder(console)
    console.print("connect failed: https://api.example.com/v1?api_key=sk-abcdef1234567890")
    remove_console_recorder(console)

    assert not any("sk-abcdef1234567890" in line for line in _console_lines(path))


def test_removing_restores_the_original_file(tmp_path: Path, detached):
    import io

    sink = io.StringIO()
    console = Console(file=sink, force_terminal=False, width=80)
    install_console_recorder(console)
    remove_console_recorder(console)

    assert console.file is sink


def test_removing_one_that_was_never_installed_is_not_an_error():
    remove_console_recorder(Console(quiet=True))


def test_the_recorder_keeps_the_terminals_answers(tmp_path: Path):
    """Rich asks the file whether it is a terminal and for its descriptor,
    to pick colour and width. A wrapper that answers differently silently
    reformats every panel in the run."""
    import sys

    from rudra.trace.console_log import _ConsoleTee

    tee = _ConsoleTee(sys.stdout, lambda _line: None)
    assert tee.isatty() == sys.stdout.isatty()
    assert tee.fileno() == sys.stdout.fileno()


def test_trace_renderings_are_printed_but_not_recorded(tmp_path: Path, detached):
    """The event is already the complete record. Its console rendering is
    a wrapped, truncated copy of one -- storing both costs size and adds
    nothing."""
    import io

    from rudra.trace import TraceEvent, TraceKind, TraceLevel
    from rudra.trace.sink import console_consumer

    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    sink = io.StringIO()
    console = Console(file=sink, force_terminal=False, width=200)

    install_console_recorder(console)
    console_consumer(console, TraceLevel.VERBOSE)(
        TraceEvent(kind=TraceKind.AI_TEXT, role="planner", payload="thinking out loud")
    )
    console.print("Plan not executed (cancel)")
    remove_console_recorder(console)

    assert "thinking out loud" in sink.getvalue()  # the user still saw it
    assert _console_lines(path) == ["Plan not executed (cancel)"]


def test_every_line_in_the_run_log_carries_a_wall_clock(tmp_path: Path, detached):
    """OPEN-77's other half. A console line is the record whose entire
    value is "what did the user see, and when"; an ordinary log record
    rides `record.created`, so both are on the same clock rather than
    nearly on it."""
    import time

    path = tmp_path / "debug-aaa.jsonl"
    detached.append(configure_debug_logging(path, enabled=True))
    console = Console(force_terminal=False, width=80)

    before = time.time()
    install_console_recorder(console)
    console.print("something happened")
    logging.getLogger("rudra.example").debug("and so did this")
    remove_console_recorder(console)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {row["kind"] for row in rows} == {"console", "log"}
    for row in rows:
        assert before <= row["ts"] <= time.time()
