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
