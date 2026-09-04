"""OPEN-91 part 2: a project-rooted tool asked for the machine's filesystem.

Run `fc543fb2b82f`, task t7: the coder decided to verify its work by running
the tests, has no shell, and went looking for a Python interpreter with the
file tools -- 36 interpreter-hunting `glob` calls over 2,704 s, every one
returning the bare string `No files found`.

Every one of them could only ever return that. `glob` is virtual-rooted at the
project (`virtual_mode=True`), so `/usr/bin/python*` means
`<project>/usr/bin/python*`, and the machine's `/usr/bin` is not reachable
through any file tool on any OS. `No files found` is a SUCCESSFUL result that
reads as "look somewhere else", which is exactly what the model did.

Two prompts already forbade this in so many words when that run happened
(`_CODER_PROMPT`'s `## YOU CANNOT RUN COMMANDS`, `_GENERAL_PURPOSE_PROMPT`)
and both were in context. This fix does not add a third: it changes what the
TOOL says, at the moment of the mistake, which is the seam OPEN-82 and
OPEN-25 measured working.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from rudra.compat.virtual_paths import looks_windows_absolute
from rudra.middleware.machine_paths import (
    MACHINE_HINT_NOTICE,
    MachinePathMiddleware,
    machine_root,
)


def _request(name: str, **args):
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


class _Handler:
    """Returns a canned tool result and records that it ran."""

    def __init__(self, result="No files found"):
        self.result = result
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return self.result


class _Trace:
    def __init__(self):
        self.notices: list[tuple[str, str]] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append((name, payload))


# --------------------------------------------------------------------------
# machine_root -- resolved by SHAPE, never by host OS (CLAUDE.md 1.8)
# --------------------------------------------------------------------------


def test_a_posix_machine_root_is_recognised():
    assert machine_root("/usr/bin/python*") == "/usr"
    assert machine_root("/bin/python3") == "/bin"
    assert machine_root("/opt/homebrew/bin/python*") == "/opt"
    assert machine_root("/Library/Frameworks/Python.framework") == "/Library"


def test_a_windows_machine_path_is_recognised_on_this_host_too():
    """A mac user's model and a Windows user's model make the same mistakes,
    so the shape is what is asked -- `compat/virtual_paths.py`'s rule."""
    assert machine_root(r"C:\Windows\py.exe") == "C:\\"
    assert machine_root(r"\\server\share\python.exe") == "\\\\server\\share\\"


def test_a_backslash_root_relative_path_is_read_as_posix():
    """`\\usr\\bin` carries no drive, so `virtual_to_relative` reads it as a
    POSIX absolute path and so does this."""
    assert machine_root(r"\usr\bin\python3") == "/usr"


def test_a_project_path_is_not_a_machine_path():
    assert machine_root("/src/app.py") is None
    assert machine_root("/tests/test_app.py") is None
    assert machine_root("**/pytest") is None
    assert machine_root("tests/test_app.py") is None
    assert machine_root("") is None
    assert machine_root("/") is None


def test_tmp_is_deliberately_not_a_machine_root():
    """`/tmp/` is a hallucinated sandbox prefix that `[compat] sandbox_paths`
    already owns, and a project really can hold a `tmp/`. Claiming it for the
    machine would make this middleware lie about a directory the project has."""
    assert machine_root("/tmp/build/out.js") is None


# --------------------------------------------------------------------------
# The hint itself
# --------------------------------------------------------------------------


def test_an_empty_glob_for_an_interpreter_gets_the_explanation(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)
    handler = _Handler("No files found")

    result = middleware.wrap_tool_call(_request("glob", pattern="/usr/bin/python*"), handler)

    assert handler.calls == 1
    assert result.startswith("No files found")
    assert "only inside this project" in result
    assert str(tmp_path) in result
    assert "/usr" in result


def test_the_hint_answers_the_question_the_model_actually_had(tmp_path):
    """It has no shell and wants to run the tests. Saying "you cannot" is what
    two prompts already do; this says what happens instead."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path, has_shell=False)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert "no shell" in result
    assert "verification gate" in result


def test_an_agent_that_does_have_a_shell_is_pointed_at_it(tmp_path):
    """The tester holds `execute`, whose "/" IS the machine (OPEN-21). Telling
    it "there is no way to run anything" would be false."""
    middleware = MachinePathMiddleware("tester", project_path=tmp_path, has_shell=True)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert "execute" in result
    assert "no shell" not in result


def test_a_project_relative_pattern_is_left_alone(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="**/test_index_html.py"), _Handler("No files found")
    )

    assert result == "No files found"


def test_a_virtual_absolute_project_path_is_left_alone(tmp_path):
    """`/src/app.py` is `<project>/src/app.py` and is the spelling `ls` and
    `glob` answer in -- correct, and not this defect."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/src/*.py"), _Handler("No files found")
    )

    assert result == "No files found"


def test_a_glob_that_found_something_is_never_annotated(tmp_path):
    """A project MAY hold `usr/bin/`. The hint fires only where the result is
    empty, so a real match is answered with the real match."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("/usr/bin/python3")
    )

    assert result == "/usr/bin/python3"


def test_a_failed_read_keeps_its_error_prefix(tmp_path):
    """`runner.py`'s MAX_CONSECUTIVE_FAILURES and `repeat_guard._is_error`
    both key on content starting with "Error", so the hint is APPENDED and
    never prepended."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("read_file", file_path="/usr/bin/python3"),
        _Handler("Error: File not found: /usr/bin/python3"),
    )

    assert result.startswith("Error:")
    assert "only inside this project" in result


def test_ls_and_grep_are_covered_too(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    listed = middleware.wrap_tool_call(
        _request("ls", path="/usr/bin"), _Handler("Error: Path not found")
    )
    grepped = middleware.wrap_tool_call(
        _request("grep", pattern="def main", path="/usr/lib"), _Handler("No files found")
    )

    assert "only inside this project" in listed
    assert "only inside this project" in grepped


def test_a_grep_regex_is_never_read_as_a_path(tmp_path):
    """`grep`'s `pattern` is a regular expression. `/usr/` inside one is a
    character sequence, not a directory."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("grep", pattern="/usr/bin/python", path="/src"), _Handler("No files found")
    )

    assert result == "No files found"


def test_a_tool_message_result_is_answered_as_a_tool_message(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)
    message = ToolMessage(content="No files found", tool_call_id="call-1", name="glob")

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), lambda r: message
    )

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-1"
    assert "only inside this project" in result.content


def test_a_result_that_is_neither_string_nor_message_is_passed_through(tmp_path):
    """A `Command` carries state updates this must not rewrite -- the rule
    every uncertain case in `gutter_indent.py` follows."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)
    sentinel = object()

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), lambda r: sentinel
    )

    assert result is sentinel


def test_no_project_path_declines(tmp_path):
    """Cannot say where "/" points, so it says nothing -- `gutter_indent`'s
    "cannot confirm" rule."""
    middleware = MachinePathMiddleware("coder", project_path=None)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert result == "No files found"


def test_an_untouched_tool_is_never_wrapped(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("write_file", file_path="/usr/bin/python3", content="x"), _Handler("Updated")
    )

    assert result == "Updated"


def test_the_hint_says_it_fired(tmp_path):
    """TODO.md lesson 5: anything that changes what a run does emits a trace
    event when it fires, or it is a new silence."""
    trace = _Trace()
    middleware = MachinePathMiddleware("coder", project_path=tmp_path, trace=trace)

    middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert [name for name, _ in trace.notices] == [MACHINE_HINT_NOTICE]
    assert "/usr" in trace.notices[0][1]


def test_a_broken_trace_never_ends_the_call(tmp_path):
    """Bookkeeping may never end a run (CLAUDE.md 8a)."""

    class Exploding:
        def notice(self, *args, **kwargs):
            raise RuntimeError("sink is gone")

    middleware = MachinePathMiddleware("coder", project_path=tmp_path, trace=Exploding())

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert "only inside this project" in result


async def test_the_async_path_annotates_too(tmp_path):
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    async def handler(request):
        return "No files found"

    result = await middleware.awrap_tool_call(_request("glob", pattern="/usr/bin/python*"), handler)

    assert "only inside this project" in result


def test_the_resolved_path_is_the_one_the_backend_would_have_used(tmp_path):
    """The hint quotes `virtual_to_host`'s answer, so what it says the tool
    looked for is what the tool looked for (CR-B4's shared function)."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert str(Path(tmp_path) / "usr" / "bin" / "python*") in result


# --------------------------------------------------------------------------
# OPEN-96 -- the classification is on the RESOLVED path, not the spelling
# --------------------------------------------------------------------------

# The reported run's project root. Kept literal because it IS the
# reproduction: `_PATH_RULES` teaches `{project_path}/src/...` as a correct
# spelling, and every macOS project is under `/Users`.
_OPEN96_ROOT = Path("/Users/archish/Documents/ai-ml/test-rudra")


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        # OPEN-91's real cases -- these MUST keep firing.
        ("/usr/bin/python3", "/usr"),
        ("/opt/homebrew/bin/python3", "/opt"),
        ("/Users", "/Users"),
        (r"C:\Windows\py.exe", "C:\\"),
        # OPEN-96's misfires -- these must decline.
        (str(_OPEN96_ROOT / "src"), None),
        ("//.mcp.json", None),
        # Already correct before this item.
        ("/src", None),
        ("src", None),
    ],
)
def test_machine_root_classifies_the_resolved_path(spelling, expected):
    """A path is the machine's when the place it LANDS is a machine directory.

    Every backend is `virtual_mode=True`, so `/Users/<me>/proj/src` lands at
    `<project>/src` and is this project's own file however it was spelled --
    and `_PATH_RULES` (`registry.py`) teaches that spelling as correct.
    """
    assert machine_root(spelling, _OPEN96_ROOT) == expected


def test_the_projects_own_absolute_path_gets_no_hint(tmp_path):
    """Misfire A. `{project_path}/src` is the third spelling `_PATH_RULES`
    endorses; before OPEN-96 the middleware told the model it had reached
    outside the project for using it."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("ls", path=str(tmp_path / "src")),
        _Handler("Error: Path not found"),
    )

    assert result == "Error: Path not found"


def test_a_doubled_slash_is_not_a_network_share(tmp_path):
    """Misfire B. `PureWindowsPath("//.mcp.json").drive` is truthy -- pathlib
    reads a doubled leading slash as a UNC `\\\\server\\share` -- so the most
    common one-character path typo was reported as the machine's filesystem
    for a file the project has."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("read_file", file_path="//.mcp.json"),
        _Handler("Error: File not found"),
    )

    assert result == "Error: File not found"


def test_a_unc_spelling_is_classified_by_where_it_lands(tmp_path):
    """The deliberate narrowing of §4.2: a real DRIVE anchor is always the
    machine, a UNC one is not asserted to be. `//x` and `\\\\server\\share\\x`
    are the same shape to pathlib, so the anchor cannot decide and the
    resolved path does -- the backend strips `\\\\server\\usr\\`, which is why
    `\\\\server\\usr\\bin\\python3` lands at `bin/python3` and still fires."""
    assert machine_root(r"\\server\share\python.exe", tmp_path) is None
    assert machine_root(r"\\server\usr\bin\python3", tmp_path) == "/bin"


def test_without_a_project_root_the_spelling_still_answers():
    """`machine_root` is public and its one-argument form is what every test
    above this section calls. It keeps the pre-OPEN-96 reading, because with
    no root there is nowhere for the path to land."""
    assert machine_root("/usr/bin/python3") == "/usr"
    assert machine_root(str(_OPEN96_ROOT / "src")) == "/Users"


def test_the_hint_does_not_explain_a_substitution_that_did_not_happen(tmp_path):
    """Option B. `_HINT` reads "X was looked for at Y", which is informative
    only when X and Y differ. A model that spelled the resolved path in full
    was told "X was looked for at X, which does not exist"."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)
    spelling = str(tmp_path / "usr" / "bin" / "python3")

    result = middleware.wrap_tool_call(
        _request("read_file", file_path=spelling), _Handler("Error: File not found")
    )

    assert "only inside this project" in result
    assert "was looked for at" not in result
    assert f'"{spelling}" is not in this project' in result


def test_a_differing_spelling_still_gets_the_substitution_sentence(tmp_path):
    """The other half of the branch: when the two DO differ, saying where the
    tool actually looked is the whole point."""
    middleware = MachinePathMiddleware("coder", project_path=tmp_path)

    result = middleware.wrap_tool_call(
        _request("glob", pattern="/usr/bin/python*"), _Handler("No files found")
    )

    assert "was looked for at" in result
    assert str(Path(tmp_path) / "usr" / "bin" / "python*") in result


def test_looks_windows_absolute_is_untouched():
    """It is shared with `virtual_to_relative` and the backend's routing, so
    changing it would change path resolution project-wide (§4.4). The UNC
    reading is correct for a real UNC path; OPEN-96 is fixed inside
    `machine_root` instead."""
    assert looks_windows_absolute(r"C:\x\y.py") is True
    assert looks_windows_absolute(r"\\server\share\y.py") is True
    assert looks_windows_absolute("//.mcp.json") is True
    assert looks_windows_absolute("/src/app.py") is False
    assert looks_windows_absolute(r"\src\app.py") is False
