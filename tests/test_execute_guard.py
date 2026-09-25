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
    COMMAND_PATH_NOTICE,
    PIP_REFUSAL,
    PIP_REFUSED_NOTICE,
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


# --------------------------------------------------------------------------
# 4. pip refusing to install outside a virtualenv (OPEN-120)
# --------------------------------------------------------------------------


def _refused() -> ToolMessage:
    return ToolMessage(
        content=f"ERROR: {PIP_REFUSAL}\n[Command failed with exit code 3]",
        name="execute",
        tool_call_id="c1",
        artifact={"exit_code": 3},
        status="success",
    )


class _Trace:
    def __init__(self):
        self.notices: list[dict] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append({"payload": payload, "role": role, "name": name})


def test_a_pip_refusal_is_explained_with_the_route():
    """Run a04f89bd2ed6's tester, refused, would have looked for another pip.
    A refusal carrying no correction is answered by retrying (OPEN-95)."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "pip3 install Flask==3.0.3 SQLAlchemy==2.0.29 2>&1")

    result = guard.wrap_tool_call(request, _Handler(_refused()))

    assert PIP_REFUSAL in result.content, "the original result survives"
    assert "requirements.txt or pyproject.toml" in result.content
    assert ".venv" in result.content


def test_the_pip_note_is_true_whether_or_not_this_is_a_python_project():
    """Final whole-branch review, finding 2: the note used to state flatly
    that "Rudra installs declared dependencies into this project's .venv
    before every gate run," but `testing/project_env.py::_skip_reason`
    returns "not a python project" for anything `stacks.detect` does not call
    Python -- so in a Node or Rust project that sentence is false, and can
    steer a model into inventing a bogus requirements.txt/pyproject.toml
    there. The middleware has no reliable project-type signal at this point,
    so the routing half is worded conditionally instead of detected."""
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "pip install flask")

    result = guard.wrap_tool_call(request, _Handler(_refused()))

    lowered = result.content.lower()
    assert "requirements.txt or pyproject.toml" in lowered
    assert "if this is a python project" in lowered
    assert "if it is not" in lowered


def test_a_pip_refusal_is_counted_and_named():
    from rudra.context.usage import RunUsage

    usage, trace = RunUsage(), _Trace()
    guard = ExecuteGuardMiddleware(role="tester", usage=usage, trace=trace)
    command = "python3 -m pip install flask"

    guard.wrap_tool_call(_tool_request("execute", command), _Handler(_refused()))

    assert usage.as_dict()["tester"]["installs_refused"] == 1
    assert [(n["name"], n["role"]) for n in trace.notices] == [(PIP_REFUSED_NOTICE, "tester")]
    assert command in trace.notices[0]["payload"]


def test_an_install_that_failed_for_another_reason_is_never_annotated():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", ".venv/bin/python -m pip install flask==99")
    result = _failed("ERROR: No matching distribution found for flask==99")

    assert guard.wrap_tool_call(request, _Handler(result)) is result


def test_a_cd_and_a_pip_refusal_in_one_command_get_both_notes():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "cd /app && pip install -r requirements.txt")

    result = guard.wrap_tool_call(request, _Handler(_refused()))

    assert "already runs in this project's root directory" in result.content
    assert "requirements.txt or pyproject.toml" in result.content


@pytest.mark.asyncio
async def test_the_pip_note_also_runs_on_the_async_path():
    guard = ExecuteGuardMiddleware()
    request = _tool_request("execute", "pip install flask")

    result = await guard.awrap_tool_call(request, _Handler(_refused()).acall)

    assert "requirements.txt or pyproject.toml" in result.content


def test_pip_itself_refuses_with_the_text_this_guard_keys_on(tmp_path):
    """The upstream contract, pinned against the machine's real pip.

    The note keys on pip's OUTPUT, and pip owns that sentence
    (pip/_internal/cli/base_command.py:213). A release that rewords it would
    silence the note with every stand-in test above still green -- so this
    one runs pip. Skipped where the machine has no pip outside a venv.
    """
    import subprocess
    from pathlib import Path

    from rudra.permissions.env import scrubbed_env
    from rudra.stacks.detect import system_interpreter

    interpreter = system_interpreter()
    if not Path(interpreter).is_absolute():
        pytest.skip("no python3 on PATH outside Rudra's own venv")
    probe = subprocess.run(
        [interpreter, "-c", "import sys, pip; print(sys.prefix == sys.base_prefix)"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "True":
        pytest.skip("the machine's python has no pip, or is itself a virtualenv")
    env = scrubbed_env(SimpleNamespace(models={}))
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(
        [interpreter, "-m", "pip", "install", "--no-index", "rudra-probe-does-not-exist"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
        check=False,
    )

    assert result.returncode == 3, result.stdout + result.stderr
    assert PIP_REFUSAL in result.stdout + result.stderr


# --------------------------------------------------------------------------
# 5. A command that used this project's VIRTUAL "/" spelling (OPEN-151)
#
# The inverse of MachinePathMiddleware. That one explains a FILE TOOL sent to
# the machine; this explains an `execute` sent to the project's virtual
# spelling -- the one place where "/x" means two different things, which
# `_COMMAND_RULES` (subagents/registry.py:201-223) states in three bullets and
# a Right/Wrong pair and lost anyway.
#
# Live run G2 (`eed59b91daca`), the tester, working t1:
#   execute {'command': '/.venv/bin/python -m pytest tests/test_main.py -v'}
#     -> [stderr] /bin/sh: /.venv/bin/python: No such file or directory
#        Exit code: 127
#   execute {'command': 'ls -la /.venv/bin/ | grep python'}
#     -> [stderr] ls: /.venv/bin/: No such file or directory  Exit code: 1
# Both ALLOWED by the gate (`permissions.jsonl`, mode-default), and each is a
# tool failure at `subagents/runner.py`'s MAX_CONSECUTIVE_FAILURES = 3 (:42):
# two in a row spent two thirds of that budget before the tester recovered by
# itself with the relative spelling.
# --------------------------------------------------------------------------


def _shell_error(message: str, exit_code: int = 1) -> ToolMessage:
    """What the shell actually answers: deepagents renders stderr with a
    `[stderr] ` prefix (backends/local_shell.py:320-336) and the status line
    from `_format_execute_output` (middleware/filesystem.py:2765)."""
    return ToolMessage(
        content=f"[stderr] {message}\n[Command failed with exit code {exit_code}]",
        name="execute",
        tool_call_id="c1",
        artifact={"exit_code": exit_code},
        status="success",
    )


def test_a_command_that_used_the_virtual_spelling_is_explained(tmp_path):
    """Run G2's first call, verbatim."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/.venv/bin/python -m pytest tests/test_main.py -v")
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)

    result = guard.wrap_tool_call(request, _Handler(answer))

    assert "[Command failed with exit code 127]" in result.content, "the original result survives"
    assert ".venv/bin/python" in result.content
    assert "already runs in this project's root directory" in result.content


def test_the_explanation_names_the_relative_spelling_to_use(tmp_path):
    """OPEN-95's rule: a refusal carrying no correction is one the model
    answers by retrying. The note has to say what to type instead."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/.venv/bin/python -m pytest tests/test_main.py -v")
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)

    note = guard.wrap_tool_call(request, _Handler(answer)).content

    assert "`.venv/bin/python`" in note, "the spelling that works, quoted"
    assert "`/.venv/bin/python`" in note, "and the one the shell looked for"


def test_run_g2_s_second_command_is_explained_through_the_pipe(tmp_path):
    """`ls -la /.venv/bin/ | grep python` -- the token is not the first word,
    and the command holds a pipe, so the scan cannot be a `startswith`."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "ls -la /.venv/bin/ | grep python")
    answer = _shell_error("ls: /.venv/bin/: No such file or directory", 1)

    result = guard.wrap_tool_call(request, _Handler(answer))

    assert ".venv/bin" in result.content
    assert "already runs in this project's root directory" in result.content


def test_the_third_archived_hit_is_explained(tmp_path):
    """Run 2cde3406f7d6: `cat /src/iphone15.html` failed, and 170 s later that
    same tester wrote the same literal into a test file -- 8 of 8 tests failing
    forever against 480 lines of correct HTML, the run 0 of 2 in 2,132 s.
    OPEN-93 fixed the WRITE; nothing answered this call, which is this item."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "iphone15.html").write_text("<!doctype html>")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "cat /src/iphone15.html")
    answer = _shell_error("cat: /src/iphone15.html: No such file or directory")

    result = guard.wrap_tool_call(request, _Handler(answer))

    assert "src/iphone15.html" in result.content


def test_a_command_that_succeeded_is_never_explained(tmp_path):
    """machine_paths.py's rule one tool over: a project that really answers
    must be answered with its own behaviour, never with this."""
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/.venv/bin/python -m pytest")
    answer = _succeeded()

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_machine_path_the_project_does_not_hold_is_left_alone(tmp_path):
    """`execute` may legitimately ask for the machine's interpreter."""
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/usr/local/bin/python3.99 -V")
    answer = _shell_error("/bin/sh: /usr/local/bin/python3.99: No such file or directory", 127)

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_first_segment_in_machine_dirs_declines_even_when_the_project_has_one(tmp_path):
    """`/etc/hosts` in a shell command is ordinary and correct, and the model
    may have meant the machine. MACHINE_DIRS is the same frozenset
    machine_paths.py reads -- one definition, two consumers."""
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hosts").write_text("127.0.0.1 localhost\n")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "cat /etc/hosts")
    answer = _shell_error("cat: /etc/hosts: No such file or directory")

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_path_that_really_exists_on_the_machine_is_left_alone(tmp_path):
    """`/bin/sh` exists here. Whatever failed, it was not the spelling."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "sh").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/bin/sh run.sh")
    answer = _shell_error("run.sh: No such file or directory", 127)

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_failure_with_no_slash_token_is_left_alone(tmp_path):
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "python -m pytest tests/test_x.py -v")
    answer = _shell_error("ModuleNotFoundError: No module named 'main'")

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_bare_slash_and_a_unc_spelling_are_left_alone(tmp_path):
    """Probed: `virtual_to_host` resolves both `/` and `//server/share` to the
    project ROOT itself, which exists, so a naive existence check would
    annotate every failing command that happens to hold one."""
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    for command in ("ls / //server/share", "du -sh /"):
        answer = _shell_error("ls: //server/share: No such file or directory")
        assert guard.wrap_tool_call(_tool_request("execute", command), _Handler(answer)) is answer


def test_a_token_the_shell_never_named_is_left_alone(tmp_path):
    """The precision rule, and the reason the note cannot fire on a command
    that merely CONTAINS such a token: every one of the three archived hits is
    a path the shell itself quoted back. A test that failed on an assertion is
    not this defect, whatever `--cov=/src` resolves to."""
    (tmp_path / "src").mkdir()
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "python -m pytest --cov=/src tests/")
    answer = _shell_error("AssertionError: assert 1 == 2")

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_no_project_path_declines(tmp_path):
    """Nowhere to resolve against, so nothing can be claimed
    (machine_paths.py's own rule)."""
    guard = ExecuteGuardMiddleware(project_path=None)
    request = _tool_request("execute", "/.venv/bin/python -m pytest")
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


def test_a_cd_into_a_path_the_project_holds_gets_one_note_not_two(tmp_path):
    """`cd /app` with an `app/` in the project satisfies both rules, and both
    would say the same thing. The `cd` note is the more specific -- it names
    the `cd` itself -- so it wins and this one stands down."""
    (tmp_path / "app").mkdir()
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "cd /app && python -m pytest")
    answer = _shell_error("sh: line 0: cd: /app: No such file or directory")

    content = guard.wrap_tool_call(request, _Handler(answer)).content

    assert content.count("already runs in this project's root directory") == 1


def test_a_virtual_path_and_a_pip_refusal_in_one_command_get_both_notes(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "pip").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    request = _tool_request("execute", "/.venv/bin/pip install -r requirements.txt")
    answer = ToolMessage(
        content=(
            "[stderr] /bin/sh: /.venv/bin/pip: No such file or directory\n"
            f"ERROR: {PIP_REFUSAL}\n[Command failed with exit code 3]"
        ),
        name="execute",
        tool_call_id="c1",
        artifact={"exit_code": 3},
        status="success",
    )

    content = guard.wrap_tool_call(request, _Handler(answer)).content

    assert ".venv/bin/pip" in content
    assert "requirements.txt or pyproject.toml" in content


def test_a_virtual_path_command_is_counted_and_named(tmp_path):
    """CLAUDE.md 8a: a number that would answer a user's complaint must be
    WRITTEN. What this guard prevents is a tool failure that never happens."""
    from rudra.context.usage import RunUsage

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    usage, trace = RunUsage(), _Trace()
    guard = ExecuteGuardMiddleware(role="tester", usage=usage, trace=trace, project_path=tmp_path)
    command = "/.venv/bin/python -m pytest tests/test_main.py -v"
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)

    guard.wrap_tool_call(_tool_request("execute", command), _Handler(answer))

    assert usage.as_dict()["tester"]["commands_explained"] == 1
    assert [(n["name"], n["role"]) for n in trace.notices] == [(COMMAND_PATH_NOTICE, "tester")]
    assert "/.venv/bin/python" in trace.notices[0]["payload"]


def test_the_explanation_never_rewrites_the_command(tmp_path):
    """Option C, rejected in the plan and pinned here: `rm /tests` rewritten is
    the project's tests deleted. Nothing in this module rewrites a command."""
    (tmp_path / "tests").mkdir()
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    handler = _Handler(_shell_error("rm: /tests: No such file or directory"))

    guard.wrap_tool_call(_tool_request("execute", "rm -rf /tests"), handler)

    assert handler.seen.tool_call["args"]["command"] == "rm -rf /tests"


def test_the_result_still_reads_as_a_failure(tmp_path):
    """OPEN-94: this appends to an existing failure, so `runner.py`'s
    consecutive-failure counter sees exactly what it saw before."""
    from rudra.trace.stream import message_is_error

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)
    before = message_is_error(answer)

    result = guard.wrap_tool_call(
        _tool_request("execute", "/.venv/bin/python -m pytest"), _Handler(answer)
    )

    assert message_is_error(result) == before
    assert result.artifact == {"exit_code": 127}


def test_a_bare_string_result_is_handled(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    answer = (
        "[stderr] /bin/sh: /.venv/bin/python: No such file or directory\n"
        "[Command failed with exit code 127]"
    )

    result = guard.wrap_tool_call(
        _tool_request("execute", "/.venv/bin/python -m pytest"), _Handler(answer)
    )

    assert isinstance(result, str)
    assert ".venv/bin/python" in result


def test_an_unbalanced_quote_does_not_raise(tmp_path):
    """`shlex.split` raises on an unterminated quote, and a model emits one.
    Bookkeeping may never end a run (CLAUDE.md 8a)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    answer = _shell_error("sh: unexpected EOF while looking for matching `\"'")

    result = guard.wrap_tool_call(
        _tool_request("execute", 'python -c "print(open(/src/a.py'), _Handler(answer)
    )

    assert result is not None


def test_a_non_execute_tool_is_never_explained(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)
    request = SimpleNamespace(
        tool_call={"name": "read_file", "args": {"command": "/.venv/bin/python"}, "id": "c1"}
    )

    assert guard.wrap_tool_call(request, _Handler(answer)) is answer


@pytest.mark.asyncio
async def test_the_virtual_path_note_also_runs_on_the_async_path(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    guard = ExecuteGuardMiddleware(project_path=tmp_path)
    answer = _shell_error("/bin/sh: /.venv/bin/python: No such file or directory", 127)

    result = await guard.awrap_tool_call(
        _tool_request("execute", "/.venv/bin/python -m pytest"), _Handler(answer).acall
    )

    assert ".venv/bin/python" in result.content
