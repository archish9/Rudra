"""OPEN-25: the `execute` tool's own description tells the model it is in a sandbox.

deepagents builds every `execute` tool from a description that opens
"Executes a shell command in an isolated sandbox", whose only worked example
is a `cd`, and which says "Use absolute paths" -- directly contradicting
`_PATH_RULES`' "NEVER use absolute paths"
(deepagents/middleware/filesystem.py:1290-1298). Measured result: the tester
emitted `cd /app && python -m pytest ...` and `cd /mnt/<uuid> && ...`, both
into directories that do not exist, costing two model calls each time.

A prompt cannot outrank a prompt (OPEN-17), so the contradicting text is
REPLACED rather than argued with, and a command that failed after a `cd` is
told why in-band.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from rudra.middleware.execute_guard import (
    RUDRA_EXECUTE_DESCRIPTION,
    ExecuteGuardMiddleware,
)


def _tool(name: str, description: str) -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda command="": "", name=name, description=description
    )


class _ModelRequest(SimpleNamespace):
    """Enough of langchain's ModelRequest for the description swap."""

    def override(self, **overrides):
        merged = dict(self.__dict__)
        merged.update(overrides)
        return _ModelRequest(**merged)


def _model_request(*tools) -> _ModelRequest:
    return _ModelRequest(tools=list(tools))


class _Handler:
    """Records the request it was handed and returns a canned result."""

    def __init__(self, result=None):
        self.result = result
        self.seen = None

    def __call__(self, request):
        self.seen = request
        return self.result

    async def acall(self, request):
        self.seen = request
        return self.result


def _tool_request(name: str, command: str) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "args": {"command": command}, "id": "c1"})


def _failed(command_output: str = "sh: line 0: cd: /app: No such file or directory") -> ToolMessage:
    return ToolMessage(
        content=f"{command_output}\n[Command failed with exit code 1]",
        name="execute",
        tool_call_id="c1",
        artifact={"exit_code": 1},
        status="success",
    )


def _succeeded(output: str = "ok") -> ToolMessage:
    return ToolMessage(
        content=f"{output}\n[Command succeeded with exit code 0]",
        name="execute",
        tool_call_id="c1",
        artifact={"exit_code": 0},
        status="success",
    )


# --------------------------------------------------------------------------
# 1. The description the model is given
# --------------------------------------------------------------------------


def test_the_replacement_says_the_shell_is_already_in_the_project():
    """The fact that settles it, stated positively. No prompt in Rudra said
    where `execute` runs before OPEN-25 -- only what "/" meant to it."""
    lowered = RUDRA_EXECUTE_DESCRIPTION.lower()
    assert "project" in lowered
    assert "root directory" in lowered


def test_the_replacement_carries_none_of_the_three_pushes():
    """ "isolated sandbox", a `cd` example, and "use absolute paths" are the
    three things upstream's description does that produce the bug."""
    lowered = RUDRA_EXECUTE_DESCRIPTION.lower()
    assert "isolated sandbox" not in lowered
    assert 'cd "' not in lowered
    assert "use absolute paths" not in lowered


def test_the_execute_description_is_replaced_before_the_model_sees_it():
    guard = ExecuteGuardMiddleware()
    handler = _Handler()
    request = _model_request(_tool("execute", "Executes a shell command in an isolated sandbox."))

    guard.wrap_model_call(request, handler)

    assert handler.seen.tools[0].description == RUDRA_EXECUTE_DESCRIPTION


def test_other_tools_are_left_alone():
    guard = ExecuteGuardMiddleware()
    handler = _Handler()
    request = _model_request(
        _tool("read_file", "Reads a file."),
        _tool("execute", "Executes a shell command in an isolated sandbox."),
    )

    guard.wrap_model_call(request, handler)

    assert handler.seen.tools[0].description == "Reads a file."
    assert handler.seen.tools[1].description == RUDRA_EXECUTE_DESCRIPTION


def test_the_caller_s_tool_object_is_never_mutated():
    """`model_copy`, not assignment: the tool list is owned by the
    FilesystemMiddleware that built it, and one agent's rewrite must not
    reach another's."""
    guard = ExecuteGuardMiddleware()
    original = _tool("execute", "Executes a shell command in an isolated sandbox.")
    request = _model_request(original)

    guard.wrap_model_call(request, _Handler())

    assert original.description == "Executes a shell command in an isolated sandbox."


def test_an_agent_without_execute_is_passed_through_untouched():
    """The reviewer and general-purpose agents have no `execute`. Nothing to
    rewrite means the request object itself is handed on unchanged."""
    guard = ExecuteGuardMiddleware()
    handler = _Handler()
    request = _model_request(_tool("read_file", "Reads a file."))

    guard.wrap_model_call(request, handler)

    assert handler.seen is request


def test_a_request_with_no_tools_at_all_is_passed_through():
    guard = ExecuteGuardMiddleware()
    handler = _Handler()
    request = _ModelRequest(tools=None)

    guard.wrap_model_call(request, handler)

    assert handler.seen is request


@pytest.mark.asyncio
async def test_the_description_swap_also_runs_on_the_async_path():
    """Rudra invokes every agent with `astream` (permissions/approval.py:212),
    and langchain puts a sync-only middleware into the async chain anyway --
    where the base class raises NotImplementedError."""
    guard = ExecuteGuardMiddleware()
    handler = _Handler()
    request = _model_request(_tool("execute", "Executes a shell command in an isolated sandbox."))

    await guard.awrap_model_call(request, handler.acall)

    assert handler.seen.tools[0].description == RUDRA_EXECUTE_DESCRIPTION


# --------------------------------------------------------------------------
# 2. The in-band warning, keyed on the `cd` and never on the prefix
# --------------------------------------------------------------------------


def test_a_failed_cd_into_a_sandbox_path_is_explained():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && python -m pytest tests/test_todo.py -v")

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "[Command failed with exit code 1]" in result.content, "the original result survives"
    assert "/app" in result.content
    assert "already runs in this project's root directory" in result.content


def test_a_failed_cd_into_an_invented_mount_is_explained_too():
    """/mnt/<uuid> is in no prefix set. Keying on the `cd` catches it anyway,
    which is the whole reason this is not a SANDBOX_PREFIXES check."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request(
        "execute", "cd /mnt/w01d0e0020-6a70-4f67-9b8e-9f8b8b8b8b8b && python -m pytest -v"
    )

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "/mnt/w01d0e0020-6a70-4f67-9b8e-9f8b8b8b8b8b" in result.content
    assert "already runs in this project's root directory" in result.content


def test_a_known_sandbox_prefix_is_named_as_one():
    """The prefix set does not decide whether to fire -- it only sharpens the
    wording once the `cd` already has."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /workspace && pytest")

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "container" in result.content.lower()


def test_a_quoted_absolute_cd_is_caught():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", 'cd "/app/my project" && pytest')

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "already runs in this project's root directory" in result.content


def test_a_windows_shaped_absolute_cd_is_caught_on_any_host():
    """Shape, not `sys.platform` (CLAUDE.md §1.8). A model emits the platform
    its training data suggests, not the one it is running on."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", r"cd C:\workspace\proj && pytest")

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "already runs in this project's root directory" in result.content


def test_a_cd_after_a_semicolon_is_caught():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "echo hi ; cd /testbed && pytest")

    result = guard.wrap_tool_call(request, _Handler(_failed()))

    assert "/testbed" in result.content


def test_a_command_that_succeeded_is_never_annotated():
    """The note is a consolation for a turn already lost. A command that
    worked has nothing to learn from it."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && pytest")
    result = _succeeded()

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_failed_command_writing_to_tmp_is_never_annotated():
    """D4's objection, answered structurally: `/tmp/` is a SANDBOX_PREFIXES
    entry AND an ordinary directory. Keying on the `cd` means a command that
    merely NAMES one is untouched, so this can never misfire on it."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cp build/out.txt /tmp/out.txt")
    result = _failed("cp: build/out.txt: No such file or directory")

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_failed_command_with_no_cd_at_all_is_never_annotated():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "python -m pytest tests/test_todo.py -v")
    result = _failed("2 failed")

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_relative_cd_is_never_annotated():
    """`cd tests && pytest` is pointless here but it is not this bug, and a
    note about container paths would be a lie."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd tests && pytest")
    result = _failed("no tests ran")

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_word_ending_in_cd_is_not_a_cd():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "abcd /app/thing")
    result = _failed("abcd: command not found")

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_non_execute_tool_is_never_annotated():
    guard = ExecuteGuardMiddleware()
    request = SimpleNamespace(
        tool_call={"name": "write_file", "args": {"file_path": "cd /app"}, "id": "c1"}
    )
    result = _failed()

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_the_exit_code_is_read_from_the_content_when_there_is_no_artifact():
    """deepagents publishes no artifact when the exit code is unknown
    (filesystem.py:2777-2779), and offloaded output takes a different code
    path entirely (:2781-2795). The status line is the one thing both write."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && pytest")
    result = ToolMessage(
        content="sh: cd: /app: No such file or directory\n[Command failed with exit code 1]",
        name="execute",
        tool_call_id="c1",
    )

    assert (
        "already runs in this project's root directory"
        in guard.wrap_tool_call(request, _Handler(result)).content
    )


def test_a_result_that_is_a_bare_string_is_handled():
    """Nothing upstream returns one today, but `wrap_tool_call` is typed
    loosely enough that a middleware ahead of this one could."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && pytest")
    result = "cd: /app: No such file or directory\n[Command failed with exit code 1]"

    annotated = guard.wrap_tool_call(request, _Handler(result))

    assert "already runs in this project's root directory" in annotated


@pytest.mark.asyncio
async def test_the_warning_also_runs_on_the_async_path():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && pytest")

    result = await guard.awrap_tool_call(request, _Handler(_failed()).acall)

    assert "already runs in this project's root directory" in result.content


# --------------------------------------------------------------------------
# 3. The invariant the static description depends on
#    (the stack wiring is asserted in test_subagents_build.py, beside the
#     other _middleware_for invariants, which have a real Config)
# --------------------------------------------------------------------------


def test_every_spec_that_grants_execute_also_grants_the_search_tools():
    """The replacement description is static, so it names grep, glob and
    read_file unconditionally -- upstream picks between four variants by
    which of those are visible (filesystem.py:2602-2620). Static is only
    honest while this holds."""
    from rudra.subagents.registry import REGISTRY

    for name, spec in REGISTRY.items():
        if "execute" not in spec.fs_tools:
            continue
        assert {"grep", "glob", "read_file"} <= set(spec.fs_tools), name


def test_the_real_deepagents_execute_tool_survives_the_swap(tmp_path):
    """The stand-in tools above prove the branch; this proves the tool.

    `model_copy` is a pydantic copy of a tool deepagents built by closing over
    a backend (filesystem.py:2797-2890). If it dropped the coroutine or the
    args schema the swap would not fail loudly -- `execute` would simply stop
    working, in a run, on a model call.
    """
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    upstream = FilesystemMiddleware(
        backend=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True, env={}),
        tools=["ls", "read_file", "glob", "grep", "execute"],
    )
    tools = list(upstream.tools)
    before = {tool.name: tool for tool in tools}
    assert "isolated sandbox" in before["execute"].description, "upstream changed; see OPEN-25"

    handler = _Handler()
    ExecuteGuardMiddleware().wrap_model_call(_model_request(*tools), handler)
    after = {tool.name: tool for tool in handler.seen.tools}

    assert after["execute"].description == RUDRA_EXECUTE_DESCRIPTION
    assert after["execute"].args_schema is before["execute"].args_schema
    assert after["execute"].coroutine is before["execute"].coroutine
    assert after["execute"].func is before["execute"].func
    for name, tool in before.items():
        if name != "execute":
            assert after[name] is tool, f"{name} was copied for no reason"
