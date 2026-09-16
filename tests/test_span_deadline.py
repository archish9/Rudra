"""OPEN-114 — a time bound must not discard the answer it already waited for.

Both time bounds read the clock at the top of the chunk loop, and a `values`
chunk carrying an `AIMessage` with tool calls is yielded **after the model node
returns and before the tool node has run**. So the bound fired at the one moment
where stopping destroys the most: immediately after paying for an answer,
immediately before using it. Run `8f160d92c6da` lost two finished `record_fact`
calls that way -- 767.5 s of model time -- and its clarify stage ended with zero
facts having produced one.

The bound now lives in a `before_model` hook that jumps to `end`, so it fires
after the previous answer's tools ran and before the next call is paid for.
Checked against that run: same halt times (1888 s, 1467 s), both facts kept.

This file pins the mechanism -- the deadline object, the hook, and the two span
owners. `tests/test_planner_bounds.py` and `tests/test_subagents_runner.py` pin
the announcement each span makes when it trips.
"""

from __future__ import annotations

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from rich.console import Console

from rudra.agent import planner_agent
from rudra.agent.planner_agent import PLANNER_HALT_NOTICE, _stream_planner_turn
from rudra.context import deadline as deadline_module
from rudra.context.deadline import SPAN_DEADLINE, SpanDeadline, span_deadline
from rudra.context.usage import RunUsage
from rudra.middleware.span_deadline import SpanDeadlineMiddleware


class StepClock:
    """A monotonic clock that moves only when something says it did.

    `FakeClock` in `tests/test_planner_bounds.py` advances per *reading*, which
    cannot express this item's shape: the limit has to pass DURING a model call,
    so that the check before that call is under it and the check after it is
    over. Here the fake model advances the clock, and every reading in between
    agrees with itself.
    """

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTrace:
    """Records what Rudra said about itself."""

    def __init__(self) -> None:
        self.notices: list[tuple[str, str]] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append((name, payload))

    def feed(self, chunk, state):
        return []


# --- the deadline object, with no graph anywhere near it -------------------


def test_a_deadline_is_not_expired_before_its_limit():
    clock = StepClock()
    deadline = SpanDeadline(100.0, clock=clock.monotonic)

    clock.advance(99.0)

    assert deadline.expired() is False
    assert deadline.tripped_at is None


def test_a_deadline_is_expired_at_its_limit():
    clock = StepClock()
    deadline = SpanDeadline(100.0, clock=clock.monotonic)

    clock.advance(100.0)

    assert deadline.expired() is True


def test_a_zero_limit_never_expires():
    """0 disables, the way `[agent] max_questions = 0` does -- and the way both
    `_stage_time_limit` and `_invocation_limit` already report it."""
    clock = StepClock()
    deadline = SpanDeadline(0.0, clock=clock.monotonic)

    clock.advance(10_000.0)

    assert deadline.expired() is False


def test_tripping_records_when_it_happened():
    """The halt sentence quotes the elapsed seconds, and it must quote the
    moment the bound fired rather than whenever the announcement is built."""
    clock = StepClock()
    deadline = SpanDeadline(100.0, clock=clock.monotonic)

    clock.advance(500.0)
    deadline.trip()
    clock.advance(500.0)

    assert deadline.tripped_at == pytest.approx(500.0)


def test_tripping_twice_keeps_the_first_moment():
    clock = StepClock()
    deadline = SpanDeadline(100.0, clock=clock.monotonic)

    clock.advance(500.0)
    deadline.trip()
    clock.advance(500.0)
    deadline.trip()

    assert deadline.tripped_at == pytest.approx(500.0)


def test_the_context_var_is_empty_by_default():
    """Inert unless a span sets one: every agent built outside a run -- every
    compat test among them -- streams with no deadline at all."""
    assert SPAN_DEADLINE.get() is None


def test_the_span_context_manager_resets_its_token():
    with span_deadline(100.0) as deadline:
        assert SPAN_DEADLINE.get() is deadline

    assert SPAN_DEADLINE.get() is None


def test_the_span_context_manager_resets_after_an_exception():
    """A halt is not the only way a span ends. `run_subagent` reports a raised
    exception rather than raising it, and a leaked deadline would then bound
    the NEXT invocation from the previous one's start."""
    with pytest.raises(RuntimeError), span_deadline(100.0):
        raise RuntimeError("boom")

    assert SPAN_DEADLINE.get() is None


# --- the hook, on a real graph --------------------------------------------


ran: list[str] = []


@tool
def record_fact(key: str) -> str:
    """Record a fact."""
    ran.append(key)
    return "recorded"


class SlowModel(BaseChatModel):
    """Answers with a tool call, and takes `seconds` of the clock to do it.

    The clock is advanced INSIDE the model call, which is the whole point:
    that is where run `8f160d92c6da` crossed its limit -- one 733.7 s call
    plus a 218.1 s retry -- and where the old bound then threw the answer away.
    """

    clock: object = None
    seconds: float = 500.0
    calls: int = 0
    answers: int = 1

    @property
    def _llm_type(self) -> str:
        return "fake-slow-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        self.clock.advance(self.seconds)
        if self.calls <= self.answers:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_fact",
                        "args": {"key": f"k{self.calls}"},
                        "id": str(self.calls),
                    }
                ],
            )
        else:
            msg = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def clock(monkeypatch):
    """A clock the fake model drives, installed where `SpanDeadline` reads it.

    `SpanDeadline` takes an injectable clock, but the two span owners build
    their own, so a test that wants to control their clock replaces the module
    global they default to.
    """
    fake = StepClock()
    monkeypatch.setattr(deadline_module, "time", fake)
    ran.clear()
    return fake


def _agent(clock, *, answers: int = 1, seconds: float = 500.0, middleware=None):
    model = SlowModel(clock=clock, seconds=seconds, answers=answers)
    stack = [SpanDeadlineMiddleware()] if middleware is None else middleware
    return model, create_agent(model=model, tools=[record_fact], middleware=stack)


async def _stream(agent, **kwargs):
    async for _ in agent.astream(
        {"messages": [{"role": "user", "content": "go"}]},
        {"configurable": {"thread_id": "t"}},
        stream_mode="values",
        subgraphs=True,
        **kwargs,
    ):
        pass


async def test_the_hook_reads_the_deadline_the_span_owner_set(clock):
    """The plumbing claim, proved rather than assumed (plan §5.1).

    Planner stage agents are compiled in `agent/main_agent.py` and subagents in
    `subagents/build.py`, while the span starts later in `_stream_planner_turn`
    / `run_subagent`. A ContextVar set in the task that iterates `astream` is
    copied into the asyncio tasks langgraph creates for its nodes, so neither
    builder needs a new parameter.
    """
    _, agent = _agent(clock, answers=1)

    with span_deadline(100.0, clock=clock.monotonic) as deadline:
        await _stream(agent)

    assert deadline.tripped_at == pytest.approx(500.0)


async def test_the_hook_reaches_a_deepagents_stack_too(clock):
    """The stack Rudra actually builds. `create_deep_agent` delegates to
    `create_agent` (`deepagents/graph.py`), so the conditional edge
    `can_jump_to` establishes is the same one -- but that is upstream's
    arrangement rather than Rudra's, and it is what both registrations depend
    on."""
    from deepagents import create_deep_agent

    model = SlowModel(clock=clock, seconds=500.0, answers=1)
    agent = create_deep_agent(
        model=model, tools=[record_fact], middleware=[SpanDeadlineMiddleware()]
    )

    with span_deadline(100.0, clock=clock.monotonic) as deadline:
        await _stream(agent)

    assert deadline.tripped_at is not None
    assert ran == ["k1"]
    assert model.calls == 1


async def test_a_span_that_overruns_but_finishes_on_its_own_is_not_halted(clock):
    """**A deliberate change of meaning, and it is the honest one.**

    The bound fires before a model call, so a span whose last answer ends the
    agent -- an `AIMessage` with no tool calls -- is never asked again and never
    trips, however far past the limit it went. The old check read the clock on
    that final chunk and halted.

    Nothing was stopped, so nothing is announced: `roles.planner.planner_halts`
    counts guards that ACTED, and reporting a halt for work that completed would
    make the number a reader of `debug-<id>.jsonl` cannot trust. The overrun is
    still visible where overruns belong -- `usage.json`'s seconds.
    """
    model, agent = _agent(clock, answers=0)

    with span_deadline(100.0, clock=clock.monotonic) as deadline:
        await _stream(agent)

    assert model.calls == 1
    assert deadline.elapsed() > 100.0
    assert deadline.tripped_at is None


async def test_a_zero_limit_leaves_a_slow_span_alone_on_a_real_graph(clock):
    """0 disables, the way `[agent] max_questions = 0` does -- end to end, not
    only on the deadline object. The call ceilings still hold; each bound covers
    the regime where the other is loose."""
    model, agent = _agent(clock, answers=3, seconds=5_000.0)

    with span_deadline(0.0, clock=clock.monotonic) as deadline:
        await _stream(agent)

    assert deadline.tripped_at is None
    assert model.calls == 4
    assert ran == ["k1", "k2", "k3"]


async def test_the_answer_that_crossed_the_limit_still_runs(clock):
    """**The item, in one assertion.** Run `8f160d92c6da`'s clarify stage waited
    733.7 s plus a 218.1 s retry for a `record_fact project_type` and then threw
    it away unexecuted; `facts.json` holds only `layout`.

    The limit passes during the first model call, so: the check before it is
    under the limit and the call is issued, its tool RUNS, and the check before
    the second call halts. One model call, one fact recorded.
    """
    model, agent = _agent(clock, answers=2)

    with span_deadline(100.0, clock=clock.monotonic):
        await _stream(agent)

    assert ran == ["k1"]
    assert model.calls == 1


async def test_nothing_is_issued_after_the_limit(clock):
    """The other half: the bound still bounds. A span whose deadline has passed
    pays for no further model call -- that is what makes this a fix rather than
    a widening."""
    model, agent = _agent(clock, answers=5)

    with span_deadline(100.0, clock=clock.monotonic):
        await _stream(agent)

    assert model.calls == 1


async def test_a_span_under_its_limit_runs_to_completion(clock):
    model, agent = _agent(clock, answers=1, seconds=1.0)

    with span_deadline(10_000.0, clock=clock.monotonic) as deadline:
        await _stream(agent)

    assert deadline.tripped_at is None
    assert ran == ["k1"]
    assert model.calls == 2


async def test_the_middleware_is_inert_without_a_deadline(clock):
    """Every existing test builds a stack with no span around it, and the
    compat suites build agents with no run at all. No deadline, no jump."""
    model, agent = _agent(clock, answers=1)

    await _stream(agent)

    assert SPAN_DEADLINE.get() is None
    assert ran == ["k1"]
    assert model.calls == 2


# --- through the planner's own span ---------------------------------------


async def test_a_planner_stage_keeps_the_fact_its_last_call_asked_for(clock, monkeypatch):
    """`_stream_planner_turn` end to end, on a real graph.

    Before this, the halt fired on the chunk carrying the answer and `break`
    closed the generator langgraph's step loop lives inside -- so the tool node
    was never scheduled.
    """
    monkeypatch.setattr(planner_agent, "_stage_time_limit", lambda: 100.0)
    model, agent = _agent(clock, answers=2)
    trace = FakeTrace()
    usage = RunUsage()

    ok = await _stream_planner_turn(
        agent,
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
        usage=usage,
    )

    assert ok is False
    assert ran == ["k1"]
    assert model.calls == 1
    assert usage.as_dict()["planner"]["planner_halts"] == 1
    ((name, reason),) = trace.notices
    assert name == PLANNER_HALT_NOTICE
    assert "s limit" in reason


async def test_a_planner_halt_no_longer_claims_a_call_will_not_run(clock, monkeypatch):
    """OPEN-113's sentence became false the moment the halt stopped discarding
    the call: it fires before the next model call now, so there is no answer in
    hand to describe. It is deleted rather than reworded."""
    monkeypatch.setattr(planner_agent, "_stage_time_limit", lambda: 100.0)
    _, agent = _agent(clock, answers=2)
    trace = FakeTrace()

    await _stream_planner_turn(
        agent,
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
    )

    assert "will not run" not in trace.notices[0][1]


async def test_a_planner_stage_under_its_limit_is_not_halted(clock, monkeypatch):
    monkeypatch.setattr(planner_agent, "_stage_time_limit", lambda: 10_000.0)
    _, agent = _agent(clock, answers=1, seconds=1.0)
    trace = FakeTrace()
    usage = RunUsage()

    ok = await _stream_planner_turn(
        agent,
        "plan it",
        thread_id="t",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
        usage=usage,
    )

    assert ok is True
    assert trace.notices == []
    assert usage.as_dict() == {}
