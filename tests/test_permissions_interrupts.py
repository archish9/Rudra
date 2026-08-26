"""interrupt_on wiring and the resume loop (Step 7 spec §6.1, §6.2).

The async generator is driven with asyncio.run() from sync tests rather
than adding a pytest-asyncio dev dependency for one case — this repo
dropped six dependencies in A2.16 and should not gain one back cheaply.
"""

from __future__ import annotations

import asyncio

import pytest
from deepagents import create_deep_agent
from deepagents.backends.local_shell import LocalShellBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from rudra.permissions.interrupts import build_interrupt_on
from rudra.permissions.rules import MUTATING_TOOLS, PermissionEngine


class OneExecuteModel(BaseChatModel):
    command: str = "echo gated"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "one-execute"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "execute", "args": {"command": self.command}, "id": "c1"}],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def make_engine(tmp_path, mode="ask", allow=()):
    return PermissionEngine(
        mode=mode, allow=allow, deny=(), floor_disable=(), project_root=tmp_path
    )


def agent_for(tmp_path, mode="ask", allow=()):
    engine = make_engine(tmp_path, mode, allow)
    agent = create_deep_agent(
        model=OneExecuteModel(),
        backend=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True),
        interrupt_on=build_interrupt_on(engine),
        checkpointer=InMemorySaver(),
    )
    return agent, engine


def test_every_mutating_tool_gets_an_interrupt_config(tmp_path):
    assert set(build_interrupt_on(make_engine(tmp_path))) == set(MUTATING_TOOLS)


def test_read_only_tools_get_no_interrupt_config(tmp_path):
    assert "read_file" not in build_interrupt_on(make_engine(tmp_path))


def test_each_config_allows_approve_and_reject(tmp_path):
    for config in build_interrupt_on(make_engine(tmp_path)).values():
        assert config["allowed_decisions"] == ["approve", "reject"]


def test_each_predicate_is_bound_to_its_own_tool(tmp_path):
    """A closure over the loop variable would make them all see one tool."""
    engine = make_engine(tmp_path, mode="auto")
    configs = build_interrupt_on(engine)
    assert len({id(config["when"]) for config in configs.values()}) == len(configs)


def test_an_ask_decision_actually_interrupts(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t1"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    assert agent.get_state(config).interrupts, "mode=ask did not pause the graph"


def test_an_allowed_call_does_not_interrupt(tmp_path):
    agent, _ = agent_for(tmp_path, allow=("execute:echo*",))
    config = {"configurable": {"thread_id": "t2"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    assert not agent.get_state(config).interrupts


def test_auto_mode_does_not_interrupt(tmp_path):
    agent, _ = agent_for(tmp_path, mode="auto")
    config = {"configurable": {"thread_id": "t3"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    assert not agent.get_state(config).interrupts


def test_resume_with_approve_runs_the_command(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t4"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert "gated" in tool_messages[0].content


def test_resume_with_reject_returns_the_message(tmp_path):
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t5"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "no thanks"}]}), config
    )
    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert tool_messages[0].content == "no thanks"


def test_the_resume_payload_must_be_a_dict_with_decisions(tmp_path):
    """A bare list raises TypeError from inside the middleware. Pin the shape."""
    agent, _ = agent_for(tmp_path)
    config = {"configurable": {"thread_id": "t6"}}
    agent.invoke({"messages": [{"role": "user", "content": "go"}]}, config)
    with pytest.raises(TypeError):
        agent.invoke(Command(resume=[{"type": "approve"}]), config)


def _drain(agent, inputs, config, gate, console):
    """Run the async generator to completion, collecting its chunks."""
    from rudra.permissions.approval import run_with_approvals

    async def go():
        chunks = []
        async for chunk in run_with_approvals(agent, inputs, config, gate, console):
            chunks.append(chunk)
        return chunks

    return asyncio.run(go())


def test_run_with_approvals_prompts_then_resumes(tmp_path):
    from rich.console import Console

    from rudra.config.loader import build_config
    from rudra.permissions import build_gate

    agent, engine = agent_for(tmp_path)
    gate = build_gate(build_config(tmp_path), tmp_path)
    gate.engine = engine
    gate.reader = lambda: "a"

    config = {"configurable": {"thread_id": "t7"}}
    with (tmp_path / "out.txt").open("w", encoding="utf-8") as handle:
        chunks = _drain(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            config,
            gate,
            Console(file=handle, width=100),
        )

    assert chunks, "run_with_approvals yielded nothing"
    assert not agent.get_state(config).interrupts, "the interrupt was never resumed"
    messages = agent.get_state(config).values["messages"]
    tool_messages = [m for m in messages if type(m).__name__ == "ToolMessage"]
    assert "gated" in tool_messages[0].content


def test_run_with_approvals_rejects_when_the_user_says_no(tmp_path):
    from rich.console import Console

    from rudra.config.loader import build_config
    from rudra.permissions import build_gate

    agent, engine = agent_for(tmp_path)
    gate = build_gate(build_config(tmp_path), tmp_path)
    gate.engine = engine
    gate.reader = lambda: "r"

    config = {"configurable": {"thread_id": "t8"}}
    with (tmp_path / "out.txt").open("w", encoding="utf-8") as handle:
        _drain(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            config,
            gate,
            Console(file=handle, width=100),
        )

    messages = agent.get_state(config).values["messages"]
    tool_messages = [m for m in messages if type(m).__name__ == "ToolMessage"]
    assert "rejected" in tool_messages[0].content.lower()


def test_run_with_approvals_without_a_gate_just_streams(tmp_path):
    """A caller that has not built a gate must still get its chunks."""
    from rich.console import Console

    agent, _ = agent_for(tmp_path, mode="auto")
    config = {"configurable": {"thread_id": "t9"}}
    with (tmp_path / "out.txt").open("w", encoding="utf-8") as handle:
        chunks = _drain(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            config,
            None,
            Console(file=handle, width=100),
        )
    assert chunks


def test_run_with_approvals_works_with_the_real_async_checkpointer(tmp_path):
    """Production uses AsyncSqliteSaver, which forbids sync get_state.

    Found by the Step 7 acceptance run: every test above uses InMemorySaver,
    which tolerates both interfaces, so a synchronous get_state() passed the
    whole suite and then raised InvalidStateError on the first real run.
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from rich.console import Console

    from rudra.config.loader import build_config, reset_config
    from rudra.permissions import build_gate
    from rudra.permissions.approval import run_with_approvals

    async def go():
        conn = await aiosqlite.connect(str(tmp_path / "cp.db"))
        checkpointer = AsyncSqliteSaver(conn=conn)
        await checkpointer.setup()
        engine = make_engine(tmp_path)
        agent = create_deep_agent(
            model=OneExecuteModel(),
            backend=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True),
            interrupt_on=build_interrupt_on(engine),
            checkpointer=checkpointer,
        )
        reset_config()
        gate = build_gate(build_config(tmp_path), tmp_path)
        gate.engine = engine
        gate.reader = lambda: "a"

        config = {"configurable": {"thread_id": "async-1"}}
        with (tmp_path / "out.txt").open("w", encoding="utf-8") as handle:
            console = Console(file=handle, width=100)
            async for _ in run_with_approvals(
                agent, {"messages": [{"role": "user", "content": "go"}]}, config, gate, console
            ):
                pass
        state = await agent.aget_state(config)
        await conn.close()
        return state

    state = asyncio.run(go())
    tool_messages = [m for m in state.values["messages"] if type(m).__name__ == "ToolMessage"]
    assert "gated" in tool_messages[0].content


# --- OPEN-11: the approval prompt on the shared selector ---


def test_the_approval_prompt_offers_all_five_actions():
    """Approve, reject, always, auto-accept, diff.

    Four until 2026-08-26, when OPEN-30 added `auto`. Order is asserted, not
    just membership: `diff` stays last because it is the only non-terminal
    row, and `auto` sits after `always` because it is the wider of the two.
    """
    from rudra.permissions.approval import _APPROVAL_CHOICES

    assert [choice.value for choice in _APPROVAL_CHOICES] == [
        "approve",
        "reject",
        "always",
        "auto",
        "diff",
    ]


def test_every_approval_hotkey_is_distinct():
    """`run_inline` binds each hotkey individually, so a duplicate would
    silently give one row's key to another row's action."""
    from rudra.permissions.approval import _APPROVAL_CHOICES

    hotkeys = [choice.hotkey for choice in _APPROVAL_CHOICES]
    assert len(hotkeys) == len(set(hotkeys))
    assert None not in hotkeys


def test_approve_and_always_keep_distinct_hotkeys():
    """`a` approves once, `A` grants for the rest of the run. Collapsing
    them would silently widen a grant the user never gave."""
    from rudra.permissions.approval import _APPROVAL_CHOICES

    hotkeys = {choice.value: choice.hotkey for choice in _APPROVAL_CHOICES}
    assert hotkeys["approve"] == "a"
    assert hotkeys["always"] == "A"
    assert hotkeys["reject"] == "r"
    # Not a letter, and not next to any of them: `!` is the widest answer
    # in the menu and the one a mistyped key must never reach (OPEN-30).
    assert hotkeys["auto"] == "!"
    assert hotkeys["diff"] == "d"


def _decide_one(tmp_path, reader):
    """One gated write through decide_action_requests, with an injected
    reader -- the seam approval.py:3 documents."""
    import io

    from rich.console import Console

    from rudra.config.loader import build_config
    from rudra.permissions import build_gate
    from rudra.permissions.approval import decide_action_requests

    gate = build_gate(build_config(tmp_path), tmp_path)
    return decide_action_requests(
        [{"name": "write_file", "args": {"file_path": "notes.txt", "content": "hi"}}],
        engine=gate.engine,
        grants=gate.grants,
        audit=gate.audit,
        console=Console(file=io.StringIO(), width=100),
        project_root=tmp_path,
        mode="ask",
        reader=reader,
    )


def test_pressing_a_approves(tmp_path):
    assert _decide_one(tmp_path, lambda: "a")[0]["type"] == "approve"


def test_pressing_r_rejects(tmp_path):
    assert _decide_one(tmp_path, lambda: "r")[0]["type"] == "reject"


def test_cancelling_the_approval_prompt_rejects(tmp_path):
    """Never approve. An interrupt at a write prompt is the one moment
    where guessing wrong puts bytes on the user's disk."""

    def reader():
        raise KeyboardInterrupt

    assert _decide_one(tmp_path, reader)[0]["type"] == "reject"


def test_eof_at_the_approval_prompt_rejects(tmp_path):
    def reader():
        raise EOFError

    assert _decide_one(tmp_path, reader)[0]["type"] == "reject"


def test_d_shows_the_diff_then_the_next_answer_decides(tmp_path):
    """`d` is not terminal: it re-renders with the full diff and asks
    again, which is the loop this function has always had."""
    answers = iter(["d", "a"])
    assert _decide_one(tmp_path, lambda: next(answers))[0]["type"] == "approve"


def test_pressing_capital_a_grants_for_the_rest_of_the_run(tmp_path):
    import io

    from rich.console import Console

    from rudra.config.loader import build_config
    from rudra.permissions import build_gate
    from rudra.permissions.approval import decide_action_requests

    gate = build_gate(build_config(tmp_path), tmp_path)
    request = {"name": "write_file", "args": {"file_path": "notes.txt", "content": "hi"}}
    console = Console(file=io.StringIO(), width=100)

    def _run(reader):
        return decide_action_requests(
            [request],
            engine=gate.engine,
            grants=gate.grants,
            audit=gate.audit,
            console=console,
            project_root=tmp_path,
            mode="ask",
            reader=reader,
        )

    assert _run(lambda: "A")[0]["type"] == "approve"

    # The second call must not prompt at all -- the grant now covers it.
    def explode():
        raise AssertionError("the grant should have made this call silent")

    assert _run(explode)[0]["type"] == "approve"
