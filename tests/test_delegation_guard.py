"""OPEN-26: a subagent that should not delegate must not be able to.

Measured 2026-08-26, run `run2`: six of twelve tasks blocked with "the coder
wrote nothing", ~20s each. The coder wanted to run tests, has no `execute`
(registry.py:179), so it called `task` -- which every Rudra subagent holds,
because deepagents registers the tool whenever any subagent spec is passed
(graph.py:827-828) and Rudra always passes its gated general-purpose one.

The delegate is that general-purpose agent: read-only, and also without
`execute`. It answered, verbatim:

    "I cannot run unit tests as I do not have the ability to execute code or
     run commands."

The parent coder took that as the outcome and stopped. No write, so the
empty-diff guard (OPEN-13) blocked the task.

`_CODER_PROMPT` already told it to "finish the writing work instead of
looking for another way". `task` IS the other way, and it was in the tool
list -- OPEN-17's lesson: a prompt cannot outrank a tool that exists.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from rudra.middleware.delegation_guard import DELEGATION_TOOL, DelegationGuardMiddleware


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(func=lambda **kw: "", name=name, description=name)


class _ModelRequest(SimpleNamespace):
    def override(self, **overrides):
        merged = dict(self.__dict__)
        merged.update(overrides)
        return _ModelRequest(**merged)


class _Handler:
    def __init__(self):
        self.seen = None

    def __call__(self, request):
        self.seen = request

    async def acall(self, request):
        self.seen = request


def _request(*names):
    return _ModelRequest(tools=[_tool(name) for name in names])


def _names(request):
    return [tool.name for tool in request.tools]


def test_the_task_tool_is_removed_when_a_spec_may_not_delegate():
    handler = _Handler()

    DelegationGuardMiddleware(can_delegate=False).wrap_model_call(
        _request("write_file", DELEGATION_TOOL, "read_file"), handler
    )

    assert _names(handler.seen) == ["write_file", "read_file"]


def test_every_other_tool_survives_untouched():
    handler = _Handler()
    request = _request("ls", "read_file", "write_file", "edit_file", "glob", "grep", "task")

    DelegationGuardMiddleware(can_delegate=False).wrap_model_call(request, handler)

    assert DELEGATION_TOOL not in _names(handler.seen)
    assert len(handler.seen.tools) == len(request.tools) - 1


def test_a_spec_that_may_delegate_keeps_the_tool():
    """The flag exists so this stays possible. Nothing ships with it today,
    and `to_subagent_spec` is the path that would use it -- build.py:353-355
    records that nothing has ever built a delegating parent."""
    handler = _Handler()

    DelegationGuardMiddleware(can_delegate=True).wrap_model_call(
        _request("write_file", DELEGATION_TOOL), handler
    )

    assert DELEGATION_TOOL in _names(handler.seen)


def test_a_request_without_the_tool_is_passed_through_unchanged():
    """Identity, not an equal copy: rebuilding the list every call would
    churn objects for nothing on the overwhelmingly common path."""
    handler = _Handler()
    request = _request("ls", "read_file")

    DelegationGuardMiddleware(can_delegate=False).wrap_model_call(request, handler)

    assert handler.seen is request


def test_a_request_with_no_tools_is_passed_through():
    handler = _Handler()
    request = _ModelRequest(tools=None)

    DelegationGuardMiddleware(can_delegate=False).wrap_model_call(request, handler)

    assert handler.seen is request


def test_the_callers_tool_list_is_never_mutated():
    handler = _Handler()
    request = _request("write_file", DELEGATION_TOOL)
    before = list(request.tools)

    DelegationGuardMiddleware(can_delegate=False).wrap_model_call(request, handler)

    assert request.tools == before


@pytest.mark.asyncio
async def test_the_filter_also_runs_on_the_async_path():
    """Rudra invokes every agent with `astream` (permissions/approval.py:212),
    and langchain puts a sync-only middleware into the async chain anyway
    (agents/factory.py:1031-1035), where the base raises NotImplementedError."""
    handler = _Handler()

    await DelegationGuardMiddleware(can_delegate=False).awrap_model_call(
        _request("write_file", DELEGATION_TOOL), handler.acall
    )

    assert DELEGATION_TOOL not in _names(handler.seen)
