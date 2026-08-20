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
    monkeypatch.setattr(runner, "build_agent", lambda spec, context: object())
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
    def boom(spec, context):
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
