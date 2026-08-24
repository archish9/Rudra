"""RepeatGuardMiddleware — stop an agent re-running a call that already failed.

`loop/bounds.py` already does this one level up: two identical failure
signatures stop a task (C6.5a). Nothing did it *inside* an agent turn, so a
model could call one tool with byte-identical arguments until the recursion
limit caught it. Measured 2026-08-24: `read_file` with
`{'file_path': '/. rudra/AGENTS.md'}` four times in a row, failing
identically, before the model tried anything else (TODO.md OPEN-10).

The principle is S9c.1's -- the model decides what work exists, Python
decides when to stop.

**Read tools only, and that is the whole safety argument.** A missing file
does not appear because you asked a third time, so short-circuiting a
repeated `read_file` can only save a round trip. A *command* is different:
`execute` can legitimately succeed on retry after a flaky test, a network
blip or a file another step has since written. Guarding that would turn a
token cost into a correctness bug, so `execute` -- and every writing tool --
is deliberately outside `_GUARDED_TOOLS`.

The count is per (tool, arguments) and resets the moment that exact call
succeeds, so a read that fails while a file is being written and then works
is never held against the model.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

# Deterministic reads. A repeat of one of these after a failure cannot
# succeed, which is what makes short-circuiting safe. `execute` is
# excluded on purpose -- see the module docstring.
_GUARDED_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})

MAX_IDENTICAL_FAILURES = 2
"""Failures of one exact call before the next is refused rather than run.

Two, not one: the first retry is worth allowing -- a model correcting
itself on the second attempt is normal, and a guard that fires on the
first failure would be indistinguishable from the tool simply being
broken. The third identical call is the one that has stopped being a
retry and started being a loop.
"""


def _signature(name: str, args: dict[str, Any]) -> str:
    """A stable key for one exact call.

    `sort_keys` because dict ordering follows whatever the model emitted,
    and `default=str` because an argument that is not JSON-serialisable
    must degrade to a usable key rather than raise inside a tool call.
    """
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def _is_error(result: Any) -> bool:
    """Did this tool call fail?

    deepagents reports filesystem failures as ordinary ToolMessage content
    beginning with "Error:" rather than by raising
    (backends/filesystem.py:447), so the string is the signal available
    here. Checked on `.content` when present so a ToolMessage and a bare
    string are treated alike.
    """
    content = getattr(result, "content", result)
    return isinstance(content, str) and content.lstrip().startswith("Error")


class RepeatGuardMiddleware(AgentMiddleware):
    """Refuse a read that has already failed twice with identical arguments.

    State lives on the instance, and one instance is built per agent, so
    the count is per agent run -- the same lifetime `build.py` gives every
    other middleware it constructs.
    """

    def __init__(self, max_identical_failures: int = MAX_IDENTICAL_FAILURES) -> None:
        super().__init__()
        self.max_identical_failures = max_identical_failures
        self._failures: dict[str, int] = {}
        self._last_error: dict[str, str] = {}

    def _refusal(self, signature: str, name: str) -> str:
        seen = self._failures[signature]
        return (
            f"Error: {name} has already failed {seen} times with these exact "
            f"arguments, and was not run again. The error each time was: "
            f"{self._last_error.get(signature, 'unknown')}\n"
            f"Calling it again with the same arguments will not work. Change "
            f"the arguments, use ls to find the correct path, or continue "
            f"without this file."
        )

    def _blocked(self, request) -> str | None:
        """The refusal to return instead of running this call, or None."""
        name = request.tool_call.get("name")
        if name not in _GUARDED_TOOLS:
            return None
        signature = _signature(name, request.tool_call.get("args", {}))
        if self._failures.get(signature, 0) < self.max_identical_failures:
            return None
        return self._refusal(signature, name)

    def _record(self, request, result: Any) -> None:
        name = request.tool_call.get("name")
        if name not in _GUARDED_TOOLS:
            # Anything else -- a write, an edit, a command -- may have
            # changed what the guarded reads would see, so every count is
            # dropped. That is what makes this a NO-PROGRESS rule rather
            # than a quota: "you tried this N times and nothing happened in
            # between", which is exactly loop/bounds.py's semantics one
            # level up. Cleared even when the call failed, because a failed
            # command can still have written something.
            #
            # Without this the guard is a correctness bug of its own: a read
            # that fails twice, is fixed by a write, and is retried would
            # stay blocked for the rest of the turn, and the block can never
            # lift because the read that would clear it never runs.
            self._failures.clear()
            self._last_error.clear()
            return
        signature = _signature(name, request.tool_call.get("args", {}))
        if _is_error(result):
            self._failures[signature] = self._failures.get(signature, 0) + 1
            self._last_error[signature] = str(getattr(result, "content", result))[:200]
        else:
            self._failures.pop(signature, None)
            self._last_error.pop(signature, None)

    def wrap_tool_call(self, request, handler):
        refusal = self._blocked(request)
        if refusal is not None:
            return refusal
        result = handler(request)
        self._record(request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        refusal = self._blocked(request)
        if refusal is not None:
            return refusal
        result = await handler(request)
        self._record(request, result)
        return result


__all__ = ["MAX_IDENTICAL_FAILURES", "RepeatGuardMiddleware"]
