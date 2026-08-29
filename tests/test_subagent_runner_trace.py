"""The coder is no longer invisible (Step 15a, C9.1).

run_subagent consumed chunks to drive its guards and rendered NONE of
them, so the coder, tester and reviewer produced no output at all while
they did the run's actual work -- the user watched `→ status` lines and
learned what happened only from the final panel.

This asserts the events reach the sink AND that every guard still fires,
because the loop was extended, not replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.subagents.runner import SubagentContext, run_subagent
from rudra.trace import TraceKind, TraceLevel
from rudra.trace.sink import TraceSink


def _context(sink, monkeypatch, chunks):
    """A SubagentContext whose stream yields `chunks`, with no model."""

    async def fake_stream(agent, inputs, config, gate, console, **kwargs):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr("rudra.subagents.runner.run_with_approvals", fake_stream)
    monkeypatch.setattr(
        "rudra.subagents.runner.build_agent", lambda spec, context, task="": object()
    )

    return SubagentContext(
        project_path=Path("."),
        backend=None,
        gate=None,
        console=Console(quiet=True),
        cfg=None,
        session_id="s1",
        trace=sink,
    )


async def test_a_coder_run_emits_its_tool_calls_to_the_sink(monkeypatch):
    seen = []
    sink = TraceSink(level=TraceLevel.NORMAL, consumers=[seen.append])
    call = {"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"}
    chunks = [
        ((), {"messages": [AIMessage(content="", tool_calls=[call])]}),
        (
            (),
            {
                "messages": [
                    AIMessage(content="", tool_calls=[call]),
                    ToolMessage(content="Wrote a.py", tool_call_id="1", name="write_file"),
                ]
            },
        ),
    ]
    context = _context(sink, monkeypatch, chunks)

    result = await run_subagent("coder", "write a.py", context=context)

    assert result.ok
    assert [event.kind for event in seen] == [TraceKind.TOOL_CALL, TraceKind.TOOL_RESULT]
    assert all(event.role == "coder" for event in seen)


async def test_a_subagents_messages_do_not_hide_the_parents(monkeypatch):
    """A1.20 on the path that matters: the coder can reach `task`."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    parent = [AIMessage(content="p1"), AIMessage(content="p2")]
    chunks = [
        ((), {"messages": parent[:1]}),
        (("task:1",), {"messages": [AIMessage(content="c1")]}),
        ((), {"messages": parent}),
    ]
    context = _context(sink, monkeypatch, chunks)

    await run_subagent("coder", "write a.py", context=context)

    assert [event.payload for event in seen] == ["p1", "c1", "p2"]


async def test_the_repeat_guard_still_halts_with_tracing_on(monkeypatch):
    call = {"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"}
    messages = [AIMessage(content="", tool_calls=[call]) for _ in range(6)]
    chunks = [((), {"messages": messages[: n + 1]}) for n in range(6)]
    context = _context(TraceSink(level=TraceLevel.NORMAL), monkeypatch, chunks)

    result = await run_subagent("coder", "write a.py", context=context)

    assert not result.ok
    assert "repeated" in (result.halted_reason or "")


async def test_the_consecutive_failure_guard_still_halts_with_tracing_on(monkeypatch):
    failures = [
        ToolMessage(content="Error: boom", tool_call_id=str(n), name="execute") for n in range(3)
    ]
    chunks = [((), {"messages": failures[: n + 1]}) for n in range(3)]
    context = _context(TraceSink(level=TraceLevel.NORMAL), monkeypatch, chunks)

    result = await run_subagent("coder", "write a.py", context=context)

    assert not result.ok
    assert "consecutive tool failures" in (result.halted_reason or "")


async def test_a_run_without_a_sink_still_works(monkeypatch):
    """Every 9b-era test builds a context with no trace at all."""
    chunks = [((), {"messages": [AIMessage(content="done")]})]
    context = _context(None, monkeypatch, chunks)

    result = await run_subagent("coder", "write a.py", context=context)

    assert result.ok
    assert result.text == "done"


@pytest.mark.parametrize("name", ["coder", "tester", "reviewer"])
async def test_every_working_subagent_is_traced_under_its_own_role(monkeypatch, name):
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    chunks = [((), {"messages": [AIMessage(content="hello")]})]
    context = _context(sink, monkeypatch, chunks)

    await run_subagent(name, "do it", context=context)

    assert [event.role for event in seen] == [name]


async def test_tool_calls_do_not_corrupt_the_namespace_bookkeeping(monkeypatch):
    """Regression: the namespace counter and the repeat guard both wanted a
    variable called `key`. Sharing it filed a namespace's position under a
    (tool, target) pair, so the parent's position was lost the moment it
    called a tool."""
    seen = []
    sink = TraceSink(level=TraceLevel.VERBOSE, consumers=[seen.append])
    call = {"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"}
    parent = [AIMessage(content="", tool_calls=[call]), AIMessage(content="p2")]
    chunks = [
        ((), {"messages": parent[:1]}),
        (("task:1",), {"messages": [AIMessage(content="c1")]}),
        ((), {"messages": parent}),
    ]
    context = _context(sink, monkeypatch, chunks)

    await run_subagent("coder", "write a.py", context=context)

    assert [event.payload for event in seen] == ["{'file_path': 'a.py'}", "c1", "p2"]


async def test_a_guard_halt_is_emitted_as_a_notice(monkeypatch):
    """OPEN-44. `trace.feed` forwards graph CHUNKS, and a halt is Rudra's
    own decision rather than a chunk -- so the debug log, which is meant to
    be the complete record (CLAUDE.md §3), held nothing about six killed
    coder invocations in run bf6be7525991. Grepping it for `stopping`,
    `halted` and `repeated` returned nothing at all.
    """
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET)
    sink.add_recorder(seen.append)
    call = {"name": "write_file", "args": {"file_path": "/DONE"}, "id": "1"}
    messages = [AIMessage(content="", tool_calls=[call]) for _ in range(6)]
    chunks = [((), {"messages": messages[: n + 1]}) for n in range(6)]
    context = _context(sink, monkeypatch, chunks)

    result = await run_subagent("coder", "write a.py", context=context)

    assert not result.ok
    notices = [event for event in seen if event.kind is TraceKind.NOTICE]
    assert len(notices) == 1, "one notice per halt, and a halt ends the invocation"
    assert notices[0].role == "coder"
    # `DONE`, not `/DONE`: the halt names the resolved path since OPEN-50.
    assert "'DONE'" in notices[0].payload
    assert notices[0].payload == result.halted_reason, "the trace and the caller agree"


async def test_the_notice_carries_the_namespace_it_fired_in(monkeypatch):
    """A delegate's events read `role: coder, namespace: [...]` and are not
    the coder's own -- the distinction OPEN-37 turned on."""
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET)
    sink.add_recorder(seen.append)
    call = {"name": "write_file", "args": {"file_path": "a.py"}, "id": "1"}
    messages = [AIMessage(content="", tool_calls=[call]) for _ in range(6)]
    chunks = [(("task:1",), {"messages": messages[: n + 1]}) for n in range(6)]
    context = _context(sink, monkeypatch, chunks)

    await run_subagent("coder", "write a.py", context=context)

    notices = [event for event in seen if event.kind is TraceKind.NOTICE]
    assert notices[0].namespace == ("task:1",)


async def test_a_total_call_halt_is_emitted_too(monkeypatch):
    """Three guards can halt an invocation; the notice is not keyed to one."""
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET)
    sink.add_recorder(seen.append)
    calls = [{"name": "glob", "args": {"pattern": f"**/{n}"}, "id": str(n)} for n in range(90)]
    messages = [AIMessage(content="", tool_calls=[call]) for call in calls]
    chunks = [((), {"messages": messages[: n + 1]}) for n in range(90)]
    context = _context(sink, monkeypatch, chunks)

    result = await run_subagent("coder", "look around", context=context)

    assert "tool calls" in (result.halted_reason or "")
    notices = [event for event in seen if event.kind is TraceKind.NOTICE]
    assert [event.payload for event in notices] == [result.halted_reason]


async def test_a_clean_run_emits_no_notice(monkeypatch):
    seen = []
    sink = TraceSink(level=TraceLevel.QUIET)
    sink.add_recorder(seen.append)
    chunks = [((), {"messages": [AIMessage(content="done")]})]
    context = _context(sink, monkeypatch, chunks)

    await run_subagent("coder", "write a.py", context=context)

    assert [event for event in seen if event.kind is TraceKind.NOTICE] == []
