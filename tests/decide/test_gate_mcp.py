"""mcp_hint: the MCP tool pre-selection hint (decide/gates.py, OPEN-179 Task 1).

laya picks the single most likely MCP tool for a task from the tools VISIBLE to
this subagent, and the caller injects that tool's id + schema into the prompt so
the agent skips the ``list_mcp_tools -> describe_mcp_tool -> call`` exploration
(2-3 Vaayu turns). A HINT only: it never removes a capability and never names a
tool the agent cannot call. On abstain / review / unavailable / no visible tools
-> ``None`` and the agent explores exactly as today.
"""

from rudra.decide.engine import Decision
from rudra.decide.gates import McpHint, mcp_hint


class _Tool:
    """A duck-typed stand-in for an MCP ToolInfo (id, description, schema)."""

    def __init__(self, id: str, description: str = "", input_schema: dict | None = None) -> None:
        self.id = id
        self.description = description
        self.input_schema = input_schema or {"type": "object", "properties": {}}


class _FakeEngine:
    """Returns one pre-built Decision for ``mcp_tool``; records the state and
    whether ``decide`` was called at all (a no-server guard must not call laya)."""

    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision
        self.called = False
        self.state = None

    def decide(self, state, questions):
        self.called = True
        self.state = state
        return {"mcp_tool": self._decision}


def _decision(*, value, abstain=False, route="auto", backend="mlx", confidence=0.9) -> Decision:
    return Decision(
        question_id="mcp_tool",
        kind="choice",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=confidence,
        backend=backend,
    )


def _tools() -> list[_Tool]:
    return [
        _Tool("mempalace__search", "search long-term memory"),
        _Tool("mempalace__store", "store a memory"),
        _Tool("kala__verify", "verify a design against the system"),
    ]


def test_confident_pick_returns_hint_with_id_and_schema():
    tools = _tools()
    eng = _FakeEngine(_decision(value="kala__verify"))
    hint = mcp_hint(eng, "check this component against the design system", tools)
    assert isinstance(hint, McpHint)
    assert hint.tool_id == "kala__verify"
    # The chosen tool's own schema is carried, ready to inline.
    assert hint.input_schema == tools[2].input_schema
    assert hint.description == "verify a design against the system"


def test_abstain_returns_none():
    eng = _FakeEngine(_decision(value="kala__verify", abstain=True, route="review"))
    assert mcp_hint(eng, "anything", _tools()) is None


def test_review_route_returns_none():
    eng = _FakeEngine(_decision(value="kala__verify", route="review"))
    assert mcp_hint(eng, "anything", _tools()) is None


def test_unavailable_backend_returns_none():
    eng = _FakeEngine(
        _decision(value=None, abstain=True, route="review", backend="unavailable", confidence=0.0)
    )
    assert mcp_hint(eng, "anything", _tools()) is None


def test_picked_id_not_visible_returns_none():
    # laya names a tool that is not in the visible set -> never hint a tool the
    # agent cannot call. Treat exactly like an abstain.
    eng = _FakeEngine(_decision(value="forbidden__tool"))
    assert mcp_hint(eng, "anything", _tools()) is None


def test_no_visible_tools_returns_none_without_calling_laya():
    # No servers / nothing visible -> nothing to hint, and no wasted laya call.
    eng = _FakeEngine(_decision(value="whatever"))
    assert mcp_hint(eng, "anything", []) is None
    assert eng.called is False


def test_task_is_passed_as_named_state_field():
    # tool_select's instructions reference `task`; the state must key it so.
    eng = _FakeEngine(_decision(value="mempalace__search"))
    mcp_hint(eng, "recall what we learned", _tools())
    assert eng.state == {"task": "recall what we learned"}


def test_options_are_built_from_visible_tool_ids():
    # The choice laya is asked must offer exactly the visible ids, so it can
    # never pick a tool outside the subagent's allow/deny visibility.
    captured = {}

    class _CapturingEngine(_FakeEngine):
        def decide(self, state, questions):
            captured["questions"] = questions
            return super().decide(state, questions)

    eng = _CapturingEngine(_decision(value="mempalace__search"))
    mcp_hint(eng, "recall", _tools())
    criteria = captured["questions"]["mcp_tool"]["criteria"]
    assert set(criteria) == {"mempalace__search", "mempalace__store", "kala__verify"}
