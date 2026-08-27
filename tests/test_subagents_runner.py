"""run_subagent's result mapping and its per-invocation guards."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from rudra.subagents import runner
from rudra.subagents.runner import SubagentContext, SubagentResult, run_subagent


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict


def make_context(tmp_path):
    return SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=None,
        console=Console(quiet=True),
        cfg=FakeCfg(compat=FakeCompat(), tools=FakeTools(), models={}),
        session_id="s1",
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


def test_the_call_key_separates_read_windows():
    """`read_file` pages, so the window is part of what makes a call
    distinct. Without it the key for page 1 and page 3 of one file are the
    same string and the guard counts them as one call made three times."""
    whole = {"name": "read_file", "args": {"file_path": "/a.py"}}
    page_two = {"name": "read_file", "args": {"file_path": "/a.py", "offset": 100}}
    page_two_short = {
        "name": "read_file",
        "args": {"file_path": "/a.py", "offset": 100, "limit": 50},
    }

    assert runner._call_key(whole) != runner._call_key(page_two)
    assert runner._call_key(page_two) != runner._call_key(page_two_short)
    # The identical call is still identical -- that is what the guard is for.
    assert runner._call_key(page_two) == runner._call_key(dict(page_two))
    # A tool with no window is unaffected: same name, same target, same key.
    write = {"name": "write_file", "args": {"file_path": "/a.py", "content": "x"}}
    assert runner._call_key(write) == runner._call_key(
        {"name": "write_file", "args": {"file_path": "/a.py", "content": "y"}}
    )


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
    assert "/a.py" in (result.halted_reason or ""), "the halt must still name the file"


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
