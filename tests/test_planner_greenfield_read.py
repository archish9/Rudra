"""OPEN-116 — a greenfield planner stage answers a stray file read with the route.

A planner stage whose project holds nothing to read is built without `ls`,
`read_file`, `glob` and `grep` (`agent/planner_agent.py::create_planner_agent`):
absence is the enforcement, and on run `8f160d92c6da` those four tools were
2191.3 of 4088.0 model seconds.

Absence alone leaves the ANSWER to a habitual call as langgraph's tool-list
echo, which says what the model cannot do and nothing about what to do
instead -- OPEN-103's run-killer, where 3 of 4 coder invocations died on three
echoes in a row, and a planner stage halts on the same three
(`_stream_planner_turn`). The coder, shown a listing and never told to `ls`,
still opened with one in 18 of 46 invocations, so the habit is measured.
"""

from __future__ import annotations

import pytest

from rudra.agent import planner_agent
from rudra.context.usage import RunUsage
from rudra.middleware import GreenfieldReadMiddleware
from rudra.middleware.planner_greenfield import GREENFIELD_READ_NOTICE
from rudra.trace.stream import is_rudra_refusal, message_is_error


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
    reached = []

    def handler(req):
        reached.append(req)
        return "ran"

    return mw.wrap_tool_call(request, handler), reached


# --- it answers instead of echoing -----------------------------------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("ls", {"path": "/"}),
        ("read_file", {"file_path": "/.mcp.json"}),
        ("glob", {"pattern": "**/*.py"}),
        ("grep", {"pattern": "flask"}),
    ],
)
def test_a_read_on_a_greenfield_stage_is_answered(name, args):
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    result, reached = _handled(mw, Request(name, **args))

    assert reached == []
    assert result.content.startswith("REJECTED:")
    assert "no files yet" in result.content
    assert "PROJECT STRUCTURE" in result.content


@pytest.mark.parametrize("name", ["write_file", "edit_file", "record_fact", "add_tasks", "bash"])
def test_anything_but_a_read_is_left_alone(name):
    """Writes are PlannerWriteMiddleware's, the stage's own tools run, and a
    shell name is not a read -- a wrong route is worse than none."""
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    result, reached = _handled(mw, Request(name, file_path="/x"))

    assert result == "ran"
    assert len(reached) == 1


def test_a_registered_read_tool_is_never_answered():
    """Only an UNREGISTERED call is answered. If a stage somehow holds the
    tool, the tool answers -- this middleware must never hide a real file."""
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    result, reached = _handled(mw, Request("ls", registered=True, path="/"))

    assert result == "ran"
    assert len(reached) == 1


async def test_the_async_path_answers_identically():
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    async def handler(req):
        raise AssertionError("the tool must not run")

    result = await mw.awrap_tool_call(Request("ls", path="/"), handler)

    assert result.content.startswith("REJECTED:")


def test_the_answer_is_counted_as_a_planner_failure():
    """The owner's OPEN-103 decision, as PlannerWriteMiddleware keeps it: an
    uncounted answer a model ignores is bounded only by the stage's 40-call
    and time caps. `status="error"`, read before any text (OPEN-118)."""
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    result, _ = _handled(mw, Request("ls", path="/"))

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
    mw = GreenfieldReadMiddleware(stage_tools=tools)

    result, _ = _handled(mw, Request("read_file", file_path="/.mcp.json"))

    assert named in result.content
    assert absent not in result.content


def test_the_answer_names_no_file_tool_as_available():
    """It is answering the absence of those tools; naming one as a route
    would be the echo's defect with better grammar."""
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    result, _ = _handled(mw, Request("glob", pattern="**/*"))

    for other in ("ls", "read_file", "grep"):
        assert f"`{other}`" not in result.content, other


def test_a_stage_holding_neither_still_gets_a_route():
    mw = GreenfieldReadMiddleware(stage_tools=())

    result, _ = _handled(mw, Request("ls", path="/"))

    assert result.content.startswith("REJECTED:")
    assert "record_fact" not in result.content
    assert "add_tasks" not in result.content


# --- the diagnostic (CLAUDE.md §8a) ----------------------------------------


def test_an_answer_is_counted_and_announced():
    usage = RunUsage()
    trace = FakeTrace()
    mw = GreenfieldReadMiddleware(
        stage_tools=("record_fact",), role="planner", usage=usage, trace=trace
    )

    _handled(mw, Request("read_file", file_path="/.mcp.json"))

    assert usage.as_dict()["planner"]["greenfield_reads_answered"] == 1
    name, payload = trace.notices[0]
    assert name == GREENFIELD_READ_NOTICE
    assert "read_file" in payload
    assert "/.mcp.json" in payload


def test_bookkeeping_failure_does_not_break_the_answer():
    class Exploding:
        def notice(self, *a, **k):
            raise RuntimeError("no")

        def record_greenfield_read_answered(self, *a, **k):
            raise RuntimeError("no")

    mw = GreenfieldReadMiddleware(
        stage_tools=("record_fact",), role="planner", usage=Exploding(), trace=Exploding()
    )

    result, reached = _handled(mw, Request("ls", path="/"))

    assert reached == []
    assert result.content.startswith("REJECTED:")


# --- where it sits ---------------------------------------------------------


def test_a_greenfield_stack_carries_it_once_right_after_the_write_answer():
    """Outside the repeat guard, for MachinePathMiddleware's reason: a
    repeated stray call the guard would dedupe must still get the route."""
    names = [type(m).__name__ for m in planner_agent.build_planner_middleware("t", can_read=False)]

    assert names.count("GreenfieldReadMiddleware") == 1
    at = names.index("GreenfieldReadMiddleware")
    assert names[at - 1] == "PlannerWriteMiddleware"
    assert at < names.index("RepeatGuardMiddleware")


def test_a_readable_stack_does_not_carry_it():
    stack = planner_agent.build_planner_middleware("t")

    assert not any(isinstance(m, GreenfieldReadMiddleware) for m in stack)


def test_the_middleware_registers_no_tools_of_its_own():
    """`AgentMiddleware.tools` is what a middleware CONTRIBUTES; the trap
    `test_planner_write_refusal.py` records."""
    mw = GreenfieldReadMiddleware(stage_tools=("record_fact",))

    assert list(getattr(mw, "tools", [])) == []


def test_a_greenfield_filesystem_middleware_registers_no_tool():
    """Built, not dropped: deepagents adds its own FilesystemMiddleware with
    every tool when none is passed (graph.py:215-232)."""
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)
    stack = planner_agent.build_planner_middleware(
        "t", backend=backend, evict_tokens=13107, can_read=False
    )

    filesystem = [m for m in stack if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1
    assert list(filesystem[0].tools) == []
    assert filesystem[0]._tool_token_limit_before_evict == 13107
