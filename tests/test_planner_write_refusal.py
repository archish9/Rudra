"""OPEN-100 option C — a planner `write_file` is answered with the route.

Option A stops the bleeding: a runaway stage now terminates itself. This is
the other half. Run `d8f742805b9b`'s architect stage reached `write_file`
twice and was answered, both times, by langgraph's tool-list echo:

    Error: write_file is not a valid tool, try one of
      [ls, read_file, glob, grep, task, record_fact]

The model read it, understood it, and said so in its own words at debug line
140 -- *"I notice there's no `write_file` tool available in this environment.
Let me check what tools are available and provide the HTML content to the
user."* -- then invented `task {'pattern': 'create file'}`. It was told what
it could not do and never told what to do instead, which is OPEN-95's
argument one agent up.

**The refusal must name only tools the stage actually holds.** `_tools_for_stage`
gives clarify `record_fact`/`ask_user`, architect `record_fact`, and breakdown
`add_tasks`/`drop_task`/`read_ledger` and NO `record_fact` -- so a fixed
sentence naming `record_fact` would advertise an absent tool to the breakdown
stage, which is OPEN-15 and is the very defect OPEN-101 is filed on.
"""

from __future__ import annotations

import pytest

from rudra.agent import planner_agent
from rudra.context.usage import RunUsage
from rudra.middleware import PlannerWriteMiddleware
from rudra.middleware.planner_write import PLANNER_WRITE_NOTICE
from rudra.trace.stream import is_rudra_refusal, message_is_error


class FakeTrace:
    def __init__(self):
        self.notices: list[tuple[str, str]] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append((name, payload))


class Request:
    """The shape langgraph hands a wrapper. `tool` is None for a name that is
    not registered -- pinned upstream in tests/test_deepagents_contract.py."""

    def __init__(self, name: str, **args):
        self.tool_call = {"name": name, "args": args, "id": "c1"}
        self.tool = None
        self.state: dict = {}
        self.runtime = None


def _handled(mw, request):
    """Run the sync wrapper, recording whether the real tool was reached."""
    reached = []

    def handler(req):
        reached.append(req)
        return "ran"

    return mw.wrap_tool_call(request, handler), reached


# --- it answers instead of running -----------------------------------------


def test_a_planner_write_file_is_answered_not_run():
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    result, reached = _handled(mw, Request("write_file", file_path="/index.html", content="<html>"))

    assert reached == []
    assert "REJECTED:" in result.content


def test_edit_file_and_delete_are_answered_too():
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    for name in ("edit_file", "delete"):
        result, reached = _handled(mw, Request(name, file_path="/index.html"))
        assert reached == [], name
        assert "REJECTED:" in result.content, name


def test_a_read_tool_is_untouched():
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    for name in ("ls", "read_file", "glob", "grep", "record_fact", "add_tasks", "ask_user"):
        result, reached = _handled(mw, Request(name, path="/"))
        assert result == "ran", name
        assert len(reached) == 1, name


async def test_the_async_path_answers_identically():
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))
    request = Request("write_file", file_path="/index.html", content="<html>")

    async def handler(req):
        raise AssertionError("the tool must not run")

    result = await mw.awrap_tool_call(request, handler)

    assert "REJECTED:" in result.content


# --- how the failure counter reads it (OPEN-118) ----------------------------


def test_the_refusal_is_counted_as_a_planner_failure():
    """Pinned against the counter itself, not the text (OPEN-118).

    This was `test_the_refusal_leads_with_REJECTED_and_never_Error`, whose
    docstring said the lead kept the failure counter from firing. The
    planner's counter (`agent/planner_agent.py::_stream_planner_turn`) asks
    `message_is_error`, which answers from `status="error"` before any text,
    so three of these in a row DO end a stage. Kept counted on purpose, the
    owner's OPEN-103 decision applied here: an uncounted refusal a model
    ignores is bounded only by the stage's call and time caps (OPEN-100)."""
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    result, _ = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert result.content.startswith("REJECTED:")
    assert result.status == "error"
    assert message_is_error(result) is True
    assert is_rudra_refusal(result) is False


# --- it names only tools the stage holds (OPEN-15) -------------------------


@pytest.mark.parametrize(
    ("tools", "named", "absent"),
    [
        (("record_fact", "ask_user"), "record_fact", "add_tasks"),
        (("record_fact",), "record_fact", "add_tasks"),
        (("add_tasks", "drop_task", "read_ledger"), "add_tasks", "record_fact"),
    ],
)
def test_the_route_names_a_tool_this_stage_actually_has(tools, named, absent):
    """clarify, architect and breakdown in order. The breakdown stage has no
    `record_fact`, so naming it there would advertise an absent tool -- which
    is exactly what OPEN-101 is filed on, one surface over."""
    mw = PlannerWriteMiddleware(stage_tools=tools)

    result, _ = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert named in result.content
    assert absent not in result.content


def test_a_stage_holding_neither_still_gets_a_route():
    """Declines to name a tool rather than naming a wrong one."""
    mw = PlannerWriteMiddleware(stage_tools=("ls", "read_file"))

    result, _ = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert "REJECTED:" in result.content
    assert "record_fact" not in result.content
    assert "add_tasks" not in result.content


def test_the_refusal_names_the_path_the_model_asked_for():
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    result, _ = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert "/index.html" in result.content


def test_the_refusal_tells_the_model_not_to_reproduce_the_content():
    """The whole cost of this run was 430.9 s of HTML generated three times.
    A refusal that stops the tool call and not the generation saves nothing."""
    mw = PlannerWriteMiddleware(stage_tools=("record_fact",))

    result, _ = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert "coder" in result.content


# --- the diagnostic --------------------------------------------------------


def test_a_refusal_is_counted_and_announced():
    usage = RunUsage()
    trace = FakeTrace()
    mw = PlannerWriteMiddleware(
        stage_tools=("record_fact",), role="planner", usage=usage, trace=trace
    )

    _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert usage.as_dict()["planner"]["planner_writes_refused"] == 1
    assert trace.notices[0][0] == PLANNER_WRITE_NOTICE
    assert "/index.html" in trace.notices[0][1]


def test_bookkeeping_failure_does_not_break_the_refusal():
    """CLAUDE.md §8a: a run that did its work must not be reported failed
    because a log line could not be written."""

    class Exploding:
        def notice(self, *a, **k):
            raise RuntimeError("no")

        def record_planner_write_refused(self, *a, **k):
            raise RuntimeError("no")

    mw = PlannerWriteMiddleware(
        stage_tools=("record_fact",), role="planner", usage=Exploding(), trace=Exploding()
    )

    result, reached = _handled(mw, Request("write_file", file_path="/index.html", content="x"))

    assert reached == []
    assert "REJECTED:" in result.content


# --- it is on the planner stack and nowhere else ---------------------------


def test_the_middleware_is_on_the_planner_stack():
    stack = planner_agent.build_planner_middleware("build it")

    assert any(isinstance(m, PlannerWriteMiddleware) for m in stack)


def test_the_middleware_registers_no_tools_of_its_own():
    """**A trap, hit while writing this.** `AgentMiddleware.tools` is
    upstream's list of tools a middleware CONTRIBUTES to the agent
    (`langchain/agents/middleware/types.py:150`), and langchain registers
    every entry. A constructor parameter named `tools` therefore does not
    configure the middleware -- it hands `ToolNode` a set of tool NAMES,
    which fails at agent build time -- `getattr(m, "tools", [])`, factory.py:1005
    -- on every planner stage. Hence `stage_tools`."""
    mw = PlannerWriteMiddleware(stage_tools=("record_fact", "ask_user"))

    # `factory.py:1005` -- `[t for m in middleware for t in getattr(m, "tools", [])]`
    assert list(getattr(mw, "tools", [])) == []
    assert mw.stage_tools == frozenset({"record_fact", "ask_user"})


def test_the_middleware_is_built_once_per_stack():
    """One entry, so a refusal cannot be counted or announced twice."""
    stack = planner_agent.build_planner_middleware("build it")

    assert sum(isinstance(m, PlannerWriteMiddleware) for m in stack) == 1
