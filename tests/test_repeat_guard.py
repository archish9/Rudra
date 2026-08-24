"""OPEN-10: an agent must not re-run a read that already failed identically.

Measured 2026-08-24 from a real run's debug log: `read_file` called four
times with byte-identical arguments, failing identically each time, before
the model tried anything else.

`loop/bounds.py` already stops a *task* on two identical failure signatures
(C6.5a). This is the same rule one level down, inside a single agent turn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rudra.middleware.repeat_guard import RepeatGuardMiddleware


def _request(name: str, **args):
    return SimpleNamespace(tool_call={"name": name, "args": args})


class _Backend:
    """A handler that records every call it actually receives."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return self.results[min(self.calls - 1, len(self.results) - 1)]


NOT_FOUND = "Error: File '/. rudra/AGENTS.md' not found"


def test_the_first_two_identical_failures_still_run():
    """One retry is normal. A guard that fired on the first failure would
    be indistinguishable from the tool being broken."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        assert guard.wrap_tool_call(_request("read_file", file_path="/x"), backend) == NOT_FOUND

    assert backend.calls == 2


def test_the_third_identical_failure_is_refused_without_running():
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 2, "the third call must not reach the backend"
    assert "already failed 2 times" in result


def test_the_refusal_repeats_the_original_error_and_says_what_to_do():
    """A refusal that only says "no" costs a round trip and buys nothing.
    This one has to carry the model out of the loop."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/. rudra/AGENTS.md"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/. rudra/AGENTS.md"), backend)

    assert "not found" in result, "the original error must survive into the refusal"
    assert "ls" in result
    assert "same arguments" in result


def test_a_different_argument_is_a_different_call():
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/y"), backend)

    assert backend.calls == 3


def test_argument_order_does_not_create_a_new_signature():
    """The key is sorted, because dict order follows whatever the model
    emitted and a reordered repeat is still a repeat."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    guard.wrap_tool_call(_request("grep", pattern="x", path="/a"), backend)
    guard.wrap_tool_call(_request("grep", path="/a", pattern="x"), backend)
    guard.wrap_tool_call(_request("grep", pattern="x", path="/a"), backend)

    assert backend.calls == 2


def test_a_write_in_between_clears_the_block():
    """The case that makes this a no-progress rule rather than a quota, and
    the reason the first draft of this middleware was a bug: a read that
    fails twice, is fixed by a write, and is retried must run. Without the
    clear it stays blocked for the whole turn -- and the block can never
    lift, because the read that would clear it never reaches the backend."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND, NOT_FOUND, "written", "file contents")

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/x", content="hi"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 4
    assert result == "file contents"


def test_a_failed_command_in_between_also_clears_the_block():
    """Conservative on purpose: a command that exits non-zero can still
    have written something, so it counts as the world having changed."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND, NOT_FOUND, "Error: exit 1", NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    guard.wrap_tool_call(_request("execute", command="make"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 4


def test_the_observed_loop_had_nothing_in_between_and_is_still_caught():
    """The measured trace: four identical read_file calls back to back,
    with no other tool call between them."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(4):
        guard.wrap_tool_call(_request("read_file", file_path="/. rudra/AGENTS.md"), backend)

    assert backend.calls == 2, "calls 3 and 4 never reach the backend"


def test_a_success_clears_that_calls_own_history():
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND, "file contents", NOT_FOUND, NOT_FOUND)

    for _ in range(4):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 4, "the success at call 2 resets the count"


def test_execute_is_never_short_circuited():
    """The safety argument. A command can legitimately succeed on retry --
    a flaky test, a network blip, a file another step has since written.
    Guarding it would turn a token cost into a correctness bug."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("Error: exit 1")

    for _ in range(6):
        guard.wrap_tool_call(_request("execute", command="pytest"), backend)

    assert backend.calls == 6


def test_writing_tools_are_never_short_circuited():
    guard = RepeatGuardMiddleware()
    backend = _Backend("Error: nope")

    for _ in range(6):
        guard.wrap_tool_call(_request("write_file", file_path="/x", content="y"), backend)

    assert backend.calls == 6


def test_a_successful_read_is_returned_untouched():
    guard = RepeatGuardMiddleware()
    backend = _Backend("hello")

    assert guard.wrap_tool_call(_request("read_file", file_path="/x"), backend) == "hello"


def test_an_error_reported_on_a_message_object_is_still_an_error():
    """deepagents returns tool failures as ToolMessage content, not by
    raising, so the guard has to look through `.content`."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(SimpleNamespace(content=NOT_FOUND))

    for _ in range(3):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 2


def test_unserialisable_arguments_do_not_raise():
    """`default=str`: a guard that raises inside wrap_tool_call would break
    the call it exists to protect."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("ok")

    guard.wrap_tool_call(_request("read_file", file_path=object()), backend)

    assert backend.calls == 1


async def test_the_async_path_guards_identically():
    """Both paths, because deepagents calls the async one at run time and a
    guard on only the sync path would never fire in production."""
    guard = RepeatGuardMiddleware()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return NOT_FOUND

    for _ in range(4):
        result = await guard.awrap_tool_call(_request("read_file", file_path="/x"), handler)

    assert calls == 2
    assert "already failed" in result


@pytest.mark.parametrize("threshold", [1, 3, 5])
def test_the_threshold_is_configurable(threshold: int):
    guard = RepeatGuardMiddleware(max_identical_failures=threshold)
    backend = _Backend(NOT_FOUND)

    for _ in range(threshold + 3):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == threshold
