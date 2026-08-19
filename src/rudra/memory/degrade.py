"""What a memory failure turns into.

Two rules, and both are load-bearing.

1. A memory failure never fails a task. That is
   record_task_in_memory's rule (loop/engine.py:352-366), applied to a
   second store: a task that genuinely finished must not be undone by a
   bookkeeping write.

2. It never degrades silently. S14.2 makes MemPalace mandatory for every
   user, and a mandatory subsystem that quietly does nothing is worse
   than one that fails. `last_failure()` is how the run and `doctor`
   report it.

The first failure is kept rather than the last, because after a palace
fails to open every later call fails too, and the fifth message explains
nothing the first did not.

KeyboardInterrupt and SystemExit pass through: swallowing Ctrl-C in a
long-running local run is its own defect.

Pure by rule: nothing here imports from Rudra or from mempalace.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_failure: str | None = None


class MemoryUnavailable(RuntimeError):
    """The palace could not be reached or opened.

    Raised by store.py's own checks. Callers do not catch it: `degrades`
    does, which is what keeps every call site free of try/except.
    """


def last_failure() -> str | None:
    """The first failure this process saw, or None."""
    return _failure


def reset_failures() -> None:
    """Forget the recorded failure. For tests and for `rudra memory` runs."""
    global _failure
    _failure = None


def degrades(default: Any) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Return `default` instead of raising, and record why.

    `default` is passed per call site rather than inferred, because the
    right answer differs: a search degrades to `[]`, a write to `False`,
    an open to `None`. A single sentinel would make every caller test for
    it.
    """

    def decorate(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            global _failure
            try:
                return fn(*args, **kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - the whole point
                if _failure is None:
                    _failure = f"{type(exc).__name__}: {exc}"
                logger.debug("memory call %s failed", fn.__name__, exc_info=True)
                return default  # type: ignore[return-value]

        return wrapper

    return decorate


__all__ = ["MemoryUnavailable", "degrades", "last_failure", "reset_failures"]
