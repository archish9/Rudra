"""run_subagent's result mapping and its per-invocation guards."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.subagents import runner
from rudra.subagents.runner import SubagentContext, SubagentResult, run_subagent
from rudra.trace.stream import REFUSAL_KEY


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeAgent:
    max_invocation_seconds: float = runner.MAX_INVOCATION_SECONDS


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict
    agent: FakeAgent = field(default_factory=FakeAgent)


def make_context(tmp_path, *, max_invocation_seconds=runner.MAX_INVOCATION_SECONDS, trace=None):
    return SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=None,
        console=Console(quiet=True),
        cfg=FakeCfg(
            compat=FakeCompat(),
            tools=FakeTools(),
            models={},
            agent=FakeAgent(max_invocation_seconds=max_invocation_seconds),
        ),
        session_id="s1",
        trace=trace,
    )


def stream_of(*chunks):
    # **kwargs: Step 15a added trace/stream_tokens/role to
    # run_with_approvals, and this stub stands in for it.
    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    return fake_stream


def ai(text="", tool_calls=()):
    return AIMessage(content=text, tool_calls=list(tool_calls))


def call(name, **args):
    return {"name": name, "args": args, "id": f"c{id(args)}"}


@pytest.fixture
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    return make_context(tmp_path)


async def test_an_unknown_name_raises(patched):
    with pytest.raises(ValueError, match="reviewr"):
        await run_subagent("reviewr", "go", context=patched)


async def test_a_clean_run_returns_the_final_message(monkeypatch, patched):
    monkeypatch.setattr(
        runner,
        "run_with_approvals",
        stream_of({"messages": [ai("first"), ai("Findings: none.")]}),
    )
    result = await run_subagent("reviewer", "review it", context=patched)
    assert isinstance(result, SubagentResult)
    assert result.ok is True
    assert result.text == "Findings: none."
    assert result.halted_reason is None
    assert result.error is None


async def test_the_last_non_empty_message_wins(monkeypatch, patched):
    # A trailing empty AIMessage is common after a tool call.
    monkeypatch.setattr(
        runner,
        "run_with_approvals",
        stream_of({"messages": [ai("the answer"), ai("")]}),
    )
    result = await run_subagent("reviewer", "go", context=patched)
    assert result.text == "the answer"


async def test_a_build_failure_is_reported_not_raised(monkeypatch, patched):
    def boom(spec, context, task=""):
        raise RuntimeError("no provider package installed")

    monkeypatch.setattr(runner, "build_agent", boom)
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert result.error is not None
    assert "no provider package" in result.error


async def test_a_provider_error_mid_stream_is_reported_not_raised(monkeypatch, patched):
    async def exploding(agent, inputs, config, gate, console, **kwargs):
        yield {"messages": [ai("starting")]}
        raise RuntimeError("connection reset")

    monkeypatch.setattr(runner, "run_with_approvals", exploding)
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "connection reset" in result.error
    # A1.39 is not fixed here -- but the caller gets a value, not a crash.


async def test_three_consecutive_tool_errors_halt(monkeypatch, patched):
    errors = [ToolMessage(content="Error: nope", tool_call_id=str(i)) for i in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": [ai("try"), *errors]}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "consecutive" in result.halted_reason


async def test_a_success_between_errors_resets_the_counter(monkeypatch, patched):
    messages = [
        ai("try"),
        ToolMessage(content="Error: nope", tool_call_id="1"),
        ToolMessage(content="fine", tool_call_id="2"),
        ToolMessage(content="Error: nope", tool_call_id="3"),
        ai("done"),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is True


async def test_the_same_tool_call_three_times_halts(monkeypatch, patched):
    repeated = [ai("", [call("write_file", file_path="a.py")]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "repeated" in result.halted_reason


async def test_the_task_tool_is_watched_by_the_repeat_guard(monkeypatch, patched):
    # A1.20: the guard listed `task` but no agent could call it. The
    # general-purpose subagent means one now can.
    repeated = [ai("", [call("task", subagent_type="general-purpose")]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "task" in result.halted_reason


async def test_the_same_tool_on_different_files_does_not_halt(monkeypatch, patched):
    writes = [ai("", [call("write_file", file_path=f"{i}.py")]) for i in range(3)]
    monkeypatch.setattr(
        runner, "run_with_approvals", stream_of({"messages": [*writes, ai("done")]})
    )
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is True


# --- OPEN-35: paging a long file is not repeating -------------------------


def test_the_call_key_separates_read_windows(tmp_path):
    """`read_file` pages, so the window is part of what makes a call
    distinct. Without it the key for page 1 and page 3 of one file are the
    same string and the guard counts them as one call made three times."""
    whole = {"name": "read_file", "args": {"file_path": "/a.py"}}
    page_two = {"name": "read_file", "args": {"file_path": "/a.py", "offset": 100}}
    page_two_short = {
        "name": "read_file",
        "args": {"file_path": "/a.py", "offset": 100, "limit": 50},
    }

    assert runner._call_key(whole, tmp_path) != runner._call_key(page_two, tmp_path)
    assert runner._call_key(page_two, tmp_path) != runner._call_key(page_two_short, tmp_path)
    # The identical call is still identical -- that is what the guard is for.
    assert runner._call_key(page_two, tmp_path) == runner._call_key(dict(page_two), tmp_path)
    # A tool with no window carries an empty one, and two writes of the same
    # bytes are still one key. (Two writes of DIFFERENT bytes are not -- that
    # is OPEN-60 Half B, tested below.)
    write = {"name": "write_file", "args": {"file_path": "/a.py", "content": "x"}}
    assert runner._call_key(write, tmp_path) == runner._call_key(
        {"name": "write_file", "args": {"file_path": "/a.py", "content": "x"}}, tmp_path
    )


# --- OPEN-50: one file is one key, however the model spelled it ----------


def test_the_call_key_collapses_every_spelling_of_one_file(tmp_path):
    """The guard must name the same file the gate, the approval preview and
    the backend name -- `compat/virtual_paths.py` is the one function that
    says which that is (CR-B4), and before OPEN-50 this guard did not use
    it. Run b593a6137c64 wrote `app.py` fourteen times under four
    spellings, which is four keys of at most seven and never three."""
    spellings = ["/app.py", "./app.py", "app.py", str(tmp_path / "app.py")]
    keys = {
        runner._call_key({"name": "write_file", "args": {"file_path": s}}, tmp_path)
        for s in spellings
    }
    assert len(keys) == 1
    assert next(iter(keys)) == ("write_file", "app.py", "", "", "")


def test_the_call_key_keeps_different_files_apart(tmp_path):
    """Resolving is not collapsing. `/app/tests/test_app.py` is a real
    second file -- run b593a6137c64 created it by mistake and pytest
    reported `import file mismatch` against `tests/test_app.py` -- so the
    two must stay two keys."""
    right = {"name": "write_file", "args": {"file_path": "/tests/test_app.py"}}
    wrong = {"name": "write_file", "args": {"file_path": "/app/tests/test_app.py"}}
    assert runner._call_key(right, tmp_path) != runner._call_key(wrong, tmp_path)


def test_the_call_key_leaves_a_delegation_alone(tmp_path):
    """`task` carries a `subagent_type`, not a path. Running that through a
    path resolver would be meaningless, so the identifier is only resolved
    when the argument is one of the path arguments."""
    delegate = {"name": "task", "args": {"subagent_type": "general-purpose", "description": "x"}}
    assert runner._call_key(delegate, tmp_path) == ("task", "general-purpose", "", "", "")


def test_the_call_key_resolves_a_backend_route_the_way_the_gate_does(tmp_path):
    """`/artifacts/` and `/skills/` are mounted outside the project root,
    and `virtual_to_relative` returns a relative path for them rather than
    None -- which is what the gate already reads them as. The guard is a
    counter, not a resolver (CR-B4), so it agrees rather than special-casing."""
    route = {"name": "read_file", "args": {"file_path": "/artifacts/note.md"}}
    assert runner._call_key(route, tmp_path) == ("read_file", "artifacts/note.md", "", "", "")


def test_the_call_key_survives_a_missing_identifier(tmp_path):
    """A watched tool called with neither a path nor a subagent_type keys on
    the empty string, as it did before OPEN-50. The guard must never be the
    thing that raises inside the stream loop."""
    assert runner._call_key({"name": "write_file", "args": {}}, tmp_path) == (
        "write_file",
        "",
        "",
        "",
        "",
    )


async def test_three_writes_in_three_spellings_halt(monkeypatch, patched):
    """End to end, which is where OPEN-50 was actually paid for: run
    b593a6137c64 halted ten times and every halt was a respelling, so the
    guard both fired too late (twelve writes before anything counted three)
    and fired anyway (both blocked tasks died of it). Three spellings of one
    file are one call made three times."""
    writes = [
        ai("", [call("write_file", file_path="/app.py")]),
        ai("", [call("write_file", file_path="./app.py")]),
        ai("", [call("write_file", file_path="app.py")]),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": writes}))

    result = await run_subagent("coder", "write it", context=patched)

    assert result.ok is False
    assert "repeated" in (result.halted_reason or "")
    # The resolved spelling is the durable record (OPEN-44's Task.halts).
    assert "'app.py'" in (result.halted_reason or "")


async def test_paging_through_one_long_file_does_not_halt(monkeypatch, patched):
    """OPEN-35: `read_file` pages at 100 lines, so the third page of any
    file was the third "repeat" and tripped MAX_REPEATED_CALLS -- meaning no
    subagent could read a file past ~200 lines.

    Run 36023bb8bdd1 ended on exactly this: the reviewer, four events from
    the end of the debug log, asked for `offset: 200` of a 400-line
    test_api.py and was killed for it.
    """
    pages = [
        ai("", [call("read_file", file_path="/tests/integration/test_api.py", offset=offset)])
        for offset in (0, 100, 200, 300)
    ]
    monkeypatch.setattr(
        runner, "run_with_approvals", stream_of({"messages": [*pages, ai("Findings: none.")]})
    )
    result = await run_subagent("reviewer", "review it", context=patched)
    assert result.ok is True
    assert result.halted_reason is None
    assert result.text == "Findings: none."


async def test_the_same_page_read_three_times_still_halts(monkeypatch, patched):
    """The guard's real target is the identical call made again (OPEN-10's
    four identical read_files, A1.92's globs). Widening the key must not
    retire it."""
    repeated = [ai("", [call("read_file", file_path="/a.py", offset=100)]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("reviewer", "review it", context=patched)
    assert result.ok is False
    assert "repeated" in (result.halted_reason or "")
    # `a.py`, not `/a.py`: the halt interpolates the resolved identifier
    # since OPEN-50. Naming the file is what this asserts, not the spelling.
    assert "'a.py'" in (result.halted_reason or ""), "the halt must still name the file"


async def test_the_whole_file_read_three_times_still_halts(monkeypatch, patched):
    """`offset` and `limit` carry defaults, so an unpaged read arrives with
    neither in `args`. Three of those are still one call made three times."""
    repeated = [ai("", [call("read_file", file_path="/a.py")]) for _ in range(3)]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": repeated}))
    result = await run_subagent("reviewer", "review it", context=patched)
    assert result.ok is False
    assert "repeated" in (result.halted_reason or "")


async def test_each_invocation_gets_a_distinct_thread(monkeypatch, patched):
    seen: list[str] = []

    async def capture(agent, inputs, config, gate, console, **kwargs):
        seen.append(config["configurable"]["thread_id"])
        yield {"messages": [ai("ok")]}

    monkeypatch.setattr(runner, "run_with_approvals", capture)
    await run_subagent("coder", "one", context=patched)
    await run_subagent("coder", "two", context=patched)
    assert len(set(seen)) == 2
    assert all(t.startswith("s1-coder-") for t in seen)


async def test_an_explicit_thread_id_is_honoured(monkeypatch, patched):
    seen: list[str] = []

    async def capture(agent, inputs, config, gate, console, **kwargs):
        seen.append(config["configurable"]["thread_id"])
        yield {"messages": [ai("ok")]}

    monkeypatch.setattr(runner, "run_with_approvals", capture)
    await run_subagent("coder", "one", context=patched, thread_id="fixed")
    assert seen == ["fixed"]


# --- the total-call ceiling (A1.92) --------------------------------------


async def test_a_subagent_stops_after_the_total_call_ceiling(monkeypatch, patched):
    """A1.92: denied `bash`, a delegated subagent issued 204 successful
    globs over 12 minutes hunting for a Python interpreter, and NO guard
    could see it -- MAX_REPEATED_CALLS keys on (tool, target) so 204
    different patterns are 204 firsts, and MAX_CONSECUTIVE_FAILURES never
    fires because every glob succeeded. `No files found` is a success.

    A ceiling on total calls is the only guard that catches spending
    without failing.
    """
    from rudra.subagents.runner import MAX_TOTAL_CALLS

    calls = [
        {"name": "glob", "args": {"pattern": f"**/python{n}", "path": "/"}, "id": str(n)}
        for n in range(MAX_TOTAL_CALLS + 10)
    ]
    messages = [ai(tool_calls=[call]) for call in calls]
    chunks = [{"messages": messages[: n + 1]} for n in range(len(messages))]

    monkeypatch.setattr("rudra.subagents.runner.run_with_approvals", stream_of(*chunks))
    result = await run_subagent("coder", "write it", context=patched)

    assert result.ok is False
    assert "tool calls" in (result.halted_reason or "")


async def test_an_ordinary_run_is_nowhere_near_the_ceiling(monkeypatch, patched):
    """The ceiling must not fire on real work. Step 15c's acceptance shows
    a healthy coder invocation at well under 20 calls; the runaway was 204."""
    from rudra.subagents.runner import MAX_TOTAL_CALLS

    assert MAX_TOTAL_CALLS >= 50

    messages = [
        ai(tool_calls=[{"name": "read_file", "args": {"file_path": f"{n}.py"}, "id": str(n)}])
        for n in range(20)
    ]
    monkeypatch.setattr(
        "rudra.subagents.runner.run_with_approvals",
        stream_of(*[{"messages": messages[: n + 1]} for n in range(len(messages))]),
    )

    result = await run_subagent("coder", "write it", context=patched)

    assert result.ok is True


# --- OPEN-42: how a turn actually ends, and what a repeat looks like ------


async def test_a_text_only_reply_ends_the_turn_and_is_what_the_caller_gets(monkeypatch, patched):
    """The finding `_FINISH_RULES` is built on, pinned so an upgrade cannot
    quietly take it away.

    Run7's coder invented `task_complete` because nothing told it a
    text-only reply is the stop verb. It already is: langgraph's ReAct loop
    ends on an AIMessage with no tool calls, and `run_subagent` reports that
    message as `text` with `ok=True`. deepagents ships no completion tool --
    `grep -rl task_complete` over the installed package returns nothing.
    """
    messages = [
        ai("", [call("write_file", file_path="src/app.py")]),
        ToolMessage(content="Updated file src/app.py", tool_call_id="1"),
        ai("Wrote src/app.py with the CRUD endpoints."),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))

    result = await run_subagent("coder", "write it", context=patched)

    assert result.ok is True
    assert result.halted_reason is None
    assert result.text == "Wrote src/app.py with the CRUD endpoints."


async def test_repeats_separated_by_other_calls_still_halt(monkeypatch, patched):
    """Run7's actual shape, which the existing back-to-back test does not
    cover: `/task_complete.txt`, `/DONE`, `/DONE`, `/task_complete.txt`,
    `/DONE`.

    `repeated` is keyed per (tool, target) and never reset, so the third
    `/DONE` halts even with two unrelated writes in between. This is why
    OPEN-42 §6's premise is wrong -- the guard DID fire in run7, on the
    third marker write, which is why that write has no `tool_result` in the
    debug log. It was invisible for a different reason (see OPEN-44).
    """
    writes = [
        ai("", [call("write_file", file_path="/task_complete.txt")]),
        ai("", [call("write_file", file_path="/DONE")]),
        ai("", [call("write_file", file_path="/DONE")]),
        ai("", [call("write_file", file_path="/task_complete.txt")]),
        ai("", [call("write_file", file_path="/DONE")]),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": writes}))

    result = await run_subagent("coder", "write it", context=patched)

    assert result.ok is False
    assert "'DONE'" in (result.halted_reason or "")  # resolved, since OPEN-50


# --- OPEN-60 Half B: content is the other half of the key -----------------
#
# `MAX_REPEATED_CALLS` halts the WHOLE invocation and throws its work away.
# Before this, three writes to one file tripped it whether or not they
# carried the same bytes -- so a coder building a file up was killed at its
# third edit. Measured over run9 and run11: of thirteen sequences that
# reached the threshold, 4 were byte-identical, 5 mixed, and 4 were entirely
# different content, i.e. an agent doing real work.


def test_the_call_key_separates_writes_that_carry_different_content(tmp_path):
    """run11 invocation 3: `app.py` at 45 bytes, then 404, then 352 -- three
    distinct shas, a coder building a file up, halted for it."""
    keys = {
        runner._call_key(
            {"name": "write_file", "args": {"file_path": "/app.py", "content": body}}, tmp_path
        )
        for body in ("a" * 45, "b" * 404, "c" * 352)
    }
    assert len(keys) == 3


def test_the_call_key_still_joins_writes_that_carry_the_same_content(tmp_path):
    """Half A refuses these, but the halt must still be able to see them --
    a write that reaches the runner having survived the middleware is
    genuine repetition."""
    same = [
        {"name": "write_file", "args": {"file_path": "/app.py", "content": "x"}},
        {"name": "write_file", "args": {"file_path": "./app.py", "content": "x"}},
        {"name": "write_file", "args": {"file_path": str(tmp_path / "app.py"), "content": "x"}},
    ]
    assert len({runner._call_key(call, tmp_path) for call in same}) == 1


def test_the_call_key_separates_edits_that_carry_different_patches(tmp_path):
    """`edit_file` is in _WATCHED_TOOLS and carries a patch rather than a
    body, so its discriminator is the pair, not `content`."""
    first = {
        "name": "edit_file",
        "args": {"file_path": "/app.py", "old_string": "a", "new_string": "b"},
    }
    second = {
        "name": "edit_file",
        "args": {"file_path": "/app.py", "old_string": "c", "new_string": "d"},
    }
    assert runner._call_key(first, tmp_path) != runner._call_key(second, tmp_path)
    assert runner._call_key(first, tmp_path) == runner._call_key(dict(first), tmp_path)


def test_the_call_key_does_not_hold_the_content_it_keys_on(tmp_path):
    """This key lives in a dict for the life of an invocation. Keeping raw
    file bodies there would hold a file's worth of memory per write."""
    body = "z" * 5000
    key = runner._call_key(
        {"name": "write_file", "args": {"file_path": "/app.py", "content": body}}, tmp_path
    )
    assert body not in key
    assert all(len(part) <= 64 for part in key)


def test_a_read_is_unaffected_by_the_content_term(tmp_path):
    """`read_file` carries no body, so its key is what it was."""
    read = {"name": "read_file", "args": {"file_path": "/app.py", "offset": 100}}
    assert runner._call_key(read, tmp_path) == ("read_file", "app.py", "100", "", "")


def test_three_different_writes_to_one_file_do_not_reach_the_halt(tmp_path):
    """The regression this item exists to close, stated as the guard's own
    arithmetic rather than as a key comparison."""
    repeated: dict[tuple[str, ...], int] = {}
    for body in ("a", "ab", "abc"):
        key = runner._call_key(
            {"name": "write_file", "args": {"file_path": "/app.py", "content": body}}, tmp_path
        )
        repeated[key] = repeated.get(key, 0) + 1
    assert max(repeated.values()) < runner.MAX_REPEATED_CALLS


def test_three_identical_writes_to_one_file_still_reach_the_halt(tmp_path):
    """Half B narrows the halt; it must not disable it."""
    repeated: dict[tuple[str, ...], int] = {}
    for _ in range(3):
        key = runner._call_key(
            {"name": "write_file", "args": {"file_path": "/app.py", "content": "a"}}, tmp_path
        )
        repeated[key] = repeated.get(key, 0) + 1
    assert max(repeated.values()) >= runner.MAX_REPEATED_CALLS


# --- OPEN-91: the runaway bound is denominated in the wrong unit ----------


class FakeClock:
    """A monotonic clock that advances a fixed step per reading.

    `runner` reads the clock through its module-global `time`, so replacing
    that name replaces the clock for this module only -- the real
    `time.monotonic` is untouched everywhere else.
    """

    def __init__(self, step: float = 0.0):
        self.step = step
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


class FakeTrace:
    """Records what Rudra said about itself."""

    def __init__(self):
        self.notices: list[tuple[str, str]] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append((name, payload))

    def feed(self, chunk, state):
        return []


def _glob_stream(count: int):
    """`count` chunks, one interpreter-hunting glob each -- t7's shape."""
    messages = [ai("looking", [call("glob", pattern=f"/usr/bin/python{n}*")]) for n in range(count)]
    return stream_of(*[{"messages": messages[: n + 1]} for n in range(count)])


async def test_a_subagent_stops_after_the_wall_clock_bound(monkeypatch, tmp_path):
    """OPEN-91. Run `fc543fb2b82f`'s t7 made 55 calls over 2,704 s and
    MAX_TOTAL_CALLS = 80 never fired: at 46 s/call that ceiling is 61 minutes
    of sanctioned runaway. The user complains in seconds, so a bound has to
    be denominated in them."""
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=500.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(8))

    result = await run_subagent("coder", "write it", context=make_context(tmp_path))

    assert result.ok is False
    assert "1200s limit" in (result.halted_reason or "")


async def test_the_two_bounds_name_themselves_differently(monkeypatch, tmp_path):
    """ "80 tool calls" is a loop; "20 minutes" is a slow provider or a loop.
    A reader of `ledger.json` has to be able to tell them apart."""
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=0.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(runner.MAX_TOTAL_CALLS + 5))

    result = await run_subagent("coder", "write it", context=make_context(tmp_path))

    assert result.ok is False
    assert "tool calls in one invocation" in (result.halted_reason or "")
    assert "limit" not in (result.halted_reason or "")


async def test_the_wall_clock_bound_is_configurable(monkeypatch, tmp_path):
    """A user on a slow provider is the person best placed to raise it --
    the same argument `[agent] max_fix_attempts` is configurable on."""
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=500.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(8))

    result = await run_subagent(
        "coder", "write it", context=make_context(tmp_path, max_invocation_seconds=100_000)
    )

    assert result.ok is True


async def test_zero_turns_the_wall_clock_bound_off(monkeypatch, tmp_path):
    """0 disables, the way `[agent] max_questions = 0` does. The call ceiling
    still holds -- each bound covers the regime where the other is loose."""
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=5_000.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(4))

    result = await run_subagent(
        "coder", "write it", context=make_context(tmp_path, max_invocation_seconds=0)
    )

    assert result.ok is True


async def test_a_context_with_no_agent_config_still_gets_the_bound(monkeypatch, tmp_path):
    """9b-era callers build a SubagentContext with `cfg=None`, and a guard
    must not be the thing that makes those unbuildable (`_wants_token_stream`
    is read defensively for the same reason)."""
    from rich.console import Console as _Console

    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=500.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(8))

    bare = SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=None,
        console=_Console(quiet=True),
        cfg=None,
        session_id="s1",
    )
    result = await run_subagent("coder", "write it", context=bare)

    assert result.ok is False
    assert "limit" in (result.halted_reason or "")


async def test_the_time_halt_says_it_fired(monkeypatch, tmp_path):
    """TODO.md lesson 5: a new guard is a new silence unless it is wired to
    something. All three guards funnel through `TraceSink.notice`."""
    trace = FakeTrace()
    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=500.0))
    monkeypatch.setattr(runner, "run_with_approvals", _glob_stream(8))

    result = await run_subagent("coder", "write it", context=make_context(tmp_path, trace=trace))

    assert result.ok is False
    assert [name for name, _ in trace.notices] == ["guard"]
    assert "limit" in trace.notices[0][1]


async def test_an_ordinary_invocation_is_nowhere_near_the_time_bound(monkeypatch, tmp_path):
    """The bound must not fire on real work. Run `fc543fb2b82f`'s healthy t1
    coder was one call and 201 s; a hard task at 46 s/call and ~20 calls is
    ~15 minutes."""
    assert runner.MAX_INVOCATION_SECONDS >= 900

    monkeypatch.setattr(runner, "build_agent", lambda spec, context, task="": object())
    monkeypatch.setattr(runner, "time", FakeClock(step=46.0))
    messages = [ai("", [call("read_file", file_path=f"{n}.py")]) for n in range(20)]
    monkeypatch.setattr(
        runner,
        "run_with_approvals",
        stream_of(*[{"messages": messages[: n + 1]} for n in range(len(messages))]),
    )

    result = await run_subagent("coder", "write it", context=make_context(tmp_path))

    assert result.ok is True


def refusal(text="Error: ls has already failed 2 times", call_id="r"):
    """What `RepeatGuardMiddleware` returns instead of running a tool.

    Built here rather than driven through the middleware because this file
    tests the COUNTER, and the marker is the contract between the two.
    `tests/test_repeat_guard.py` holds the other end of it.
    """
    return ToolMessage(
        content=text,
        tool_call_id=call_id,
        name="ls",
        additional_kwargs={REFUSAL_KEY: True},
    )


async def test_refusals_alone_never_halt_the_invocation(monkeypatch, patched):
    """OPEN-94: no tool ran, so nothing failed.

    Run 2cde3406f7d6's coder died at 11.67 s having written nothing, on
    three failures two of which were this message. The guard short-circuits
    to save round trips and every saving was charged toward the halt.
    """
    messages = [ai("try"), *[refusal(call_id=str(n)) for n in range(5)]]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.halted_reason is None


async def test_a_refusal_does_not_clear_a_real_failure_streak(monkeypatch, patched):
    """The other half, and it is needed. A refusal is NO EVENT -- if it
    reset the counter instead, the guard would weaken the runaway bound in
    the opposite direction: two real failures either side of a free
    short-circuit would never meet."""
    messages = [
        ai("try"),
        ToolMessage(content="Error: nope", tool_call_id="1"),
        refusal(call_id="2"),
        ToolMessage(content="Error: nope", tool_call_id="3"),
        ToolMessage(content="Error: nope", tool_call_id="4"),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "consecutive" in result.halted_reason


async def test_a_real_success_still_clears_the_streak(monkeypatch, patched):
    """The no-event branch must not swallow the reset it sits beside."""
    messages = [
        ai("try"),
        ToolMessage(content="Error: nope", tool_call_id="1"),
        ToolMessage(content="fine", tool_call_id="2"),
        ToolMessage(content="Error: nope", tool_call_id="3"),
        ToolMessage(content="Error: nope", tool_call_id="4"),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))
    result = await run_subagent("coder", "write it", context=patched)
    assert result.halted_reason is None


async def test_a_gate_denial_is_still_a_failure(monkeypatch, patched):
    """OPEN-94 §4.3: a gate saying no is exactly the environment signal
    this counter is for. Only Rudra's own short-circuits are exempt."""
    denials = [
        ToolMessage(content="BLOCKED: permission denied", tool_call_id=str(n)) for n in range(3)
    ]
    monkeypatch.setattr(
        runner, "run_with_approvals", stream_of({"messages": [ai("try"), *denials]})
    )
    result = await run_subagent("coder", "write it", context=patched)
    assert result.ok is False
    assert "consecutive" in result.halted_reason


async def test_the_seven_call_sequence_that_killed_run_2cde3406f7d6(monkeypatch, patched):
    """OPEN-94's reproduction, offline: no model, no network, no filesystem.

    Coder invocation 1, in order, from
    `~/.local/state/rudra/runs/test-rudra-d80a39ab/2cde3406f7d6/`. Three
    `ls` calls really failed, one really succeeded, and three were answered
    by the repeat guard without running. Before the fix the last two of
    those refusals were the second and third "consecutive tool failures"
    and the invocation died at 11.67 s having written nothing.
    """
    messages = [
        ai("looking around"),
        ToolMessage(content="Error: Path '/src': path_not_found", tool_call_id="1"),  # real
        ToolMessage(content="['/.DS_Store', '/.mcp.json', '/.rudra/']", tool_call_id="2"),  # real
        ToolMessage(content="Error: Path '/Users': path_not_found", tool_call_id="3"),  # real
        refusal("Already read: `ls` on '.' was answered earlier in this turn", call_id="4"),
        ToolMessage(content="Error: Path '/src': path_not_found", tool_call_id="5"),  # real
        refusal(call_id="6"),
        refusal(call_id="7"),
    ]
    monkeypatch.setattr(runner, "run_with_approvals", stream_of({"messages": messages}))

    result = await run_subagent("coder", "write the page", context=patched)

    assert result.halted_reason is None
    assert result.ok is True
