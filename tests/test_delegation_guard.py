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


# ---------------------------------------------------------------------------
# OPEN-37: the same item one agent up.
#
# `planner_agent.py` passes no `subagents=`, so deepagents auto-adds its own
# general-purpose spec (graph.py:750-751, which skips the auto-add only when a
# supplied spec is literally NAMED `general-purpose`). That spec is a subagent
# like any other, so `task` is registered for the planner too -- and the
# auto-added child inherits the PARENT's tools and NOT the parent's middleware
# (CLAUDE.md, "Permission flow"), which for the planner means `add_tasks`,
# `drop_task` and `read_ledger` in an ungated agent.
#
# Measured in run6 (2026-08-26), debug log lines 528-564. The breakdown stage
# delegated:
#
#     tool_call planner task {'description': "Run the test suite to see
#                             what's actually failing",
#                             'subagent_type': 'general-purpose'}
#
# and the child answered with its tool list, which was the planner's:
#
#     Error: bash is not a valid tool, try one of
#     [ls, read_file, glob, grep, add_tasks, drop_task, read_ledger].
#
# It had no shell, so it ran `ls /home/user` (path_not_found), `ls /`, ~30
# read_file calls over the four files the planner had just read, and three
# failing `bash` calls. It contributed nothing and the run ended after it.
#
# The fix is OPEN-26's: withhold the tool. Suppressing the auto-add instead --
# by passing Rudra's own GENERAL_PURPOSE spec -- would leave `task` reachable,
# so the planner would still spend turns delegating.
# ---------------------------------------------------------------------------


def _planner_stack(**kwargs):
    from rudra.agent.planner_agent import build_planner_middleware

    return build_planner_middleware("a task", **kwargs)


def _planner_guards(**kwargs):
    return [m for m in _planner_stack(**kwargs) if isinstance(m, DelegationGuardMiddleware)]


def test_the_planner_carries_the_guard():
    guards = _planner_guards()

    assert len(guards) == 1
    assert guards[0].can_delegate is False


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"compat_task_anchor": True}, id="task-anchor"),
        pytest.param({"compat_sandbox_paths": True}, id="sandbox-paths"),
        pytest.param({"usage": object()}, id="usage"),
    ],
)
def test_the_planner_carries_the_guard_under_every_option(kwargs):
    """Unconditional, never a knob: no planner stage may delegate, so there is
    no configuration in which the guard is absent."""
    guards = _planner_guards(**kwargs)

    assert len(guards) == 1
    assert guards[0].can_delegate is False


def test_the_planner_carries_the_guard_with_a_backend():
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)

    guards = _planner_guards(backend=backend, evict_tokens=13107)

    assert len(guards) == 1
    assert guards[0].can_delegate is False


def test_the_guard_sits_after_the_param_fixer_in_the_planner_stack():
    """Same order as subagents/build.py:195-207. It must not displace
    FixWriteParamsMiddleware from the front (U.14, test_agent_wiring.py)."""
    names = [type(m).__name__ for m in _planner_stack()]

    assert names[0] == "FixWriteParamsMiddleware"
    assert names.index("DelegationGuardMiddleware") > names.index("RepeatGuardMiddleware")


def test_no_planner_stage_request_carries_the_delegation_tool():
    """The property, tested through the assembled stack rather than by name.

    Run6's planner tools, verbatim from the child's error message, plus the
    tool this item is about.
    """
    handler = _Handler()
    request = _request(
        "ls", "read_file", "glob", "grep", "add_tasks", "drop_task", "read_ledger", DELEGATION_TOOL
    )

    for middleware in _planner_stack():
        if isinstance(middleware, DelegationGuardMiddleware):
            middleware.wrap_model_call(request, handler)
            request = handler.seen

    assert DELEGATION_TOOL not in _names(request)
    assert _names(request) == [
        "ls",
        "read_file",
        "glob",
        "grep",
        "add_tasks",
        "drop_task",
        "read_ledger",
    ]
