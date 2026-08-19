"""A memory failure never fails a task, and never passes silently.

Both halves are load-bearing. The first is record_task_in_memory's
existing rule (loop/engine.py:352-366) applied to a second store. The
second is what S14.2 forces: memory is mandatory, so a mandatory
subsystem that quietly does nothing is worse than one that fails.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rudra.memory.degrade import degrades, last_failure, reset_failures


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    reset_failures()
    yield
    reset_failures()


def test_a_working_call_returns_its_value_and_records_nothing() -> None:
    @degrades(default=None)
    def works() -> str:
        return "ok"

    assert works() == "ok"
    assert last_failure() is None


def test_a_raising_call_returns_the_default_instead() -> None:
    @degrades(default=[])
    def boom() -> list:
        raise RuntimeError("chroma is unhappy")

    assert boom() == []


def test_the_failure_is_recorded_so_the_run_can_say_so() -> None:
    @degrades(default=None)
    def boom() -> None:
        raise RuntimeError("embedder identity mismatch")

    boom()
    assert "embedder identity mismatch" in (last_failure() or "")


def test_the_first_failure_is_kept_not_the_last() -> None:
    """The first is the cause; the ones after it are consequences."""

    @degrades(default=None)
    def boom(msg: str) -> None:
        raise RuntimeError(msg)

    boom("first")
    boom("second")
    assert "first" in (last_failure() or "")


def test_keyboard_interrupt_is_never_swallowed() -> None:
    @degrades(default=None)
    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        interrupted()


def test_the_decorator_keeps_the_functions_name_and_docstring() -> None:
    @degrades(default=None)
    def named() -> None:
        """Doc."""

    assert named.__name__ == "named"
    assert named.__doc__ == "Doc."
