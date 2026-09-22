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


def _request(name: str, call_id: str | None = "call-1", **args):
    """A ToolCallRequest stand-in.

    `id` is part of a real ToolCall and is what the refusal ToolMessage
    answers (OPEN-57); `call_id=None` builds the shape every stand-in in
    this file had before it, which must still work.
    """
    tool_call = {"name": name, "args": args}
    if call_id is not None:
        tool_call["id"] = call_id
    return SimpleNamespace(tool_call=tool_call)


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
    assert "already failed 2 times" in _text(result)


def test_the_refusal_repeats_the_original_error_and_says_what_to_do():
    """A refusal that only says "no" costs a round trip and buys nothing.
    This one has to carry the model out of the loop.

    Two assertions retired 2026-09-04 (OPEN-95): this used to pin `"same
    arguments"` and `"ls"`, which were the item's two defects written down
    as requirements -- the arguments were not the same, and answering an
    `ls` refusal with "use ls" is advice that cannot be taken. The section
    at the end of this file owns the replacement wording. What survives
    here is the half that was always right: the original error reaches the
    model, and the message says what to do next.
    """
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/. rudra/AGENTS.md"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/. rudra/AGENTS.md"), backend)

    assert "not found" in _text(result), "the original error must survive into the refusal"
    assert "carry on without it" in _text(result)


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
    assert "Already read" in _text(result)
    assert "already failed" not in _text(result)


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
    assert "already failed" in _text(result)


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
    assert "Already read" in _text(second)


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

    assert "read_file" in _text(result)
    assert "/models.py" in _text(result)
    assert "nothing has changed it since" in _text(result)


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

    assert looks_like_error(_text(result)) is False


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


def test_a_repeated_write_is_never_deduped_by_the_read_rule():
    """A write is outside `_GUARDED_TOOLS` and stays outside it.

    This test used to read "a repeated successful write is never deduped",
    on the argument that "a write has an effect, and short-circuiting one
    would be a correctness bug". OPEN-60 measured that argument and found
    it true of a write that CHANGES the file and false of one that does
    not -- 30 of 127 writes over five runs put back bytes already on disk.
    So the boundary moved, and what is asserted here is the part that did
    not move: repeated writes of DIFFERENT content all run, and they run
    through the write rule rather than the read one. `_GUARDED_WRITES` is a
    separate set for exactly that reason -- sharing `_GUARDED_TOOLS` would
    key a write on `json.dumps(args)`, which is the file body.
    """
    guard = RepeatGuardMiddleware()
    backend = _Backend("written")

    for body in ("y", "yy", "yyy", "yyyy"):
        guard.wrap_tool_call(_request("write_file", file_path="/x.py", content=body), backend)

    assert backend.calls == 4
    assert guard._answered == {}, "a write is never counted as an answered read"


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
        self.skipped: list[tuple[str, int]] = []

    def record_dedupe(self, role: str) -> None:
        self.deduped.append(role)

    def record_write_skipped(self, role: str, chars: int) -> None:
        self.skipped.append((role, chars))


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

    assert "Already read" in _text(result)


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
    assert "Already read" in _text(result)


@pytest.mark.parametrize("threshold", [1, 2, 4])
def test_the_read_threshold_is_configurable(threshold: int):
    """A dial, not a decoration: the constant is read rather than
    hardcoded, so the number in the module is the number in effect."""
    guard = RepeatGuardMiddleware(max_identical_reads=threshold)
    backend = _Backend(CONTENTS)

    for _ in range(threshold + 3):
        guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert backend.calls == threshold


# --------------------------------------------------------------------------
# OPEN-57: the refusal must answer the call it refused, and say so as RUDRA.
#
# Both refusals returned a bare `str`. langgraph puts a wrapper's return
# value straight into `{messages: [...]}` (prebuilt/tool_node.py:881-886) and
# `add_messages` coerces a bare string to a HumanMessage -- so run11's debug
# log records Rudra's own dedupe message as `"kind": "user"`, the AIMessage's
# tool_call is left with nothing answering it, and every script that pairs a
# call with its result loses the pair.
# --------------------------------------------------------------------------


def _text(result):
    """The refusal text, whether it arrives as a message or a bare string.

    Reads through `.content` the way `_is_error` does, so the assertions
    above this line say what they always said.
    """
    return str(getattr(result, "content", result))


def test_the_dedupe_refusal_is_a_tool_message_answering_the_call():
    """The call must have a result, and the result must be the tool's.

    A bare string became a HumanMessage, which is Rudra's words wearing the
    user's name -- and it left the AIMessage's tool_call unanswered, which
    is a transcript strict providers reject.
    """
    from langchain_core.messages import ToolMessage

    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert result.name == "read_file"
    assert "Already read" in result.content


def test_the_failure_refusal_is_a_tool_message_answering_the_call():
    """OPEN-10's refusal has had the same shape since 2026-08-24, and
    nothing counted it, so nobody noticed it arriving as the user saying
    "Error:"."""
    from langchain_core.messages import ToolMessage

    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert result.name == "read_file"
    assert "already failed 2 times" in result.content


def test_a_call_with_no_id_still_gets_a_refusal():
    """Every stand-in in this file predates the id, and a guard that raises
    inside wrap_tool_call breaks the call it exists to protect."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", call_id=None, file_path="/m.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", call_id=None, file_path="/m.py"), backend)

    assert result.tool_call_id == ""
    assert "Already read" in result.content


def test_the_dedupe_refusal_is_not_an_error_to_the_trace():
    """The message-level answer to what `looks_like_error` asserts on the
    text: `runner.py:300` reads ToolMessages, and a dedupe that counted as
    a failure would turn a saving into three dead invocations (OPEN-16)."""
    from rudra.trace.stream import message_is_error

    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert message_is_error(result) is False


def test_the_failure_refusal_is_not_an_error_to_the_trace():
    """OPEN-94 reverses OPEN-57's decided half, and this is that decision.

    OPEN-57 argued that a refused-because-it-failed-before call IS a
    failure, so the counter should see it. Two things are true and that
    reasoning kept only the first: for the MODEL the call did not succeed,
    and for `runner.py`'s counter NO TOOL RAN. That counter exists to spot
    an agent flailing against a broken environment -- a short-circuit is
    the opposite signal, and counting it makes the counter fire faster the
    better the guard works. `_failures` never expires within a turn, so
    once a signature has two real failures every later call resolving to it
    is a free `Error:` line: run 2cde3406f7d6's coder reached three in
    11.67 s and died having written nothing.

    OPEN-94 left the refusal's TEXT byte-identical so that OPEN-95 could
    own it, and OPEN-95 has since rewritten everything after the first
    word. The first word stays: the model must still read "that did not
    work", and it is free to, precisely because the classification no
    longer comes from it.
    """
    from rudra.trace.stream import message_is_error

    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert _text(result).startswith("Error:"), "the leading word is deliberately kept (OPEN-95)"
    assert message_is_error(result) is False


def test_every_refusal_this_guard_can_emit_is_marked_and_uncounted():
    """The property, not three examples of it (OPEN-94 §8.2).

    Every refusal is produced through one seam -- `_as_message` -- so a
    fourth rule added later is covered by construction. This drives all
    three that exist through the middleware rather than calling the private
    builders, so a rule that stopped going through that seam fails here.
    """
    from rudra.trace.stream import is_rudra_refusal, message_is_error

    refusals = []

    failing = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)
    for _ in range(3):
        result = failing.wrap_tool_call(_request("read_file", file_path="/x"), backend)
    refusals.append(result)

    reading = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)
    for _ in range(2):
        result = reading.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    refusals.append(result)

    writing = RepeatGuardMiddleware()
    backend = _Backend(WROTE)
    for _ in range(2):
        result = writing.wrap_tool_call(
            _request("write_file", file_path="/app.py", content=BODY), backend
        )
    refusals.append(result)

    assert len(refusals) == 3
    for refusal in refusals:
        assert is_rudra_refusal(refusal) is True, _text(refusal)
        assert message_is_error(refusal) is False, _text(refusal)


def test_a_result_that_really_came_back_from_a_tool_is_never_marked():
    """The marker means "Rudra invented this". A real failure must keep
    counting or the runaway bound stops working (OPEN-94 §4.3)."""
    from langchain_core.messages import ToolMessage

    from rudra.trace.stream import is_rudra_refusal, message_is_error

    guard = RepeatGuardMiddleware()
    backend = _Backend(ToolMessage(content="BLOCKED: denied", tool_call_id="1", name="read_file"))

    result = guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert is_rudra_refusal(result) is False
    assert message_is_error(result) is True


def test_a_call_that_runs_is_returned_exactly_as_the_backend_gave_it():
    """The guard wraps only what it invents. A real result is already a
    ToolMessage and re-wrapping one would lose its status and its id."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    assert guard.wrap_tool_call(_request("read_file", file_path="/m.py"), backend) == CONTENTS


async def test_the_async_refusal_is_a_tool_message_too():
    """deepagents calls the async path at run time, so a fix on the sync
    path alone would never reach production."""
    from langchain_core.messages import ToolMessage

    guard = RepeatGuardMiddleware()

    async def handler(request):
        return CONTENTS

    await guard.awrap_tool_call(_request("read_file", file_path="/m.py"), handler)
    result = await guard.awrap_tool_call(_request("read_file", file_path="/m.py"), handler)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"


class _Sink:
    """A TraceSink stand-in, duck-typed on `notice` alone.

    Same shape as test_model_retry_middleware.py's: a fake carrying more
    would let this file pass against a guard reaching for something else.
    """

    def __init__(self) -> None:
        self.notices: list[dict] = []

    def notice(self, payload, *, role, name="", namespace=(), index=0, at=0.0):
        self.notices.append({"payload": payload, "role": role, "name": name})
        return None


def test_a_dedupe_emits_one_notice():
    """OPEN-44's rule, arriving a third time: anything that changes what a
    run does emits a trace event when it fires. Phase 2 shipped a counter
    and no event."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)

    assert len(sink.notices) == 1
    assert sink.notices[0]["role"] == "coder"
    assert sink.notices[0]["name"] == "repeat-guard"
    assert "read_file" in sink.notices[0]["payload"]
    assert "/models.py" in sink.notices[0]["payload"]


def test_a_failure_refusal_emits_one_notice():
    """One name for both rules, because a reader looking up
    `repeat-guard` must find the whole middleware there. The payload is
    what says which rule fired."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="planner", trace=sink)
    backend = _Backend(NOT_FOUND)

    for _ in range(3):
        guard.wrap_tool_call(_request("read_file", file_path="/x"), backend)

    assert len(sink.notices) == 1
    assert sink.notices[0]["role"] == "planner"
    assert sink.notices[0]["name"] == "repeat-guard"
    assert "read_file" in sink.notices[0]["payload"]


def test_the_two_rules_are_distinguishable_in_the_trace():
    """A reader must not have to re-derive which rule fired from the count
    of prior calls."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink)

    guard.wrap_tool_call(_request("read_file", file_path="/ok.py"), _Backend(CONTENTS))
    guard.wrap_tool_call(_request("read_file", file_path="/ok.py"), _Backend(CONTENTS))
    deduped = sink.notices[-1]["payload"]

    missing = _Backend(NOT_FOUND)
    for _ in range(3):
        guard.wrap_tool_call(_request("read_file", file_path="/gone.py"), missing)
    refused = sink.notices[-1]["payload"]

    assert deduped != refused
    assert "/ok.py" in deduped
    assert "/gone.py" in refused


def test_a_call_that_runs_emits_nothing():
    """The regression that matters most: a healthy turn's trace is exactly
    what it was before OPEN-57."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/a.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/a.py", content="x"), backend)
    guard.wrap_tool_call(_request("execute", command="pytest"), backend)

    assert sink.notices == []


def test_the_guard_runs_without_a_trace():
    """Optional for the reason `usage` is: the machinery must stay
    constructible without a run, and every test above builds it that way."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/m.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/m.py"), backend)

    assert "Already read" in result.content


def test_a_broken_sink_does_not_break_the_call():
    """Observability never ends a run -- TraceSink.emit, write_usage_log and
    ModelRetryMiddleware._report all swallow for the same reason."""

    class _Broken:
        def notice(self, *args, **kwargs):
            raise RuntimeError("sink is on fire")

    guard = RepeatGuardMiddleware(role="coder", trace=_Broken())
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/m.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/m.py"), backend)

    assert "Already read" in result.content


async def test_the_async_path_emits_a_notice_too():
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="tester", trace=sink)

    async def handler(request):
        return CONTENTS

    await guard.awrap_tool_call(_request("grep", pattern="def x", path="/"), handler)
    await guard.awrap_tool_call(_request("grep", pattern="def x", path="/"), handler)

    assert len(sink.notices) == 1
    assert sink.notices[0]["role"] == "tester"


# --- the whole path, with nothing faked, which is the RUN #6 row -----------


def test_a_refusal_reaches_the_debug_log_and_the_trace_as_rudra(tmp_path):
    """A real TraceSink, a real debug recorder, and the real classifier.

    Every test above uses a `_Sink` stand-in, so all of them would stay
    green against a notice that never survived redaction, the level filter
    or JSON serialisation -- and against a ToolMessage that `stream.py`
    still read as something the user said. This is OPEN-57's checklist row
    made deterministic: after it, `grep '"kind": "notice"'` on
    `debug-<id>.jsonl` answers "did the guard fire", and no event in a run
    carries `"kind": "user"` for a line Rudra wrote.
    """
    import json
    import logging

    from rudra.trace.debug import LOGGER_NAME, configure_debug_logging, debug_consumer
    from rudra.trace.events import TraceKind
    from rudra.trace.render import TraceLevel
    from rudra.trace.sink import TraceSink
    from rudra.trace.stream import StreamState, _events_for

    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers), logger.level, logger.propagate
    debug_path = tmp_path / "debug-test.jsonl"
    handler = configure_debug_logging(debug_path, enabled=True)
    try:
        # NORMAL, not VERBOSE: a NOTICE renders at VERBOSE but must reach
        # the debug log at every level -- that is what makes it the
        # COMPLETE record (OPEN-7). At VERBOSE this test would pass even
        # if that broke.
        sink = TraceSink(level=TraceLevel.NORMAL)
        sink.add_recorder(debug_consumer())

        guard = RepeatGuardMiddleware(role="coder", trace=sink)
        backend = _Backend(CONTENTS)
        guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
        refusal = guard.wrap_tool_call(_request("read_file", file_path="/models.py"), backend)
        handler.flush()

        lines = [
            json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines() if line
        ]
        notices = [line for line in lines if line.get("kind") == "notice"]
        assert len(notices) == 1
        assert notices[0]["name"] == "repeat-guard"
        assert notices[0]["role"] == "coder"
        assert "/models.py" in notices[0]["payload"]

        # And the refusal itself, through the classifier that mislabelled
        # it: a result for the call that asked for it, attributed to the
        # tool and not to the human.
        events = _events_for(refusal, state=StreamState(role="coder"), namespace=(), index=1)
        assert [event.kind for event in events] == [TraceKind.TOOL_RESULT]
        assert events[0].name == "read_file"

        # And the shape this replaced, kept as the statement of the defect:
        # the same text as a bare string reaches the message list as a
        # HumanMessage (langgraph coerces it), and the only branch that fits
        # one is USER. The fix is the message TYPE, not the text -- which is
        # why the text above is asserted unchanged.
        from langchain_core.messages import HumanMessage

        stale = _events_for(
            HumanMessage(content=refusal.content),
            state=StreamState(role="coder"),
            namespace=(),
            index=1,
        )
        assert [event.kind for event in stale] == [TraceKind.USER]
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.handlers, logger.level, logger.propagate = before


# --- OPEN-60 Half A: a write of bytes already on disk -----------------------
#
# The read rules refuse a call whose ANSWER is already in the transcript.
# This one refuses a call whose POST-CONDITION is already satisfied, which
# is the strongest of the three cases in the module docstring's table --
# nothing is cached and nothing is served from a copy.
#
# Measured over five runs before it was written: 30 of 127 writes were
# byte-identical rewrites, ~23,320 OUTPUT tokens. Under the strict
# invalidation rule this implements -- any other tool call drops the belief
# -- 20 of those 127 are refusable, ~17,697 tokens.

WROTE = "Updated file '/app.py'"
BODY = "def main():\n    return 1\n"


def test_a_second_identical_write_to_one_path_is_refused():
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 1, "the second write must not reach the backend"
    assert "Already written" in _text(result)


def test_the_write_refusal_is_not_classified_as_an_error():
    """OPEN-16's shape. `runner.py` halts a subagent after three
    consecutive tool failures, so an "Error:"-prefixed refusal would
    convert this saving into three dead invocations."""
    from rudra.trace.stream import looks_like_error

    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert "Already written" in _text(result), "the refusal must exist to be classified"
    assert looks_like_error(_text(result)) is False


def test_the_write_refusal_answers_the_call_it_refused():
    """OPEN-57: a bare string becomes a HumanMessage, so Rudra's own words
    arrive wearing the user's name and the tool_call is left unanswered."""
    from langchain_core.messages import ToolMessage

    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(
        _request("write_file", "call-7", file_path="/app.py", content=BODY), backend
    )
    result = guard.wrap_tool_call(
        _request("write_file", "call-8", file_path="/app.py", content=BODY), backend
    )

    assert isinstance(result, ToolMessage)
    assert result.name == "write_file"
    assert result.tool_call_id == "call-8"


def test_different_content_to_the_same_path_is_never_refused():
    """The correctness half of this item, at the middleware level: a coder
    building a file up must not be stopped."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content="a"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content="ab"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content="abc"), backend)

    assert backend.calls == 3


def test_the_same_content_to_a_different_path_is_never_refused():
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/other.py", content=BODY), backend)

    assert backend.calls == 2


def test_a_command_between_two_writes_restores_the_second():
    """`execute` can do anything to a file, so the belief is dropped rather
    than reasoned about. This is the half of the invalidation rule that is
    about safety."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("execute", command="rm /app.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3, "a command between the two writes must restore the second"


def test_a_delete_between_two_writes_restores_the_second():
    """A file that is gone has to be written again -- that is real work.

    Corrected 2026-08-31 (OPEN-62 §5): this docstring used to cite run9 as
    the case, saying it wrote `tests/test_app.py`, DELETED it and wrote the
    same 3,818 characters back. run9's log disagrees -- the deletes named
    `app/tests/test_app.py` and `app/tests/__pycache__`, and the file
    rewritten was never deleted. The rule this test pins is right; the
    example was not, and 27 of the 28 were refusable rather than 26."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("delete", file_path="/app.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_an_edit_between_two_writes_restores_the_second():
    """`edit_file` changes the file, and is outside `_GUARDED_WRITES` for a
    different reason (see the module docstring) -- so it invalidates like
    any other non-read."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(
        _request("edit_file", file_path="/app.py", old_string="a", new_string="b"), backend
    )
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_read_between_two_writes_does_not_restore_the_second():
    """A READ CANNOT CHANGE A FILE, so the belief survives it.

    This is the whole difference between the two directions of the rule,
    and getting it backwards was measured. `_record` drops read-belief on a
    write because a write is how a read's answer changes; the converse does
    not hold. Dropping write-belief on a read costs 6 of 28 refusals across
    five runs -- ~4,059 output tokens -- and buys nothing, because the only
    thing it would protect against is a process OUTSIDE Rudra editing the
    file mid-turn, which `loop/engine.py::attempt_snapshot` already assumes
    away on the same grounds.

    Measured after the fix shipped strict: every single no-op rewrite that
    still got through was let through by a read -- `read_file` 6, `ls` 1,
    `glob` 1 -- and not one by a call that can change a file.
    """
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/app.py"), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2, "the read must not have restored the redundant write"
    assert "Already written" in _text(result)


def test_a_listing_between_two_writes_does_not_restore_the_second():
    """`ls` and `glob` are reads too, and both appear in the measured
    residue. One rule over `_GUARDED_TOOLS`, no per-tool branch."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("ls", path="/"), backend)
    guard.wrap_tool_call(_request("glob", pattern="**/*.py"), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 3
    assert "Already written" in _text(result)


def test_writing_a_second_file_does_not_forget_the_first():
    """Files are independent, and `_written` is a dict for that reason.

    Measured: an implementation that REPLACED the dict instead of adding to
    it -- believing exactly one path at a time -- refused 23 of the 28 no-op
    rewrites across five runs instead of 26. The coder's real shape is
    app.py, test_app.py, app.py, and the middle write was throwing away the
    belief that made the third refusable.
    """
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(
        _request("write_file", file_path="/test_app.py", content="import app"), backend
    )
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2, "the second file must not have erased belief about the first"
    assert "Already written" in _text(result)


def test_a_read_still_loses_its_own_belief_to_a_write():
    """The other direction is untouched, and must be: a write IS how a
    read's answer changes. Asserted here because this file now holds both
    directions and they are deliberately asymmetric."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/app.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("read_file", file_path="/app.py"), backend)

    assert backend.calls == 3, "the write must have invalidated the earlier read"


def test_an_edit_is_not_guarded_here():
    """`edit_file` is deliberately out of Half A. An identical patch is not
    a no-op: re-applying old_string -> new_string after it applied fails to
    find old_string, so the tool errors and the failure rule covers it.
    Measured at 10 calls over five runs, 1 repeat."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("Edited '/app.py'")

    for _ in range(3):
        guard.wrap_tool_call(
            _request("edit_file", file_path="/app.py", old_string="a", new_string="b"), backend
        )

    assert backend.calls == 3


# --- OPEN-62 6a: the belief outlives the invocation, the REFUSAL is a fact --
#
# `_written` lives on the middleware instance and `subagents/runner.py:263`
# builds a fresh one per dispatch, so the coder that wrote `database.py` in
# task 1 and the coder that rewrote it byte-identically in task 2 shared no
# state at all. Measured over run8-run13: 4 occurrences in 3 runs, 5,155
# characters, ~1,288 output tokens.
#
# Two halves, and the second is what makes the first safe. The belief moves
# to the object that already outlives an agent rebuild (`RunUsage`, the
# shape OPEN-61 used one day earlier for the same reason), and a belief that
# came from a PREVIOUS invocation refuses nothing until the file on disk is
# read and found to hold exactly those bytes. Across a boundary Rudra runs
# the gate, the fix loop and git, and a run-scoped belief that trusted
# itself would eventually refuse a write that genuinely needed making --
# work that never happens is strictly worse than a wasted turn (OPEN-59).


class _RunScopedUsage(_Usage):
    """`_Usage` plus the one method the run-scoped belief needs."""

    def __init__(self):
        super().__init__()
        self.write_beliefs: dict[str, dict[str, str]] = {}

    def beliefs_for(self, role: str) -> dict[str, str]:
        return self.write_beliefs.setdefault(role, {})


def _across(usage, project_path, role="coder"):
    """A SECOND invocation: a brand new middleware sharing one run."""
    return RepeatGuardMiddleware(role=role, usage=usage, project_path=project_path)


def test_a_rewrite_by_the_NEXT_invocation_is_refused(tmp_path):
    """run13's case. t1 wrote `database.py`; t2 was a whole coder
    invocation whose only output was the same 1,480 characters again."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    first = _across(usage, tmp_path)
    first.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    second = _across(usage, tmp_path)
    result = second.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 1, "the second invocation's rewrite must not reach the backend"
    assert "Already written" in _text(result)


def test_a_CHANGED_write_by_the_next_invocation_is_always_performed(tmp_path):
    """The false-positive guard, and the one that matters most: a coder
    picking a file up where the last task left it must not be stopped."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY + "# more\n"), backend
    )

    assert backend.calls == 2


def test_a_belief_the_DISK_contradicts_refuses_nothing(tmp_path):
    """The whole safety argument for carrying a belief across a boundary.

    Between two invocations Rudra runs the gate, the fix loop and git, and
    this middleware sees none of it. So an inherited belief is a HINT: the
    refusal is issued only after reading the file and finding exactly those
    bytes. Here something changed the file, and the write is real work."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    (tmp_path / "app.py").write_text("something else entirely\n")
    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2


def test_a_belief_whose_file_is_GONE_refuses_nothing(tmp_path):
    """The same rule with the strongest case: the file was deleted between
    the two invocations, so the bytes are not there to be already written."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    (tmp_path / "app.py").unlink()
    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2


def test_an_inherited_belief_with_no_project_path_refuses_nothing(tmp_path):
    """No project path means no file to check, and an unverifiable belief
    from another invocation is not enough to refuse on. This is the
    degraded mode, and it is the behaviour that shipped with OPEN-60."""
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    RepeatGuardMiddleware(role="coder", usage=usage).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    RepeatGuardMiddleware(role="coder", usage=usage).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2


def test_the_guard_still_refuses_ITS_OWN_rewrite_without_reading_the_disk(tmp_path):
    """OPEN-60's rule is untouched and does not become conditional on a
    file read. Within one invocation the guard performed the write itself
    and nothing has intervened, so the post-condition argument stands on
    its own -- here the file on disk says something else entirely and the
    refusal still fires."""
    (tmp_path / "app.py").write_text("stale, and irrelevant\n")
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)
    guard = _across(usage, tmp_path)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 1
    assert "Already written" in _text(result)


def test_one_role_does_not_suppress_another_roles_write(tmp_path):
    """The coder and the tester write different files for different
    reasons. A shared unkeyed map would let either silence the other."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path, role="coder").wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    _across(usage, tmp_path, role="tester").wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2


def test_a_command_that_CHANGED_the_file_restores_the_write(tmp_path):
    """A shell command can touch any file, and across a boundary the guard
    sees none of it. It does not have to: the file is read at refusal time,
    so a command that changed it is answered by the bytes rather than by a
    rule about what commands can do."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    second = _across(usage, tmp_path)
    second.wrap_tool_call(_request("execute", command="sed -i s/1/2/ app.py"), backend)
    (tmp_path / "app.py").write_text("def main():\n    return 2\n")
    second.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_command_that_changed_NOTHING_does_not_restore_the_write(tmp_path):
    """The other half, and the reason the run map is never invalidated: a
    coder runs the test suite between two tasks, which changes no source
    file, and a rule that dropped the belief on any command would forget
    something true. Measured over run8-run13, that is what made a
    run-scoped belief worth exactly zero -- coders run tests."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    second = _across(usage, tmp_path)
    second.wrap_tool_call(_request("execute", command="pytest -q"), backend)
    result = second.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2
    assert "Already written" in _text(result)


def test_a_delete_in_a_LATER_invocation_restores_the_write(tmp_path):
    """Same rule, reached through the strongest case: the file is gone, so
    there are no bytes on disk to already be the ones asked for."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    _across(usage, tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    second = _across(usage, tmp_path)
    second.wrap_tool_call(_request("delete", file_path="/app.py"), backend)
    (tmp_path / "app.py").unlink()
    second.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_usage_without_the_method_keeps_the_old_per_instance_behaviour(tmp_path):
    """Duck-typed on RunUsage the way ModelRetryMiddleware is
    (`build.py:239-243`): a stand-in that predates this field must not
    raise, and must behave exactly as it did before."""
    (tmp_path / "app.py").write_text(BODY)
    usage = _Usage()  # no `beliefs_for`
    backend = _Backend(WROTE)

    RepeatGuardMiddleware(role="coder", usage=usage, project_path=tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )
    RepeatGuardMiddleware(role="coder", usage=usage, project_path=tmp_path).wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2


def test_a_cross_invocation_refusal_is_counted_and_announced(tmp_path):
    """A refusal must stay loud however it was reached (OPEN-60's own
    rule): one `writes_skipped`, one `skipped:` notice, one ToolMessage
    answering the call that was refused (OPEN-57)."""
    from langchain_core.messages import ToolMessage

    (tmp_path / "app.py").write_text(BODY)
    usage = _RunScopedUsage()
    trace = _Sink()
    backend = _Backend(WROTE)

    RepeatGuardMiddleware(
        role="coder", usage=usage, trace=trace, project_path=tmp_path
    ).wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    result = RepeatGuardMiddleware(
        role="coder", usage=usage, trace=trace, project_path=tmp_path
    ).wrap_tool_call(_request("write_file", "call-9", file_path="/app.py", content=BODY), backend)

    assert usage.skipped == [("coder", len(BODY))]
    assert [n for n in trace.notices if n["payload"].startswith("skipped:")]
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-9"


# --- OPEN-62 6c: invalidation is by PATH, not wholesale ---------------------
#
# `_record` used to clear the WHOLE `_written` map on any call that can
# change a file, so deleting X discarded what the guard knew about Y.
# Measured over run8-run13: one occurrence, 3,818 characters, the single
# largest waste in that table -- run9 deleted `app/tests/__pycache__` and
# rewrote `tests/test_app.py` byte-identically straight after.
#
# The narrowing applies only to calls that name ONE file. `execute` stays
# wholesale, because an arbitrary command can touch anything -- the same
# capability argument OPEN-60 settled for reads, read the other way.


def test_a_delete_of_a_DIFFERENT_file_leaves_the_belief_standing():
    """run9's case, and the largest single line in OPEN-62 §5's table."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("delete", file_path="/tests/__pycache__"), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2, "deleting another file must not restore this write"
    assert "Already written" in _text(result)


def test_an_edit_of_a_DIFFERENT_file_leaves_the_belief_standing():
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(
        _request("edit_file", file_path="/other.py", old_string="a", new_string="b"), backend
    )
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 2


def test_a_delete_naming_the_file_ANOTHER_WAY_still_restores_the_write():
    """The false-positive guard, and the reason invalidation compares
    loosely while the KEY stays exact (that is 6b's).

    A belief kept because the delete was spelled differently would refuse a
    write that genuinely needed making, and work that never happens is
    strictly worse than a wasted turn."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/project/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("delete", file_path="app.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/project/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_command_still_drops_every_belief():
    """The half of the rule that is about safety, pinned so 6c's narrowing
    cannot quietly widen to `execute`: a shell command can touch any file,
    so nothing survives it."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/other.py", content=BODY), backend)
    guard.wrap_tool_call(_request("execute", command="rm -f /app.py /other.py"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/other.py", content=BODY), backend)

    assert backend.calls == 5


def test_an_unknown_tool_still_drops_every_belief():
    """An MCP tool, `task`, anything this file has never heard of: its
    blast radius is unknown, so it is treated as `execute` is."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("call_mcp_tool", server="x", tool="y"), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_failed_write_only_loses_the_belief_about_ITS_OWN_path():
    """Same rule, same reason: a write to X that errored says nothing about
    what is on disk at Y."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE, "Error: permission denied", WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/locked.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 2
    assert "Already written" in _text(result)


def test_a_call_naming_no_file_at_all_drops_every_belief():
    """A call this guard cannot attribute to a file is one it cannot
    reason about, so it falls back to the wholesale answer -- the narrowing
    applies only where there IS a path to narrow to."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE, "Error: no such file", WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 3


def test_a_failed_write_is_not_believed():
    """The post-condition argument is the whole justification, and a write
    that errored did not satisfy it."""
    guard = RepeatGuardMiddleware()
    backend = _Backend("Error: permission denied")

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 2


def test_a_skipped_write_is_counted_separately_from_a_deduped_read():
    """Two claims, two numbers. OPEN-39 Phase 2's saving is re-reads
    avoided and this one is rewrites avoided; one field carrying both would
    be neither."""
    usage = _Usage()
    guard = RepeatGuardMiddleware(role="coder", usage=usage)
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert usage.skipped == [("coder", len(BODY))]
    assert usage.deduped == [], "reads_deduped must not absorb this"


def test_a_skipped_write_emits_one_notice_naming_the_path():
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink)
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert len(sink.notices) == 1
    assert sink.notices[0]["name"] == "repeat-guard", "one name for the whole middleware"
    assert sink.notices[0]["role"] == "coder"
    assert "write_file" in sink.notices[0]["payload"]
    assert "/app.py" in sink.notices[0]["payload"]


def test_the_three_rules_are_told_apart_by_their_payload_verb():
    """`repeat-guard` is one name, so the payload is the only thing that
    says which rule fired."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink)

    guard.wrap_tool_call(_request("read_file", file_path="/m.py"), _Backend(CONTENTS))
    guard.wrap_tool_call(_request("read_file", file_path="/m.py"), _Backend(CONTENTS))
    assert sink.notices[-1]["payload"].startswith("dedupe:")

    guard = RepeatGuardMiddleware(role="coder", trace=sink)
    backend = _Backend(WROTE)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    assert sink.notices[-1]["payload"].startswith("skipped:")


def test_a_write_is_refused_without_usage_or_trace():
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert "Already written" in _text(result)


async def test_the_async_path_refuses_a_no_op_write_too():
    guard = RepeatGuardMiddleware()
    seen = []

    async def handler(request):
        seen.append(request)
        return WROTE

    await guard.awrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), handler)
    result = await guard.awrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), handler
    )

    assert len(seen) == 1
    assert "Already written" in _text(result)


# --- OPEN-62 6b / OPEN-52: the KEY is the file, not the spelling ------------
#
# `compat/virtual_paths.py` is the one function that says which real file a
# model-written path names, and the gate, the approval preview and the
# backend all route through it so they cannot disagree about which file a
# call touches (CR-B4). This middleware did not, so `/app.py`, `./app.py`
# and `<project>/app.py` were three keys for one file.
#
# OPEN-50 landed exactly this for the OTHER repeat guard
# (`subagents/runner.py::_call_key`) on 2026-08-29. OPEN-52 has been the
# read-rule half of it, measured four times at +0 and once at +3, and it
# was one measurement away from `WONTFIX`. What changed is the write rule:
# with the belief carried across invocations (6a) the respellings are the
# only thing left in the way, and they are worth 3 rewrites over run8-run13.


def test_two_spellings_of_one_failing_read_are_one_signature(tmp_path):
    """OPEN-52's original shape, and the reason it was filed by reading:
    the third identical failure is refused, but only if the guard can see
    that it IS identical."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    guard.wrap_tool_call(_request("read_file", file_path="/missing.py"), backend)
    guard.wrap_tool_call(_request("read_file", file_path="./missing.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="missing.py"), backend)

    assert backend.calls == 2, "the third spelling is the third identical call"
    assert "already failed 2 times" in _text(result)


def test_two_spellings_of_one_answered_read_are_one_signature(tmp_path):
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="app.py"), backend)
    result = guard.wrap_tool_call(_request("read_file", file_path="/app.py"), backend)

    assert backend.calls == 1
    assert "Already read" in _text(result)


def test_a_rewrite_under_a_DIFFERENT_SPELLING_is_refused(tmp_path):
    """run11's 303 characters, and the first non-zero number OPEN-52 ever
    had: the coder wrote `app.py` and then `/app.py`. One file, two keys."""
    (tmp_path / "app.py").write_text(BODY)
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="app.py", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/app.py", content=BODY), backend
    )

    assert backend.calls == 1
    assert "Already written" in _text(result)


def test_run13s_case_end_to_end(tmp_path):
    """The run that filed OPEN-62, whole: task 1 writes `database.py`, task
    2 is a fresh coder invocation that writes `/database.py` with the same
    1,480 characters. It needs BOTH halves -- the belief has to survive the
    invocation (6a) and the two spellings have to be one key (6b)."""
    (tmp_path / "database.py").write_text(BODY)
    usage = _RunScopedUsage()
    backend = _Backend(WROTE)

    first = RepeatGuardMiddleware(role="coder", usage=usage, project_path=tmp_path)
    first.wrap_tool_call(_request("write_file", file_path="database.py", content=BODY), backend)
    second = RepeatGuardMiddleware(role="coder", usage=usage, project_path=tmp_path)
    result = second.wrap_tool_call(
        _request("write_file", file_path="/database.py", content=BODY), backend
    )

    assert backend.calls == 1
    assert "Already written" in _text(result)


def test_the_host_spelling_of_a_path_is_the_same_file(tmp_path):
    """run9 typed the full host path for a file it had written relatively,
    which is the third spelling in play and the one a naive `lstrip('/')`
    misses."""
    (tmp_path / "app.py").write_text(BODY)
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="app.py", content=BODY), backend)
    guard.wrap_tool_call(
        _request("write_file", file_path=str(tmp_path / "app.py"), content=BODY), backend
    )

    assert backend.calls == 1


def test_without_a_project_path_the_spelling_is_still_the_key(tmp_path):
    """The degraded mode, stated so it cannot drift: no root means no
    resolution, and the middleware behaves exactly as it did before."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(WROTE)

    guard.wrap_tool_call(_request("write_file", file_path="app.py", content=BODY), backend)
    guard.wrap_tool_call(_request("write_file", file_path="/app.py", content=BODY), backend)

    assert backend.calls == 2


def test_a_grep_PATTERN_is_never_resolved(tmp_path):
    """Only path arguments are resolved -- `subagents/runner.py:167` says
    the same thing about `subagent_type`. A grep pattern can look exactly
    like a path, and rewriting it would make two different searches one
    key."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("grep", pattern="/app.py", path="/src"), backend)
    guard.wrap_tool_call(_request("grep", pattern="app.py", path="/src"), backend)

    assert backend.calls == 2


def test_a_read_window_is_still_part_of_the_key(tmp_path):
    """OPEN-35's window, pinned here because 6b touches the key: page 2 of
    a file is not a repeat of page 1, however either was spelled."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(CONTENTS)

    guard.wrap_tool_call(_request("read_file", file_path="/app.py", offset=0), backend)
    guard.wrap_tool_call(_request("read_file", file_path="app.py", offset=100), backend)

    assert backend.calls == 2


# --- OPEN-95: the failure refusal must be TRUE and FOLLOWABLE --------------
#
# Run 2cde3406f7d6's coder called `ls '/src'`, then `ls '<project>/src'`,
# then `ls 'src'` -- a spelling it had never used -- and was told it had
# called this "with these exact arguments", shown an error quoting '/src',
# and instructed to "change the arguments, use ls to find the correct path,
# or continue without this file". It re-sent the identical call and the
# invocation halted.
#
# The identity is right and stays (OPEN-52, the section above). What was
# missing is the EXPLANATION of that identity, and its absence was filled
# with three defects: a false claim about the arguments, an error quoted
# against a different spelling from the one asked about, and two impossible
# instructions ahead of the one viable branch.


def test_the_failure_refusal_makes_no_claim_about_the_arguments(tmp_path):
    """The false half. The key is resolved, the arguments are not, so any
    sentence about argument equality is a statement the model can see is
    untrue -- it had just typed something different."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    guard.wrap_tool_call(_request("ls", path="/src"), backend)
    guard.wrap_tool_call(_request("ls", path=str(tmp_path / "src")), backend)
    result = guard.wrap_tool_call(_request("ls", path="src"), backend)

    text = _text(result)
    assert "exact arguments" not in text
    assert "same arguments" not in text


def test_the_failure_refusal_names_the_resolved_target(tmp_path):
    """What the trace notice has always said and the model was never told.
    `_announce` renders the resolved target; the message rendered the raw
    argument of whichever spelling failed last, so the two disagreed."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    guard.wrap_tool_call(_request("ls", path="/src"), backend)
    guard.wrap_tool_call(_request("ls", path=str(tmp_path / "src")), backend)
    result = guard.wrap_tool_call(_request("ls", path="src"), backend)

    assert "'src'" in _text(result)


def test_the_failure_refusal_says_the_spellings_are_one_call(tmp_path):
    """The fact the model needed and was never given. Without it "change
    the arguments" describes an action that does not exist -- every
    spelling of that path resolves to one key."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("ls", path="/src"), backend)
    result = guard.wrap_tool_call(_request("ls", path="src"), backend)

    text = _text(result)
    assert "one call" in text
    assert "'/src'" in text
    assert f"'{tmp_path}/src'" in text, "the third spelling, spelled out in full"


def test_two_spellings_of_one_failing_read_get_the_same_refusal(tmp_path):
    """It is one call, so it is one answer. A difference here means the
    message is being built from the raw argument again."""

    def refusal_for(spelling: str) -> str:
        guard = RepeatGuardMiddleware(project_path=tmp_path)
        backend = _Backend(NOT_FOUND)
        for _ in range(2):
            guard.wrap_tool_call(_request("ls", path="/src"), backend)
        return _text(guard.wrap_tool_call(_request("ls", path=spelling), backend))

    assert refusal_for("src") == refusal_for("/src") == refusal_for(str(tmp_path / "src"))


def test_the_failure_refusal_names_a_move_that_can_be_made(tmp_path):
    """The unfollowable half. "use ls to find the correct path" was
    answering an `ls` refusal with `ls`; the coder's actual task was to
    CREATE that path, which needs no listing at all -- `_PATH_RULES` says
    directories are made on the way."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("ls", path="/src"), backend)
    result = guard.wrap_tool_call(_request("ls", path="/src"), backend)

    text = _text(result)
    assert "carry on without it" in text
    assert "write it" in text


def test_without_a_project_path_the_refusal_claims_no_equivalence():
    """The degraded mode is the honest one. With no root nothing is
    resolved and the spelling IS the key, so the equivalence sentence
    would be the new false claim (`test_without_a_project_path_the_
    spelling_is_still_the_key` is the behaviour it would misdescribe)."""
    guard = RepeatGuardMiddleware()
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("ls", path="/src"), backend)
    result = guard.wrap_tool_call(_request("ls", path="/src"), backend)

    text = _text(result)
    assert "one call" not in text
    assert "exact arguments" not in text


def test_a_grep_with_no_path_claims_no_equivalence(tmp_path):
    """A pattern is never resolved (`test_a_grep_PATTERN_is_never_resolved`),
    so respelling one genuinely IS a different search. Claiming otherwise
    would be this item's own defect pointed the other way."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    for _ in range(2):
        guard.wrap_tool_call(_request("grep", pattern="/app.py"), backend)
    result = guard.wrap_tool_call(_request("grep", pattern="/app.py"), backend)

    assert "one call" not in _text(result)


def test_the_identity_behaviour_is_unchanged_by_the_new_wording(tmp_path):
    """The regression guard on Option B, rejected in the item's §5: a
    genuinely new spelling gets no free attempt. Three spellings, one key,
    two calls through."""
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    backend = _Backend(NOT_FOUND)

    guard.wrap_tool_call(_request("ls", path="/src"), backend)
    guard.wrap_tool_call(_request("ls", path="./src"), backend)
    guard.wrap_tool_call(_request("ls", path="src"), backend)

    assert backend.calls == 2


# --- OPEN-126: a refused re-send names the route the agent is missing --------
#
# Run 1dab3a848252's coder wrote run_tests.sh and re-sent it -- "Now let me run
# the tests to verify they pass:" -- and was told only "Move on". Across the
# archive the model re-sent the same bytes after this refusal in 8 of 13 cases.

CODER_GRANTS = ("ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep")
TESTER_GRANTS = CODER_GRANTS + ("execute", "run_tests")


def _refused_resend(guard):
    backend = _Backend(WROTE)
    guard.wrap_tool_call(_request("write_file", file_path="/run_tests.sh", content=BODY), backend)
    result = guard.wrap_tool_call(
        _request("write_file", file_path="/run_tests.sh", content=BODY), backend
    )
    assert backend.calls == 1, "still refused: the decision does not change"
    return result


def test_a_refused_resend_tells_a_coder_the_gate_runs_the_tests():
    text = _text(_refused_resend(RepeatGuardMiddleware(granted=CODER_GRANTS)))

    assert text.startswith("Already written: '") and "run_tests.sh" in text
    assert "Writing a file does not run it" in text
    assert "a verification gate runs" in text
    assert "call no tool" in text
    assert "execute" not in text  # OPEN-15: the coder holds no shell


def test_a_refused_resend_tells_a_tester_to_use_run_tests():
    text = _text(_refused_resend(RepeatGuardMiddleware(granted=TESTER_GRANTS)))

    assert "`run_tests`" in text
    assert "nothing in this agent can run" not in text


def test_a_guard_built_without_grants_keeps_today_s_words():
    """The planner's guard and every bare guard in this file: no grants, no
    route, and the refusal ends exactly where it always did."""
    text = _text(_refused_resend(RepeatGuardMiddleware()))

    assert text.endswith("or edit the file if you meant to change it.")


def test_the_routed_refusal_is_still_a_refusal_not_a_failure():
    """OPEN-94/118: classified by REFUSAL_KEY, never by the words, and the words
    still do not read as an error."""
    from rudra.trace.stream import is_rudra_refusal, looks_like_error

    result = _refused_resend(RepeatGuardMiddleware(granted=CODER_GRANTS))

    assert is_rudra_refusal(result) is True
    assert looks_like_error(_text(result)) is False


# --- OPEN-134: a refused interpreter hunt names the route ----------------------
#
# Run a4196786280d's t9 dispatch 2: after its edit, the coder -- which holds no
# shell -- sent 40 globs for `.venv/bin/python` and `pytest`, and each repeat
# was answered only "That earlier result is still current -- use it". Across
# the archive 53 of 171 dedupe refusals were that shape, from exactly two
# interpreter hunts; the other 118 were ordinary re-reads, 60 the planner's.


def _refused_repeat(guard, name="glob", **args):
    backend = _Backend("['/.venv/bin/pytest']")
    guard.wrap_tool_call(_request(name, **args), backend)
    result = guard.wrap_tool_call(_request(name, **args), backend)
    assert backend.calls == 1, "still refused: the decision does not change"
    return _text(result)


def test_a_refused_interpreter_search_tells_a_coder_the_gate_runs_the_tests():
    text = _refused_repeat(
        RepeatGuardMiddleware(granted=CODER_GRANTS), pattern="**/.venv/bin/pytest"
    )

    assert text.startswith("Already read: ")
    assert "Finding an interpreter or a test runner does not run it" in text
    assert "a verification gate runs" in text
    assert "call no tool" in text
    assert "execute" not in text  # OPEN-15


def test_a_refused_interpreter_search_tells_a_tester_to_use_run_tests():
    text = _refused_repeat(RepeatGuardMiddleware(granted=TESTER_GRANTS), pattern="/usr/bin/python*")

    assert "`run_tests`" in text
    assert "Finding an interpreter" not in text


def test_an_ordinary_repeat_read_keeps_today_s_words():
    """118 of the archive's 171 dedupe refusals: no route there, and a file
    that merely mentions pytest is not a runner."""
    for args in ({"file_path": "/models.py"}, {"file_path": "/pytest.ini"}):
        text = _refused_repeat(RepeatGuardMiddleware(granted=CODER_GRANTS), "read_file", **args)
        assert text.endswith("to change the file, write or edit it.")


def test_a_guard_without_grants_never_routes_a_read():
    """The planner's guard: no grants, no route, whatever it searched for."""
    text = _refused_repeat(RepeatGuardMiddleware(), pattern="**/.venv/bin/python")

    assert text.endswith("to change the file, write or edit it.")


def test_every_archived_runner_search_is_runner_shaped():
    """The shapes the archive holds, and one each way that must not match."""
    from rudra.middleware.repeat_guard import runner_shaped

    for value in (
        "**/.venv/bin/pytest",
        "/usr/bin/python*",
        "**/.venv/bin/python",
        "/usr/bin/python3",
        ".venv/bin",
        "**/.venv/bin/python*",
        "/usr/bin/python3*",
        "**/.venv/**/python*",
        "**/.venv/bin/python3.12",
    ):
        assert runner_shaped({"pattern": value}), value
    for value in ("/pytest.ini", "tests/test_python_utils.py", "/src/app.py", "tests/*.py"):
        assert not runner_shaped({"pattern": value}), value


def test_a_repeated_search_under_a_path_is_refused_naming_its_pattern(tmp_path):
    """OPEN-150: `_target` answers with the path when a search carries one, so
    run F2's `glob('**/test_*.py', path='/')` was refused as "`glob` on '.'" --
    a call the model never made. The key kept the pattern; now the words do."""
    sink = _Sink()
    guard = RepeatGuardMiddleware(role="coder", trace=sink, project_path=tmp_path)
    backend = _Backend("['/tests/test_a.py']")

    for _ in range(2):
        result = guard.wrap_tool_call(_request("glob", pattern="**/test_*.py", path="/"), backend)

    assert "`glob` on '**/test_*.py' under '.'" in _text(result)
    assert "'**/test_*.py' under '.'" in sink.notices[-1]["payload"]


def test_two_searches_under_one_path_are_refused_in_different_words(tmp_path):
    guard = RepeatGuardMiddleware(project_path=tmp_path)
    texts = []
    for pattern in ("**/test_*.py", "**/*.md"):
        backend = _Backend("[]")
        guard.wrap_tool_call(_request("glob", pattern=pattern, path="/"), backend)
        texts.append(
            _text(guard.wrap_tool_call(_request("glob", pattern=pattern, path="/"), backend))
        )

    assert texts[0] != texts[1]


def test_only_the_dedupe_rule_marks_its_refusal_as_a_dedupe(tmp_path):
    """OPEN-149: `subagents/runner.py` counts dedupes by field, never by the
    "Already read:" lead -- the failure and write refusals must not carry it."""
    from rudra.trace.stream import DEDUPE_KEY, REFUSAL_KEY

    guard = RepeatGuardMiddleware()
    read = _Backend("['/a.py']")
    guard.wrap_tool_call(_request("glob", pattern="**/*.py"), read)
    deduped = guard.wrap_tool_call(_request("glob", pattern="**/*.py"), read)
    failing = _Backend(NOT_FOUND)
    for _ in range(3):
        failed = guard.wrap_tool_call(_request("read_file", file_path="/x"), failing)

    assert deduped.additional_kwargs == {REFUSAL_KEY: True, DEDUPE_KEY: True}
    assert failed.additional_kwargs == {REFUSAL_KEY: True, DEDUPE_KEY: False}
