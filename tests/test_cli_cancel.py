"""SIGINT cancels the turn; a second one stops being graceful (C9.3).

130 is 128 + SIGINT, the shell convention -- a script wrapping rudra can
tell "the user stopped it" from "it failed" without parsing output.

The handler is tested by calling it, and separately by raising a real
signal. Calling it is what makes the second-press window testable without
depending on how pytest's own SIGINT handling interacts with the loop.
"""

from __future__ import annotations

import asyncio
import signal

import pytest

from rudra.cli import EXIT_CANCELLED, _cancel_on_sigint


def test_exit_cancelled_is_the_shell_convention():
    assert EXIT_CANCELLED == 130


async def test_a_real_sigint_cancels_the_task_it_was_given():
    async def forever():
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    with _cancel_on_sigint(task):
        await asyncio.sleep(0)
        signal.raise_signal(signal.SIGINT)
        with pytest.raises(asyncio.CancelledError):
            await task

    assert task.cancelled()


async def test_the_handler_is_removed_afterwards():
    """A handler left installed would cancel the NEXT REPL turn's task:
    the REPL builds a fresh agent per input (S15.4) and would inherit it."""
    loop = asyncio.get_running_loop()

    async def quick():
        return "done"

    task = asyncio.create_task(quick())
    with _cancel_on_sigint(task):
        assert await task == "done"

    # Nothing routed to SIGINT any more. remove_signal_handler returns
    # False when there was nothing to remove, which is the assertion.
    assert loop.remove_signal_handler(signal.SIGINT) is False


async def test_the_handler_survives_a_task_that_already_finished():
    """The window between the run returning and the handler coming off is
    real, and a Ctrl-C landing in it must not raise."""

    async def quick():
        return "done"

    task = asyncio.create_task(quick())
    with _cancel_on_sigint(task):
        await task
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)


async def test_a_second_press_inside_the_window_leaves_immediately(monkeypatch):
    """A cancel that itself hangs must not need a kill from another
    terminal. os._exit, not sys.exit: sys.exit raises SystemExit, which
    the event loop would catch and turn back into a hang."""
    exits: list[int] = []
    monkeypatch.setattr("os._exit", lambda code: exits.append(code))

    async def forever():
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    with _cancel_on_sigint(task) as handle:
        handle()  # first press: polite
        assert exits == []
        handle()  # second, immediately: not
        assert exits == [EXIT_CANCELLED]

    task.cancel()


async def test_a_second_press_outside_the_window_is_polite_again(monkeypatch):
    """Two unrelated interrupts minutes apart are two first presses."""
    import time

    exits: list[int] = []
    monkeypatch.setattr("os._exit", lambda code: exits.append(code))
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    async def forever():
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    with _cancel_on_sigint(task) as handle:
        handle()
        clock[0] += 60.0
        handle()

    assert exits == []
    task.cancel()


# --- the REPL half -------------------------------------------------------
#
# Note for whoever reads this next: before Step 15b the REPL had NO tests
# at all (`grep -rln "_repl_session" tests/` returned nothing). These
# drive it through CliRunner with scripted input, which is the only way in
# without a terminal.


def _repl_with(monkeypatch, inputs, agent_factory):
    """Run the REPL over a scripted input list and return the output."""
    from typer.testing import CliRunner

    from rudra import cli as cli_module
    from rudra.cli import app

    remaining = list(inputs)

    async def scripted(session, prompt_text):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    monkeypatch.setattr(cli_module, "_prompt_input", scripted)
    monkeypatch.setattr(cli_module, "create_main_agent", agent_factory)
    monkeypatch.setattr(cli_module, "print_banner", lambda: None)
    return CliRunner().invoke(app, ["--auto"])


def test_a_cancelled_turn_returns_the_repl_to_its_prompt(monkeypatch, tmp_path):
    """A cancelled TURN is not a cancelled SESSION -- getting the prompt
    back is the entire point of Ctrl-C in a REPL."""
    monkeypatch.chdir(tmp_path)
    seen: list[str] = []

    class _Agent:
        async def run(self):
            raise asyncio.CancelledError

        async def close(self):
            return None

    async def factory(**kwargs):
        seen.append(kwargs["task"])
        return _Agent()

    result = _repl_with(monkeypatch, ["do the thing", "/exit"], factory)

    assert "Cancelled" in result.output
    assert "Goodbye" in result.output, "the session must survive the cancel"
    assert seen == ["do the thing"]


def test_an_ordinary_failure_is_still_reported_as_an_error(monkeypatch, tmp_path):
    """The new CancelledError branch must not swallow real failures."""
    monkeypatch.chdir(tmp_path)

    from rudra.agent.main_agent import AgentResult

    class _Agent:
        async def run(self):
            return AgentResult(success=False, message="the model refused")

        async def close(self):
            return None

    async def factory(**kwargs):
        return _Agent()

    result = _repl_with(monkeypatch, ["do the thing", "/exit"], factory)

    assert "the model refused" in result.output
    assert "Cancelled" not in result.output


# --- the single-shot half ------------------------------------------------


def _single_shot_with(monkeypatch, agent_factory):
    """Run one non-interactive `rudra "..."` and return the result."""
    from typer.testing import CliRunner

    from rudra import cli as cli_module
    from rudra.cli import app

    monkeypatch.setattr(cli_module, "create_main_agent", agent_factory)
    return CliRunner().invoke(app, ["--auto", "build it"])


def test_a_keyboard_interrupt_is_a_cancel_not_a_traceback(monkeypatch, tmp_path):
    """OPEN-2(b). A platform with no `add_signal_handler` still raises.

    `_cancel_on_sigint` falls back to the default SIGINT handler when
    `loop.add_signal_handler` raises NotImplementedError (Windows), and that
    handler raises KeyboardInterrupt on the MAIN thread. Since the approval
    prompt moved off that thread, the exception now surfaces at the `await`
    in `_settle_plan` rather than inside `Prompt.ask` -- so this path, not
    `plan_view.py`'s own `except`, is what covers Ctrl-C there.

    The exit code was already right and is not the point: typer maps
    KeyboardInterrupt to Exit(130) itself (`typer/core.py:202`). What was
    missing is that the user was told NOTHING -- no cancel line, no way
    back -- while the CancelledError sibling below prints both. The two
    mean the same thing to the user and must read the same.
    """
    monkeypatch.chdir(tmp_path)

    class _Agent:
        async def run(self):
            raise KeyboardInterrupt

        async def close(self):
            return None

    async def factory(**kwargs):
        return _Agent()

    result = _single_shot_with(monkeypatch, factory)

    assert result.exit_code == EXIT_CANCELLED
    assert "Cancelled" in result.output
    assert "--continue" in result.output, "the user needs the way back"


def test_a_cancelled_run_still_exits_130(monkeypatch, tmp_path):
    """The sibling path, so the KeyboardInterrupt branch cannot be the only
    one that works -- these two must stay identical in what they print."""
    monkeypatch.chdir(tmp_path)

    class _Agent:
        async def run(self):
            raise asyncio.CancelledError

        async def close(self):
            return None

    async def factory(**kwargs):
        return _Agent()

    result = _single_shot_with(monkeypatch, factory)

    assert result.exit_code == EXIT_CANCELLED
    assert "Cancelled" in result.output
