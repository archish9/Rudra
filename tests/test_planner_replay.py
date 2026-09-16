"""OPEN-121 — a re-consult's guards must not count the consults before it.

A stage's consults share one thread (`planner_agent.py:1200`, OPEN-65 relies
on it) and the planner is streamed `stream_mode="values"`, so every chunk
carries the thread's whole message list. `_stream_planner_turn` started each
turn at zero (`:928-932`) and walked from message 0 (`:991`), charging this
turn for every tool call earlier consults had made.

Run `a04f89bd2ed6`, measured from its archived debug log by classifying each
planner record against the consult's first `model_call`:

    consult 3  +3485.5   13 replayed calls (2 add_tasks) + 4 live (2 add_tasks)
                         -> halt "'add_tasks' called 4x" with TWO live calls
    consult 4  +4153.1   17 replayed (4 add_tasks) + 0 live  -> halt before any answer
    consult 5  +4335.7   18 replayed (4 add_tasks) + 0 live  -> halt, zero model calls

History only grows, so from consult 4 the run could not answer a block at all.
`trace.feed` (`:988`) reads the same replayed messages, which is why that run's
debug log holds five copies of the stage's `user` record and eight records
naming `edit_file`/`write_file` for ONE refused write.

`tests/test_planner_bounds.py` pins the bounds themselves; this file pins which
messages they are allowed to count.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from rich.console import Console

from rudra.agent import planner_agent
from rudra.agent.planner_agent import (
    MAX_PLANNER_TOOL_CALLS,
    MAX_PLANNING_CALLS,
    _stream_planner_turn,
)
from rudra.context.usage import RunUsage
from rudra.trace import TraceKind, TraceLevel
from rudra.trace.sink import TraceSink

THE_ASK = "The task ledger as it stands:\n\nt1 [done]\n\nTask t7 failed..."


def _call(name: str, n: int) -> dict:
    return {"name": name, "args": {}, "id": f"{name}-{n}"}


def _earlier_consult(names: list[str], *, at: int = 0) -> list[Any]:
    """One earlier consult of this stage: its user message, then its answers."""
    msgs: list[Any] = [HumanMessage(content=f"an earlier consult {at}")]
    msgs += [
        AIMessage(content="", tool_calls=[_call(n, at * 100 + i)]) for i, n in enumerate(names)
    ]
    return msgs


def _history(consults: list[list[str]]) -> list[Any]:
    msgs: list[Any] = []
    for at, names in enumerate(consults):
        msgs += _earlier_consult(names, at=at)
    return msgs


def _reconsult(history: list[Any], live: list[Any]) -> list[Any]:
    """The chunks a re-consult really yields.

    Measured against langgraph: a turn's first chunk is the input state --
    everything the thread already held, followed by this turn's own
    HumanMessage, and no new AIMessage yet. Each later chunk appends.
    """
    base = [*history, HumanMessage(content=THE_ASK)]
    chunks = [((), {"messages": list(base)})]
    msgs = list(base)
    for msg in live:
        msgs.append(msg)
        chunks.append(((), {"messages": list(msgs)}))
    return chunks


def _calls(names: list[str]) -> list[Any]:
    return [AIMessage(content="", tool_calls=[_call(n, 900 + i)]) for i, n in enumerate(names)]


def _errors(n: int) -> list[Any]:
    return [
        ToolMessage(content="Error: boom", tool_call_id=f"e{i}", name="read_file") for i in range(n)
    ]


async def _run(monkeypatch, chunks, *, trace=None, usage=None) -> bool:
    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(planner_agent, "run_with_approvals", fake_stream)

    return await _stream_planner_turn(
        object(),
        THE_ASK,
        thread_id="sess-breakdown",
        gate=None,
        console=Console(quiet=True),
        trace=trace,
        usage=usage,
    )


def _halts(usage: RunUsage) -> int:
    return usage.as_dict().get("planner", {}).get("planner_halts", 0)


# --- the item ---------------------------------------------------------------


async def test_a_re_consult_is_not_halted_by_its_predecessors_add_tasks(monkeypatch):
    """Run `a04f89bd2ed6`'s consult 4, in one assertion: a thread holding four
    earlier `add_tasks` halted before the model was asked anything."""
    usage = RunUsage()
    chunks = _reconsult(_history([["add_tasks"]] * MAX_PLANNING_CALLS), _calls(["add_tasks"]))

    ok = await _run(monkeypatch, chunks, usage=usage)

    assert ok is True
    assert _halts(usage) == 0


async def test_a_re_consult_that_really_loops_is_still_halted(monkeypatch):
    """The bound is unchanged -- it is the messages counted against it that
    were wrong. Four live `add_tasks` in ONE turn is what it targets."""
    usage = RunUsage()
    chunks = _reconsult(
        _history([["add_tasks"]] * MAX_PLANNING_CALLS),
        _calls(["add_tasks"] * MAX_PLANNING_CALLS),
    )

    ok = await _run(monkeypatch, chunks, usage=usage)

    assert ok is False
    assert _halts(usage) == 1


async def test_history_does_not_count_toward_the_total_ceiling(monkeypatch):
    """`MAX_PLANNER_TOOL_CALLS` is a runaway ceiling for ONE stage. The same
    arithmetic as the planning guard: 40 historical calls halt the next
    consult on its first chunk."""
    chunks = _reconsult(_history([["ls"] * MAX_PLANNER_TOOL_CALLS]), _calls(["ls"]))

    assert await _run(monkeypatch, chunks) is True


async def test_live_calls_still_reach_the_total_ceiling(monkeypatch):
    chunks = _reconsult(_history([["ls"] * 5]), _calls(["ls"] * MAX_PLANNER_TOOL_CALLS))

    assert await _run(monkeypatch, chunks) is False


async def test_historical_failures_do_not_count_toward_the_streak(monkeypatch):
    """Consults 3 and 4 replayed 12 and 16 `tool_result` records, so the
    consecutive-failure guard was reachable from history too."""
    history = [*_history([["read_file"] * 3]), *_errors(3)]

    assert await _run(monkeypatch, _reconsult(history, _calls(["ls"]))) is True


async def test_live_failures_still_halt_the_turn(monkeypatch):
    assert await _run(monkeypatch, _reconsult(_history([["ls"]]), _errors(3))) is False


# --- the trace (CLAUDE.md §8a: the debug log is the support channel) --------


async def test_history_is_not_rewritten_into_the_debug_log(monkeypatch):
    """Eight planner records named `edit_file`/`write_file` in run
    `a04f89bd2ed6` -- four `tool_call` and four `tool_error` -- for ONE refused
    write (`planner_writes_refused: 1`), replayed by the three consults after
    it. Run18 checklist row 23 read that as eight attempts."""
    events: list[Any] = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[events.append])
    chunks = _reconsult(_history([["edit_file"], ["add_tasks"]]), _calls(["add_tasks"]))

    await _run(monkeypatch, chunks, trace=sink)

    assert [e.name for e in events if e.kind is TraceKind.TOOL_CALL] == ["add_tasks"]


async def test_this_turns_own_question_is_still_recorded(monkeypatch):
    """The baseline stops at this turn's own HumanMessage rather than past it:
    a reader of `debug-<id>.jsonl` finds each consult by its `user` record, and
    that run's log held five copies of it -- one per consult, not none."""
    events: list[Any] = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[events.append])
    chunks = _reconsult(_history([["add_tasks"], ["add_tasks"]]), _calls(["ls"]))

    await _run(monkeypatch, chunks, trace=sink)

    user_events = [e for e in events if e.kind is TraceKind.USER]
    assert len(user_events) == 1
    assert THE_ASK in user_events[0].payload


# --- what must NOT change ---------------------------------------------------


async def test_the_first_consult_of_a_stage_counts_every_call(monkeypatch):
    """An initial consult's first chunk is `[HumanMessage]` alone: there is no
    history, the baseline is 0, and every call counts exactly as before."""
    chunks = _reconsult([], _calls(["add_tasks"] * MAX_PLANNING_CALLS))

    assert await _run(monkeypatch, chunks) is False


async def test_a_stream_without_a_user_message_counts_from_zero(monkeypatch):
    """`tests/test_planner_bounds.py::_stream_of_calls` builds chunk 0 as a bare
    `AIMessage` carrying a LIVE call. A baseline of `len(messages)` would skip
    it and turn those tests green for the wrong reason, so a chunk with no
    HumanMessage must count from zero."""
    msgs = _calls(["add_tasks"] * MAX_PLANNING_CALLS)
    chunks = [((), {"messages": msgs[: n + 1]}) for n in range(len(msgs))]

    assert await _run(monkeypatch, chunks) is False


async def test_an_approval_resume_does_not_re_baseline(monkeypatch):
    """`run_with_approvals` re-enters `_stream_with_retry` inside one turn when
    an interrupt is answered (`permissions/approval.py:407-425`), and the
    resumed stream's first chunk repeats the state at the interrupt. The
    baseline is taken once per namespace, so that chunk must not reset the
    position to this turn's HumanMessage and re-walk what was already counted.
    """
    history = _history([["add_tasks"]] * MAX_PLANNING_CALLS)
    chunks = _reconsult(history, _calls(["add_tasks", "read_file"]))
    # The resumed stream opens by re-yielding the chunk the interrupt paused on.
    chunks.append(chunks[-1])
    chunks.append(((), {"messages": [*chunks[-1][1]["messages"], *_calls(["read_ledger"])]}))

    assert await _run(monkeypatch, chunks) is True


# `test_a_time_halt_on_a_first_chunk_names_no_historical_call` stood here, with
# a `_FakeClock` beside it. It pinned that the baseline is seeded before the
# loop's clock check, so a time halt could not report an earlier consult's
# `add_tasks` as a call that "will not run".
#
# OPEN-114 deleted both halves of what it pinned: the clock check left the loop
# for a `before_model` hook, and the sentence naming the calls a halt discards
# went with it, because the bound no longer lands on an answer. There is nothing
# left for the ordering to protect -- the baseline is now seeded above
# `trace.feed`, which is the reason it was always really there.
#
# The baseline's own behaviour is pinned by every test above; the time bound is
# pinned in `tests/test_span_deadline.py`, on a real graph.


# --- the boundary itself ----------------------------------------------------


def test_the_baseline_is_the_last_user_message():
    """The anchor is the LAST HumanMessage: it is the one `consult_planner`
    just sent, and everything before it belongs to an earlier consult."""
    baseline = planner_agent._history_baseline

    assert baseline([]) == 0
    assert baseline(_calls(["ls", "ls"])) == 0
    assert baseline([HumanMessage(content="ask")]) == 0
    assert baseline([*_calls(["ls"]), HumanMessage(content="ask")]) == 1
    assert baseline([HumanMessage(content="a"), *_calls(["ls"]), HumanMessage(content="b")]) == 2
