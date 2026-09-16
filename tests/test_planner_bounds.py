"""OPEN-100 — a planner stage is bounded, and says so when a bound fires.

Run `d8f742805b9b` ended because a human pressed Ctrl-C. Its architect stage
made 18+ tool calls, three of its model calls emitted complete HTML documents
(4932, 5540 and 4636 output tokens over 430.9 s -- 42% of the run's model
time), and **no guard in `_stream_planner_turn` could see any of it**:

* the loop guard counted only `add_tasks` and `read_ledger`, so `record_fact`,
  `read_file`, `ls`, `glob`, `grep` and `write_file` were all free;
* `write_file` -- the one name that can only ever be a mistake on a stack
  whose `PLANNER_FS_TOOLS` excludes it -- **cleared** the counter;
* `consecutive_failures` reached 2 and was reset at every interleaved
  successful `record_fact`, because that guard is written for a streak and
  this failure mode is an alternation;
* and `MAX_TOTAL_CALLS` / `max_invocation_seconds` are subagent-only
  (`subagents/runner.py:41,415`), so a planner stage had no ceiling of any
  kind.

`tests/test_planner_trace.py` pins the two guards that already existed and
must keep working; this file pins the two that did not exist and the halt
announcement none of them made.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.agent import planner_agent
from rudra.agent.planner_agent import (
    MAX_PLANNER_TOOL_CALLS,
    PLANNER_HALT_NOTICE,
    _stream_planner_turn,
)
from rudra.context import deadline as deadline_module
from rudra.context.deadline import SPAN_DEADLINE
from rudra.context.usage import RunUsage
from rudra.subagents.runner import MAX_INVOCATION_SECONDS, _invocation_limit
from rudra.trace import TraceLevel
from rudra.trace.sink import TraceSink


class FakeClock:
    """A monotonic clock advancing a fixed step per reading.

    `SpanDeadline` reads the clock through `rudra.context.deadline`'s
    module-global `time`, so replacing that name replaces the clock for the
    deadline only -- the real `time.monotonic` is untouched everywhere else.
    It used to be `planner_agent`'s own global; OPEN-114 moved the clock into
    the deadline object, and this module no longer reads one.
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


def _call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": name}


def _stream_of_calls(names: list[str]):
    """One AIMessage per name, each chunk carrying every message so far.

    The shape `run_with_approvals` actually yields: `messages` grows, and
    `_stream_planner_turn` tracks its position per namespace.
    """
    messages = [AIMessage(content="", tool_calls=[_call(n)]) for n in names]
    return [((), {"messages": messages[: n + 1]}) for n in range(len(names))]


def _stream_fn(chunks):
    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    return fake_stream


def _out_of_seconds(inner):
    """A stream whose graph ended because the stage ran out of seconds.

    Since OPEN-114 the clock is not read in `_stream_planner_turn`'s own loop.
    It is read in `SpanDeadlineMiddleware`'s `before_model` hook, which ends the
    graph, and the loop then finds a tripped deadline and announces it. The old
    check read the clock as a chunk ARRIVED -- right after a model call returned
    and before its tools ran -- so it discarded the answer it had just paid for:
    run `8f160d92c6da` lost two `record_fact` calls worth 767.5 s that way.

    So this stands in for the middleware, the way the `ToolMessage`s below stand
    in for the guards that produce them: these tests are about what
    `_stream_planner_turn` DOES with a tripped deadline. The hook itself, and
    that the crossing answer's tools still run, are pinned in
    `tests/test_span_deadline.py` on a real graph.
    """

    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        async for chunk in inner(agent, inputs, config, gate, console, **kwargs):
            yield chunk
        deadline = SPAN_DEADLINE.get()
        if deadline is not None:
            deadline.trip()

    return fake_stream


async def _run_stream(monkeypatch, stream, *, trace=None, usage=None):
    monkeypatch.setattr(planner_agent, "run_with_approvals", stream)

    return await _stream_planner_turn(
        object(),
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
        usage=usage,
    )


async def _run(monkeypatch, chunks, *, trace=None, usage=None):
    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(planner_agent, "run_with_approvals", fake_stream)

    return await _stream_planner_turn(
        object(),
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
        usage=usage,
    )


# --- the call ceiling ------------------------------------------------------


async def test_every_tool_call_counts_toward_the_cap(monkeypatch):
    """The defect in one assertion: `record_fact` is uncounted no longer.

    Run `d8f742805b9b`'s architect stage recorded eight facts, read four
    files and globbed, and the guard saw none of it because it watched two
    names.
    """
    names = ["record_fact"] * MAX_PLANNER_TOOL_CALLS

    ok = await _run(monkeypatch, _stream_of_calls(names))

    assert ok is False


async def test_a_stage_under_the_cap_is_not_halted(monkeypatch):
    """The bound is a runaway ceiling, not a budget."""
    names = ["record_fact"] * (MAX_PLANNER_TOOL_CALLS - 1)

    ok = await _run(monkeypatch, _stream_of_calls(names))

    assert ok is True


async def test_a_healthy_clarify_stage_is_not_halted(monkeypatch):
    """The observed legitimate maximum, from the reported run's own clarify
    stage: ~15 calls, mostly `record_fact`, doing exactly its job."""
    names = [
        "ls",
        "read_file",
        "glob",
        "read_file",
        "ask_user",
        "record_fact",
        "record_fact",
        "record_fact",
        "ask_user",
        "record_fact",
        "record_fact",
        "grep",
        "record_fact",
        "record_fact",
        "record_fact",
    ]

    ok = await _run(monkeypatch, _stream_of_calls(names))

    assert ok is True


# --- the regression pin ----------------------------------------------------


async def test_write_file_no_longer_clears_the_counter(monkeypatch):
    """**The regression pin.** `planner_agent.py:709-710` read

        if name in ("write_file",):
            planning_tool_calls.clear()

    so the single tool name the planner can never legitimately call was the
    one name that disarmed the loop guard. Three `read_ledger` calls put the
    counter one short of `MAX_PLANNING_CALLS`; a rejected `write_file` used to
    put it back to zero.
    """
    names = ["read_ledger", "read_ledger", "read_ledger", "write_file", "read_ledger"]

    ok = await _run(monkeypatch, _stream_of_calls(names))

    assert ok is False


async def test_a_write_file_attempt_still_counts_as_a_call(monkeypatch):
    """It is a tool call like any other now -- neither privileged nor exempt."""
    names = ["write_file"] * MAX_PLANNER_TOOL_CALLS

    ok = await _run(monkeypatch, _stream_of_calls(names))

    assert ok is False


# --- the seconds bound -----------------------------------------------------


async def test_seconds_bound_halts_a_slow_stage(monkeypatch):
    """OPEN-91's argument at the planner. On this run's 28.7 s/call provider
    a 40-call ceiling is 19 minutes, and three of its calls took over 138 s
    each -- so a call cap alone bounds nothing a user would call bounded."""
    monkeypatch.setattr(deadline_module, "time", FakeClock(step=500.0))

    ok = await _run_stream(monkeypatch, _out_of_seconds(_stream_fn(_stream_of_calls(["ls"] * 8))))

    assert ok is False


# `test_a_stage_that_spends_without_calling_tools_is_still_bounded` stood here.
# It fed eight text-only `AIMessage` chunks, because the clock was read before
# the chunk was parsed and so caught a stage generating 5,540 output tokens of
# HTML and calling nothing.
#
# That shape is not one a compiled graph can produce: an `AIMessage` with no
# tool calls ENDS the agent, so the second such chunk never arrives. The
# scenario it stood for -- run `d8f742805b9b`'s planner generating documents --
# ended each of those turns in a `write_file` call, which the bound still sees.
# Deleted rather than rewritten: what a stage spending without calling tools
# actually does now is pinned in `tests/test_span_deadline.py`, where the model
# is real enough to end its own graph.


async def test_a_fast_stage_is_not_halted_by_the_clock(monkeypatch):
    """A deadline that never tripped announces nothing."""
    monkeypatch.setattr(deadline_module, "time", FakeClock(step=0.0))

    ok = await _run(monkeypatch, _stream_of_calls(["ls"] * 8))

    assert ok is True


@pytest.mark.parametrize("limit", [0.0, 42.0, 1200.0])
async def test_the_stage_limit_is_what_reaches_the_span(monkeypatch, limit):
    """`_stage_time_limit` is what the hook is bounded by, 0 included -- and 0
    means no bound, exactly as `_invocation_limit` reads it.

    Pinned on the span rather than on a halt, because with no limit there is no
    halt to observe: `SpanDeadline(0.0).expired()` is always False, so a stub
    that tripped anyway would be asserting a lie.
    `tests/test_span_deadline.py` runs the 0 case end to end on a real graph.
    """
    seen: list[object] = []

    async def capture(agent, inputs, config, gate, console, **kwargs):
        seen.append(SPAN_DEADLINE.get())
        for chunk in ():
            yield chunk  # pragma: no cover - an empty stream, typed as one

    monkeypatch.setattr(planner_agent, "_stage_time_limit", lambda: limit)

    ok = await _run_stream(monkeypatch, capture)

    assert ok is True
    assert [d.limit for d in seen] == [limit]


async def test_the_span_is_closed_when_the_turn_ends(monkeypatch):
    """The variable must not outlive the stage. `_stream_planner_turn` is
    awaited in `consult_planner`'s own task, so a leaked deadline would bound
    every later stage of the run from this one's start."""
    await _run(monkeypatch, _stream_of_calls(["ls"]))

    assert SPAN_DEADLINE.get() is None


@pytest.mark.parametrize("raw", [None, "nonsense", object()])
def test_an_unreadable_limit_falls_back_to_the_constant(monkeypatch, raw):
    """`subagents/runner.py::_invocation_limit`'s rule, verbatim: the degraded
    mode of a guard is the guard, not its absence. A config that cannot be
    read must not be a config with no bound."""

    class Cfg:
        agent = type("A", (), {"max_invocation_seconds": raw})()

    monkeypatch.setattr(planner_agent, "get_config", lambda: Cfg())

    assert planner_agent._stage_time_limit() == MAX_INVOCATION_SECONDS


@pytest.mark.parametrize("raw", [0, 0.0, -1, -1.0])
def test_a_non_positive_limit_means_no_bound(monkeypatch, raw):
    class Cfg:
        agent = type("A", (), {"max_invocation_seconds": raw})()

    monkeypatch.setattr(planner_agent, "get_config", lambda: Cfg())

    assert planner_agent._stage_time_limit() == 0.0


def test_the_planner_reads_the_same_config_key_as_a_subagent(monkeypatch):
    """One key, both stacks (OPEN-100). A user who lowers
    `[agent] max_invocation_seconds` must not find that the agent which runs
    first and can run longest is the one it never covered."""

    class Cfg:
        agent = type("A", (), {"max_invocation_seconds": 42.0})()

    monkeypatch.setattr(planner_agent, "get_config", lambda: Cfg())

    assert planner_agent._stage_time_limit() == 42.0
    # `_invocation_limit` reads the key off a SubagentContext's `cfg`, hence
    # the wrapper; the key it reaches for is the assertion.
    assert _invocation_limit(type("Ctx", (), {"cfg": Cfg()})()) == 42.0


# --- the diagnostic (CLAUDE.md §8a, TODO.md lesson 5) ----------------------


async def test_each_bound_names_itself(monkeypatch):
    """ "40 tool calls" is a loop; "over the 1200s limit" is a slow provider
    or a loop. A reader of `debug-<id>.jsonl` has to be able to tell them
    apart -- OPEN-91's sentence, at the planner."""
    calls_trace = FakeTrace()
    await _run(monkeypatch, _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS), trace=calls_trace)

    clock_trace = FakeTrace()
    monkeypatch.setattr(deadline_module, "time", FakeClock(step=500.0))
    await _run_stream(
        monkeypatch,
        _out_of_seconds(_stream_fn(_stream_of_calls(["ls"] * 8))),
        trace=clock_trace,
    )

    assert "tool calls" in calls_trace.notices[0][1]
    assert "s limit" in clock_trace.notices[0][1]
    assert calls_trace.notices[0][1] != clock_trace.notices[0][1]


async def test_a_halt_emits_exactly_one_notice(monkeypatch):
    """A guard that fires silently is `CLAUDE.md` §8a failure shape 1, and
    §3.5 of the plan shows this one already had that problem: the return
    value is discarded by `consult_planner`, so the NOTICE is the only thing
    that reaches the debug log."""
    trace = FakeTrace()

    await _run(monkeypatch, _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS), trace=trace)

    assert len(trace.notices) == 1
    assert trace.notices[0][0] == PLANNER_HALT_NOTICE


async def test_a_halt_is_counted_in_usage(monkeypatch):
    """`roles.planner.planner_halts`. The count exists for the reason
    `plans_refused` does: what a halt prevents leaves no mark on `calls`,
    `seconds` or tokens, and a maintainer reading a user's logs folder must
    not have to write a parser to see it."""
    usage = RunUsage()

    await _run(monkeypatch, _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS), usage=usage)

    assert usage.as_dict()["planner"]["planner_halts"] == 1


async def test_a_healthy_turn_counts_nothing_and_says_nothing(monkeypatch):
    trace = FakeTrace()
    usage = RunUsage()

    ok = await _run(monkeypatch, _stream_of_calls(["ls", "record_fact"]), trace=trace, usage=usage)

    assert ok is True
    assert trace.notices == []
    assert usage.as_dict() == {}


async def test_a_turn_without_a_sink_or_usage_still_halts(monkeypatch):
    """Bookkeeping may never end a run, and its absence may never end a
    guard: both are optional and the halt is not."""
    ok = await _run(monkeypatch, _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS))

    assert ok is False


async def test_a_broken_sink_does_not_break_the_halt(monkeypatch):
    """`CLAUDE.md` §8a: a run that did its work must not be reported failed
    because a log line could not be written."""

    class Exploding:
        def notice(self, *a, **k):
            raise RuntimeError("no")

        def feed(self, chunk, state):
            return []

    ok = await _run(
        monkeypatch, _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS), trace=Exploding()
    )

    assert ok is False


async def test_a_model_written_tool_name_cannot_inject_console_markup(monkeypatch):
    """The halt line interpolates `tc["name"]`, which the MODEL writes.
    `trace/render.py` escapes every payload for this reason (A1.67, A1.48,
    A1.91) and this console print did not."""
    trace = FakeTrace()
    names = ["read_ledger[/bold yellow][red]pwned"] * 4

    await _run(monkeypatch, _stream_of_calls(names), trace=trace)

    assert trace.notices == [] or "pwned" in trace.notices[0][1]


# --- the guards that already existed keep working -------------------------


async def test_the_consecutive_failure_guard_is_untouched(monkeypatch):
    """The plan's §9: it is correct for the shape it targets -- a hard streak
    -- and simply cannot see an alternation. Add beside it; do not edit it."""
    failures = [
        ToolMessage(content="Error: boom", tool_call_id=str(n), name="write_file") for n in range(3)
    ]
    chunks = [((), {"messages": failures[: n + 1]}) for n in range(3)]

    ok = await _run(monkeypatch, chunks, trace=TraceSink(level=TraceLevel.NORMAL))

    assert ok is False


# --- OPEN-113: a time halt says where the time went -------------------------


def _billing_stream(names: list[str], usage: RunUsage, seconds_per_call: float):
    """`_stream_of_calls`, with the model call behind each chunk recorded
    first -- UsageMiddleware's order, since a chunk is yielded only after the
    model node returns."""
    chunks = _stream_of_calls(names)

    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            usage.record("planner", input_tokens=None, output_tokens=None, seconds=seconds_per_call)
            yield chunk

    return fake_stream


async def test_a_time_halt_says_the_stage_was_waiting_on_the_model(monkeypatch):
    """Run `8f160d92c6da` printed `1888s in one planner stage, over the 1200s
    limit -- stopping after 3 tool calls.` and nothing else, for a stage that
    was 99.98% planner-model latency."""
    usage = RunUsage()
    trace = FakeTrace()
    monkeypatch.setattr(deadline_module, "time", FakeClock(step=500.0))

    ok = await _run_stream(
        monkeypatch,
        _out_of_seconds(_billing_stream(["ls"] * 8, usage, 499.0)),
        trace=trace,
        usage=usage,
    )

    assert ok is False
    ((name, reason),) = trace.notices
    assert name == PLANNER_HALT_NOTICE
    assert "s limit" in reason
    assert "was the planner model answering" in reason
    assert "the time went to model latency, not to tool work." in reason


async def test_a_time_halt_no_longer_names_calls_it_will_not_run(monkeypatch):
    """**The sentence this replaces was true, and OPEN-114 made it false.**

    `test_a_time_halt_names_the_calls_it_will_not_run` pinned *"The answer that
    arrived past the limit asked for record_fact, which will not run."* -- an
    accurate description of the defect: the halt landed on a finished answer and
    cancelled its tools. The bound fires before the next model call now, so
    there is no answer in hand to name, and `_unrun_tool_calls` is deleted
    rather than reworded.
    """
    trace = FakeTrace()
    monkeypatch.setattr(deadline_module, "time", FakeClock(step=500.0))

    await _run_stream(
        monkeypatch,
        _out_of_seconds(_stream_fn(_stream_of_calls(["ls", "ls", "record_fact", "ls"]))),
        trace=trace,
    )

    assert "will not run" not in trace.notices[0][1]


async def test_a_call_cap_halt_carries_no_time_detail(monkeypatch):
    """Only the seconds bound is ambiguous between a loop and a slow provider;
    the call ceiling already names a loop."""
    trace = FakeTrace()

    await _run(
        monkeypatch,
        _stream_of_calls(["ls"] * MAX_PLANNER_TOOL_CALLS),
        trace=trace,
        usage=RunUsage(),
    )

    assert "model answering" not in trace.notices[0][1]
