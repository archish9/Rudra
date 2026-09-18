"""OPEN-103 -- a call to a tool the agent does not have is answered with the route.

Run `f845b496a2aa`'s coder called a tool named `bash` fifteen times and `exit`
twice, and every call came back as langgraph's own tool-node echo:

    Error: bash is not a valid tool, try one of [ls, read_file, write_file,
    edit_file, delete, glob, grep, task, compact_conversation, remember,
    search_memory].

Three of its four invocations died on exactly three of those in a row -- two
of them having already written their files, trying only to check them.

**The coder having no shell is deliberate** (OPEN-36) and nothing here grants
one. The ANSWER was the defect: it said what the tool is not and never what to
do instead, which is OPEN-100's finding one agent down, and it advertised
`task`, which `DelegationGuardMiddleware` withholds from every shipped spec
(OPEN-26, OPEN-101 section 2.4).
"""

from __future__ import annotations

import re

import pytest
from langchain_core.messages import ToolMessage

from rudra.context.usage import RunUsage
from rudra.middleware import ToolRouteMiddleware
from rudra.middleware.tool_route import FINISH_NAMES, SHELL_NAMES, TOOL_ROUTE_NOTICE
from rudra.subagents.registry import REGISTRY
from rudra.trace.stream import is_rudra_refusal, message_is_error

COMMAND = "cd /tmp/p && python -m pytest tests/unit/test_models.py -v"
"""One of the three commands the run's coder actually sent."""


def _granted(name: str) -> frozenset[str]:
    """What a shipped spec holds -- the reading `subagents/build.py` uses."""
    spec = REGISTRY[name]
    return frozenset(spec.fs_tools) | frozenset(spec.rudra_tools)


def _route(name: str, **kwargs) -> ToolRouteMiddleware:
    return ToolRouteMiddleware(REGISTRY[name].role, granted=_granted(name), **kwargs)


class FakeTrace:
    def __init__(self):
        self.notices: list[tuple[str, str]] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append((name, payload))


class Request:
    """The shape langgraph hands a wrapper. `tool` is None for a name that is
    not registered -- pinned upstream in tests/test_deepagents_contract.py."""

    def __init__(self, name: str, *, registered: bool = False, **args):
        self.tool_call = {"name": name, "args": args, "id": "c1"}
        self.tool = object() if registered else None
        self.state: dict = {}
        self.runtime = None


def _handled(mw, request):
    """Run the sync wrapper, recording whether the real tool node was reached."""
    reached = []

    def handler(req):
        reached.append(req)
        return "ran"

    return mw.wrap_tool_call(request, handler), reached


def _named(content: str) -> set[str]:
    """Every identifier the text quotes as a tool name."""
    return set(re.findall(r"`([a-z_][a-z0-9_]*)`", content))


# --- it answers instead of echoing ------------------------------------------


def test_the_coder_calling_bash_is_answered_with_the_gate_not_a_tool_list():
    result, reached = _handled(_route("coder"), Request("bash", command=COMMAND))

    assert reached == []
    assert isinstance(result, ToolMessage)
    assert result.content.startswith("REJECTED:")
    assert COMMAND in result.content
    assert "verification gate" in result.content
    assert "execute" not in result.content


def test_the_tester_calling_bash_is_pointed_at_execute():
    """The tester DOES hold a shell. Telling it there is none would be false,
    which is `MachinePathMiddleware`'s `has_shell` rule one middleware over."""
    result, reached = _handled(_route("tester"), Request("bash", command=COMMAND))

    assert reached == []
    assert "execute" in _named(result.content)
    assert "run_tests" in _named(result.content)
    assert "verification gate" not in result.content


def test_a_read_only_spec_is_not_promised_a_gate_that_never_calls_it_back():
    """The reviewer is not in the fix loop, so "the gate calls you again" is
    false for it -- and it holds no write tool to fix anything with."""
    result, reached = _handled(_route("reviewer"), Request("bash", command=COMMAND))

    assert reached == []
    assert "calls you again" not in result.content
    assert "read_file" in _named(result.content)
    assert not {"edit_file", "write_file"} & _named(result.content)


def test_exit_is_answered_with_how_to_finish():
    """`_FINISH_RULES`' answer, delivered at the moment it is needed (OPEN-42)."""
    result, reached = _handled(_route("coder"), Request("exit"))

    assert reached == []
    assert result.content.startswith("REJECTED:")
    assert "call no tool" in result.content


@pytest.mark.parametrize("name", sorted(REGISTRY))
@pytest.mark.parametrize("called", sorted(SHELL_NAMES | FINISH_NAMES))
def test_the_route_names_no_tool_the_spec_lacks(name, called):
    """OPEN-15's rule, asserted as a rule rather than as a string: every tool
    the answer names is one this spec was granted -- and `task`, which every
    spec has registered and none may use, is never among them."""
    granted = _granted(name)
    result, reached = _handled(_route(name), Request(called, command=COMMAND))

    if called in granted:
        # A name the spec holds is never answered, whatever `request.tool` says.
        assert reached
        return
    named = _named(result.content) - {called}
    assert named <= granted, named - granted
    assert "task" not in named


# --- it declines everything it cannot route ---------------------------------


def test_an_unknown_name_still_gets_the_plain_tool_list():
    """A wrong route is worse than the echo (`planner_write.py`'s own limit),
    so a name outside both closed sets reaches langgraph unchanged."""
    result, reached = _handled(_route("coder"), Request("frobnicate", x=1))

    assert result == "ran"
    assert len(reached) == 1


def test_a_registered_tool_is_never_intercepted():
    """Structural, not by name: an MCP server could register a real `run`."""
    mw = _route("coder")

    for called in ("bash", "run", "exit"):
        result, reached = _handled(mw, Request(called, registered=True, command=COMMAND))
        assert result == "ran", called
        assert len(reached) == 1, called


def test_a_name_the_spec_holds_is_passed_through():
    """Defensive: if `granted` says the spec has it, this middleware has no
    business claiming it does not."""
    result, reached = _handled(_route("tester"), Request("execute", command=COMMAND))

    assert result == "ran"
    assert len(reached) == 1


async def test_the_async_path_answers_identically():
    mw = _route("coder")

    async def handler(req):
        raise AssertionError("the tool node must not be reached")

    result = await mw.awrap_tool_call(Request("bash", command=COMMAND), handler)

    assert result.content == _handled(mw, Request("bash", command=COMMAND))[0].content


def test_an_enormous_command_is_not_echoed_whole():
    """The model's argument is quoted so it can see which call was refused,
    not replayed into its context at any size."""
    command = "python -c " + "x" * 5000

    result, _ = _handled(_route("coder"), Request("bash", command=command))

    assert len(result.content) < 2000


# --- counted by the failure counter, deliberately ---------------------------


def test_the_route_is_still_counted_as_a_tool_failure():
    """**The owner's decision, 2026-09-14, and the opposite of the plan's
    section 5.1.** The plan said a `REJECTED:` lead keeps `runner.py`'s counter
    from halting on it; measured, that is false for every refusal Rudra has --
    `message_is_error` reads `status` first (`trace/stream.py:129`), which is
    OPEN-118. Kept counted on purpose: the gate still runs after a halt
    (`loop/engine.py:653-657`), so a model that ignores the route three times
    loses little, while an uncounted one is bounded only by 80 calls."""
    result, _ = _handled(_route("coder"), Request("bash", command=COMMAND))

    assert result.status == "error"
    assert message_is_error(result) is True
    assert is_rudra_refusal(result) is False


# --- the diagnostic (CLAUDE.md 8a) ------------------------------------------


def test_a_route_is_counted_and_announced():
    usage = RunUsage()
    trace = FakeTrace()

    _handled(_route("coder", usage=usage, trace=trace), Request("bash", command=COMMAND))

    assert usage.as_dict()["coder"]["tool_routes_answered"] == 1
    assert trace.notices[0][0] == TOOL_ROUTE_NOTICE
    assert "bash" in trace.notices[0][1]


def test_the_plain_echo_is_neither_counted_nor_announced():
    usage = RunUsage()
    trace = FakeTrace()

    _handled(_route("coder", usage=usage, trace=trace), Request("frobnicate"))

    assert "coder" not in usage.as_dict()
    assert trace.notices == []


def test_bookkeeping_failure_does_not_break_the_route():
    class Exploding:
        def notice(self, *a, **k):
            raise RuntimeError("no")

        def record_tool_route_answered(self, *a, **k):
            raise RuntimeError("no")

    mw = _route("coder", usage=Exploding(), trace=Exploding())

    result, reached = _handled(mw, Request("bash", command=COMMAND))

    assert reached == []
    assert result.content.startswith("REJECTED:")


def test_the_middleware_registers_no_tools_of_its_own():
    """`AgentMiddleware.tools` is the list a middleware CONTRIBUTES, and
    langchain registers every entry (`planner_write.py`'s trap). Hence
    `granted`."""
    mw = _route("coder")

    assert list(getattr(mw, "tools", [])) == []
    assert mw.granted == _granted("coder")


# --- through a real tool node: the offline reproduction ---------------------


def _through_tool_node(mw, name: str, **args) -> str:
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import MessagesState, StateGraph
    from langgraph.prebuilt.tool_node import ToolNode

    @tool
    def ls(path: str) -> str:
        """List a directory."""
        return "ok"

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([ls], wrap_tool_call=mw.wrap_tool_call))
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")

    call = {"name": name, "args": args, "id": "1"}
    out = graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call])]})
    return out["messages"][-1].content


def test_through_a_real_tool_node_the_echo_is_replaced():
    content = _through_tool_node(_route("coder"), "bash", command=COMMAND)

    assert content.startswith("REJECTED:")
    assert "is not a valid tool" not in content


def test_through_a_real_tool_node_an_unknown_name_keeps_the_echo():
    content = _through_tool_node(_route("coder"), "frobnicate")

    assert "is not a valid tool" in content


# --- OPEN-126: the writer's route, shared with the repeat guard --------------


def test_the_no_shell_writer_route_is_byte_identical_after_the_extraction():
    """OPEN-126 moves one sentence out so the repeat guard can say it too. The
    route OPEN-103 shipped must not change by a byte."""
    from rudra.middleware import tool_route

    assert tool_route._NO_SHELL_WRITER == (
        "REJECTED: there is no `{name}` tool, and no shell of any kind in this "
        "agent. {command} was not run, and nothing here can run it.\n\n"
        "You do not need to. When you stop, a verification gate runs the "
        "project's linter, type checker and full test suite, and calls you again "
        "with the exact failure text if anything fails -- stopping IS how you find "
        "out whether your work is correct.\n\n"
        "{hands}Do not call `{name}` again, or any other name for a shell."
    )


def test_settled_write_route_names_only_what_the_agent_holds():
    from rudra.middleware.tool_route import GATE_RUNS_ON_STOP, settled_write_route

    coder = frozenset({"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"})
    tester = coder | {"execute", "run_tests"}

    for_coder = settled_write_route(coder)
    assert "Writing a file does not run it" in for_coder
    assert GATE_RUNS_ON_STOP in for_coder
    assert "call no tool" in for_coder
    assert "execute" not in for_coder and "run_tests" not in for_coder  # OPEN-15

    for_tester = settled_write_route(tester)
    assert "`run_tests`" in for_tester
    assert "nothing in this agent can run" not in for_tester
    assert "call no tool" in for_tester

    assert "`execute`" in settled_write_route(coder | {"execute"})
    assert settled_write_route(frozenset()) == ""


def test_settled_write_route_is_byte_identical_after_sharing_its_body():
    """OPEN-134 moves the route's body into `_settled_route` so a read refusal
    can say it too. The write route's text must not change by a byte."""
    from rudra.middleware.tool_route import settled_write_route

    coder = frozenset({"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"})

    assert settled_write_route(coder) == (
        "Writing a file does not run it, and nothing in this agent can run one -- nor does "
        "anything need to. When you stop, a verification gate runs the project's linter, type "
        "checker and full test suite, and calls you again with the exact failure text if "
        "anything fails -- stopping IS how you find out whether your work is correct. If your "
        "work is finished, reply with one or two lines and call no tool."
    )
    assert settled_write_route(coder | {"execute"}) == (
        "To run a command, call `execute`. If your work is finished, reply with one or two "
        "lines and call no tool."
    )
    assert settled_write_route(frozenset()) == ""
