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


def test_a_success_clears_that_calls_own_failure_history():
    """The success at call 2 resets the failure count, so call 3 is never
    refused *as a failure*.

    It is refused as a REPEAT instead, which is OPEN-39 Phase 2 and is a
    deliberate change to this test: before 2026-08-30 the third call ran
    and the assertion here was `backend.calls == 4`. Nothing had happened
    between the success and the repeat, so re-running it could only return
    the bytes the model already held -- 41 of run10's 47 read_file calls
    were that. The refusal below names the repeat, not the old error, which
    is what says which of the two rules fired."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND, "file contents", NOT_FOUND, NOT_FOUND)

    for _ in range(4):
        result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 2
    assert "Already read" in result
    assert "already failed" not in result


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


# --------------------------------------------------------------------------
# OPEN-39 Phase 2: the same no-progress rule over SUCCESSES.
#
# run10 (83f34f50210c) made 47 read_file calls over 6 distinct files -- 41
# re-reads, 87%, of which 16 were inside a single invocation, where the bytes
# were already in the model's own transcript. The dominant shape is
# read-after-own-write: the coder writes models.py and then reads it back to
# confirm the write landed.
# --------------------------------------------------------------------------

CONTENTS = "     1\tdef create_todo():\n     2\t    ..."


def test_the_second_identical_successful_read_is_refused_without_running():
    """The measured shape. Nothing has happened in between, so the answer
    cannot have changed -- and the model already has it verbatim."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    first = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    second = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert first == CONTENTS
    assert backend.calls == 1, "the second call must not reach the backend"
    assert "Already read" in second


def test_the_threshold_for_a_success_is_one_not_two():
    """Failures allow one retry, because a model correcting itself on the
    second attempt is normal. Nothing needs correcting here, and refusing
    only the THIRD would fire exactly where runner.py:295 already halts the
    whole invocation -- too late to save anything."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    for _ in range(4):
        guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert backend.calls == 1


def test_the_dedupe_refusal_names_the_call_and_says_what_to_do():
    """A refusal that only says "no" costs the round trip it exists to save."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert "read_file" in result
    assert "/models.py" in result
    assert "nothing has changed it since" in result


def test_the_dedupe_refusal_does_not_read_as_a_tool_failure():
    """THE trap in this change, and the reason it is asserted rather than
    eyeballed. `trace/stream.py` counts a result whose first line starts
    "Error"/"Traceback"/"Errno"/"[Errno"/"BLOCKED:" as a failure, and
    runner.py halts a subagent on three consecutive failures -- so an
    "Error:"-prefixed dedupe message would turn a saving into three dead
    invocations (OPEN-16's shape)."""
    from rudra.trace.stream import looks_like_error

    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert looks_like_error(result) is False


def test_a_write_in_between_lets_the_same_read_run_again():
    """What makes this a no-progress rule and not a cache: the file the
    coder just wrote is the file it is entitled to re-read. _record already
    clears on every non-guarded call, which is where the whole correctness
    argument comes from -- no content is stored and none is served."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS, "written", "new contents")

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/models.py", content="x"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert backend.calls == 3
    assert result == "new contents"


def test_a_command_in_between_also_lets_the_read_run_again():
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    guard.wrap_tool_call(_request("execute", command="python -m pytest"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert backend.calls == 3


def test_a_different_page_of_the_same_file_is_not_a_repeat():
    """OPEN-35 again. read_file pages -- offset/limit on deepagents'
    ReadFileSchema -- and _signature carries them, so page 2 is a different
    call. Dropping that re-opens the defect where no agent could read a
    file past ~200 lines."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/big.py", offset=0), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/big.py", offset=100), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/big.py", offset=200), backend)

    assert backend.calls == 3


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("read_file", {"file_path": "/models.py"}),
        ("ls", {"path": "/"}),
        ("glob", {"pattern": "**/*.py"}),
        ("grep", {"pattern": "def create", "path": "/"}),
    ],
)
def test_every_guarded_read_tool_is_deduped(name: str, args: dict):
    """One rule over _GUARDED_TOOLS, no per-tool branch: a repeated `ls` of
    a directory nothing has touched is the same waste as a repeated read."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("ok")

    guard.wrap_tool_call(_request(name, **args), backend)
    guard.wrap_tool_call(_request(name, **args), backend)

    assert backend.calls == 1


def test_a_repeated_successful_write_is_never_deduped():
    """Writing the same content twice is wasteful and is NOT this guard's
    business: a write has an effect, and short-circuiting one would be a
    correctness bug rather than a saving. runner.py's MAX_REPEATED_CALLS is
    what stops that, and it stopped three of run10's five halts."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("written")

    for _ in range(4):
        guard.wrap_tool_call(_request("write_file", file_path="/x.py", content="y"), backend)

    assert backend.calls == 4


def test_a_repeated_successful_command_is_never_deduped():
    """The module's whole safety argument: a command can legitimately give
    a different answer the second time -- a test suite that now passes, a
    file another step has since written."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("2 passed")

    for _ in range(4):
        guard.wrap_tool_call(_request("execute", command="pytest"), backend)

    assert backend.calls == 4


def test_a_failure_after_a_success_is_still_allowed_its_retry():
    """The two rules must not contaminate each other. A read that succeeds,
    is invalidated by a write, and then fails gets the same one retry any
    other failure gets."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS, "written", NOT_FOUND, NOT_FOUND, NOT_FOUND)

    guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/x", content="y"), backend)
    for _ in range(3):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert backend.calls == 4, "two failures run, the third is refused"


class _Usage:
    """A stand-in for RunUsage: the middleware is duck-typed on it the way
    ModelRetryMiddleware is (build.py:239-243)."""

    def __init__(self):
        self.deduped: list[str] = []

    def record_dedupe(self, role: str) -> None:
        self.deduped.append(role)


def test_a_dedupe_is_counted_in_usage():
    """Without the counter the confirming run needs the ledger's own
    re-read script run by hand, which is what this item's document exists
    because of. Mirrors record_retry (OPEN-45) and record_tree (Phase 1)."""
    usage = _Usage()
    guard = RepeatGuardMiddleware(role="coder", usage=usage)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert usage.deduped == ["coder", "coder"], "one per refusal, not one per call"


def test_a_refused_failure_is_not_counted_as_a_dedupe():
    """Two counters would be one number: the saving this item claims is
    re-reads avoided, and a refused FAILING read is OPEN-10's saving, which
    was already banked in 2026-08-24."""
    usage = _Usage()
    guard = RepeatGuardMiddleware(role="coder", usage=usage)
    backend = _Backend(NOT_FOUND)

    for _ in range(4):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert usage.deduped == []


def test_the_guard_runs_without_a_usage_object():
    """Callers outside a full run build stand-ins, and half of them pass
    nothing -- the same reason build.py reads `usage` with getattr."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert "Already read" in result


async def test_the_async_path_dedupes_identically():
    """deepagents calls the async path at run time, so a rule on the sync
    path alone would never fire in production."""
    guard = RepeatGuardMiddleware()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return CONTENTS

    result = None
    for _ in range(3):
        result = await guard.awrap_tool_call(_request("read_file", file_path="/m.py"), handler)

    assert calls == 1
    assert "Already read" in result


@pytest.mark.parametrize("threshold", [1, 2, 4])
def test_the_read_threshold_is_configurable(threshold: int):
    """A dial, not a decoration: the constant is read rather than
    hardcoded, so the number in the module is the number in effect."""
    guard = RepeatGuardMiddleware(max_identical_reads=threshold)
    backend = _Backend(CONTENTS)

    for _ in range(threshold + 3):
        guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert backend.calls == threshold
