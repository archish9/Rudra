"""OPEN-93: a virtual path written into real source code.

Run `2cde3406f7d6`: the tester wrote `HTML_PATH = "/src/iphone15.html"` into
`tests/test_iphone15.py`. Rudra's file tools resolve a leading "/" against the
project (`virtual_mode=True`), so that spelling is correct as a TOOL ARGUMENT
-- and `_PATH_RULES` says so, without qualification. But this string was not a
tool argument. It was data inside a file that `python3 -m pytest` then ran, and
to a real interpreter `/src` is the machine's root. 8 of 8 tests failed forever
against 480 lines of correct HTML, and the fix loop could not converge because
the only agent that could see the defect (the coder) was scoped to the HTML.

Three defences, tested here:

* `find_project_absolute_literals` -- the predicate, which declines on
  everything uncertain rather than guessing.
* `ContentPathMiddleware` -- the tool's own answer, APPENDED to a successful
  result at the moment of the mistake. Never prepended: `runner.py`'s
  MAX_CONSECUTIVE_FAILURES and `repeat_guard._is_error` both key on a leading
  `Error` (OPEN-94 on this same board is that failure).
* It never rewrites `content`. `fix_write_params.py` excludes content from
  path repair deliberately; a false rewrite corrupts a legitimate config.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from rudra.middleware.content_paths import (
    CONTENT_PATH_NOTICE,
    ContentPathMiddleware,
    find_project_absolute_literals,
    is_interpreted_file,
    project_top_level,
)

TOP = frozenset({"src", "tests", "docs"})


def _request(name: str, **args):
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


class _Handler:
    """Returns a canned tool result and records that it ran."""

    def __init__(self, result="Updated file /tests/test_x.py"):
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


class _Usage:
    def __init__(self):
        self.flagged: list[str] = []

    def record_content_path_flagged(self, role):
        self.flagged.append(role)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # The file the middleware tests' literal names. Since OPEN-141 a literal
    # is explained only when it names a FILE on disk, which run
    # 2cde3406f7d6's did: its HTML had been complete for 170 s.
    (tmp_path / "src" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# find_project_absolute_literals -- the predicate (plan doc 8.1)
# --------------------------------------------------------------------------


def test_the_reported_literal_is_found():
    """The exact bytes of run 2cde3406f7d6, `tests/test_iphone15.py:9`."""
    src = '"""Tests for /src/iphone15.html."""\n\nHTML_PATH = "/src/iphone15.html"\n'
    assert find_project_absolute_literals(src, TOP) == ["/src/iphone15.html"]


def test_a_single_quoted_literal_counts_too():
    assert find_project_absolute_literals("p = '/tests/data.json'", TOP) == ["/tests/data.json"]


def test_a_bare_top_level_directory_counts():
    assert find_project_absolute_literals('ROOT = "/src"', TOP) == ["/src"]


def test_each_literal_is_reported_once_in_source_order():
    src = 'a = "/tests/x"\nb = "/src/y"\nc = "/tests/x"\n'
    assert find_project_absolute_literals(src, TOP) == ["/tests/x", "/src/y"]


def test_a_url_route_is_declined():
    """`api` is not a top-level entry of this project, so nothing is claimed."""
    assert find_project_absolute_literals('URL = "/api/v1/users"', TOP) == []


def test_a_real_machine_path_is_declined():
    """`/usr/bin/env` and `/etc/hosts` are requirements, not mistakes."""
    src = 'SHELL = "/usr/bin/env"\nHOSTS = "/etc/hosts"\n'
    assert find_project_absolute_literals(src, TOP) == []


def test_a_relative_path_is_declined():
    assert find_project_absolute_literals('HTML_PATH = "src/index.html"', TOP) == []


def test_a_url_on_the_line_declines_the_line():
    src = 'LINK = "https://example.com"  # see "/src/index.html"\n'
    assert find_project_absolute_literals(src, TOP) == []


def test_a_literal_inside_a_hash_comment_is_declined():
    assert find_project_absolute_literals('x = 1  # was "/src/old.html"', TOP) == []


def test_a_literal_inside_a_slash_comment_is_declined():
    assert find_project_absolute_literals('let x = 1; // "/src/old.html"', TOP) == []


def test_an_empty_top_level_declines_everything():
    """A project we could not read is a project we say nothing about."""
    assert find_project_absolute_literals('HTML_PATH = "/src/index.html"', frozenset()) == []


def test_a_windows_spelling_is_recognised_by_shape():
    r"""CLAUDE.md 1.8: branch on the shape of the input, never on sys.platform."""
    assert find_project_absolute_literals(r"P = '\src\index.html'", TOP) == ["\\src\\index.html"]


# --------------------------------------------------------------------------
# is_interpreted_file -- which files a real process later runs (plan doc 5C)
# --------------------------------------------------------------------------


def test_source_that_gets_executed_is_in_scope():
    for name in ("tests/test_x.py", "app.js", "build.sh", "Makefile", "Dockerfile", "x.go"):
        assert is_interpreted_file(name), name


def test_markup_and_data_are_out_of_scope():
    """A path in an .html href or a .md link is resolved by a browser or a
    reader, not by a process with this project's root as its cwd."""
    for name in ("src/iphone15.html", "README.md", "data.json", "notes.txt"):
        assert not is_interpreted_file(name), name


def test_a_virtual_spelling_of_the_target_still_resolves():
    assert is_interpreted_file("/tests/test_x.py")
    assert is_interpreted_file(r"\tests\test_x.py")


# --------------------------------------------------------------------------
# The middleware (plan doc 8.2-8.4)
# --------------------------------------------------------------------------


def test_a_flagged_write_gets_the_note_appended(tmp_path):
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    handler = _Handler("Updated file /tests/test_x.py")
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        handler,
    )
    assert handler.calls == 1
    assert "/src/index.html" in out
    assert "src/index.html" in out


def test_the_note_is_appended_and_never_prepended(tmp_path):
    """OPEN-94's failure mode, pinned. `trace/stream.py::looks_like_error` and
    `repeat_guard._is_error` both key on the FIRST characters of a result."""
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert out.startswith("Updated file /tests/test_x.py")
    assert out.splitlines()[0] == "Updated file /tests/test_x.py"


def test_the_content_itself_is_never_rewritten(tmp_path):
    """The bytes the model asked for are the bytes written. This middleware
    appends a sentence to a RESULT; it never edits an argument."""
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    request = _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"')
    seen = {}

    def handler(req):
        seen.update(req.tool_call["args"])
        return "Updated file /tests/test_x.py"

    mw.wrap_tool_call(request, handler)
    assert seen["content"] == 'P = "/src/index.html"'
    assert request.tool_call["args"]["content"] == 'P = "/src/index.html"'


def test_a_html_deliverable_gets_nothing(tmp_path):
    """Plan doc 8.3. The HTML in the reported run was never wrong."""
    root = _project(tmp_path)
    mw = ContentPathMiddleware("coder", project_path=root)
    out = mw.wrap_tool_call(
        _request(
            "write_file",
            file_path="/src/page.html",
            content='<a href="/src/index.html">x</a>',
        ),
        _Handler("Updated file /src/page.html"),
    )
    assert out == "Updated file /src/page.html"


def test_a_clean_write_gets_nothing(tmp_path):
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "src/index.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert out == "Updated file /tests/test_x.py"


def test_a_failed_write_gets_nothing(tmp_path):
    """Never touch a failure: the front of it is what two other guards count."""
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler("Error: something went wrong"),
    )
    assert out == "Error: something went wrong"


def test_an_edit_is_judged_on_its_new_string(tmp_path):
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request(
            "edit_file",
            file_path="/tests/test_x.py",
            old_string='P = "x"',
            new_string='P = "/src/index.html"',
        ),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert "/src/index.html" in out
    assert out.startswith("Updated file")


def test_a_tool_message_result_is_annotated_in_place(tmp_path):
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    message = ToolMessage(content="Updated file /tests/test_x.py", tool_call_id="call-1")
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler(message),
    )
    assert isinstance(out, ToolMessage)
    assert out.content.startswith("Updated file /tests/test_x.py")
    assert "/src/index.html" in out.content


def test_an_unrelated_tool_is_untouched(tmp_path):
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request("read_file", file_path="/tests/test_x.py"), _Handler('1  P = "/src/x.html"')
    )
    assert out == '1  P = "/src/x.html"'


def test_no_project_path_declines(tmp_path):
    mw = ContentPathMiddleware("tester", project_path=None)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert out == "Updated file /tests/test_x.py"


# --------------------------------------------------------------------------
# The counter and the notice (plan doc 8.4, CLAUDE.md 8a)
# --------------------------------------------------------------------------


def test_a_hit_is_counted_and_announced(tmp_path):
    root = _project(tmp_path)
    usage, trace = _Usage(), _Trace()
    mw = ContentPathMiddleware("tester", project_path=root, usage=usage, trace=trace)
    mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert usage.flagged == ["tester"]
    assert [name for name, _ in trace.notices] == [CONTENT_PATH_NOTICE]
    assert "/src/index.html" in trace.notices[0][1]


def test_bookkeeping_failure_never_ends_a_run(tmp_path):
    """CLAUDE.md 8a: a run that did its work must not fail over a log line."""

    class _Broken:
        def record_content_path_flagged(self, role):
            raise RuntimeError("boom")

        def notice(self, *a, **k):
            raise RuntimeError("boom")

    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root, usage=_Broken(), trace=_Broken())
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/index.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert "/src/index.html" in out


# --------------------------------------------------------------------------
# project_top_level -- read from disk, never guessed (plan doc 5C)
# --------------------------------------------------------------------------


def test_top_level_is_read_from_disk(tmp_path):
    root = _project(tmp_path)
    (root / "pyproject.toml").write_text("x")
    assert project_top_level(root) >= {"src", "tests", "pyproject.toml"}


def test_an_unreadable_project_declines(tmp_path):
    assert project_top_level(tmp_path / "nope") == frozenset()


def test_a_project_that_really_holds_usr_is_answered_about_it(tmp_path):
    """The set is what the project HAS, so a project with a `usr/` gets the
    note about `/usr/...` and one without is told nothing."""
    root = _project(tmp_path)
    (root / "usr").mkdir()
    assert find_project_absolute_literals('P = "/usr/share/x"', project_top_level(root)) == [
        "/usr/share/x"
    ]


# --------------------------------------------------------------------------
# find_project_absolute_mentions -- the same question asked of DIAGNOSTIC text
# --------------------------------------------------------------------------


def test_a_bare_mention_in_an_assertion_message_is_found():
    """The gate's blocker is pytest output, not source: half the run's own
    lines quote the path and half do not. `find_project_absolute_literals`
    reads a language's string grammar and would miss the unquoted half."""
    from rudra.middleware.content_paths import find_project_absolute_mentions

    text = "E   AssertionError: HTML file not found at /src/iphone15.html\n"
    assert find_project_absolute_mentions(text, TOP) == ["/src/iphone15.html"]


def test_a_quoted_mention_is_found_without_its_quotes():
    from rudra.middleware.content_paths import find_project_absolute_mentions

    text = "E   FileNotFoundError: [Errno 2] No such file or directory: '/src/x.html'"
    assert find_project_absolute_mentions(text, TOP) == ["/src/x.html"]


def test_trailing_punctuation_is_not_part_of_the_path():
    from rudra.middleware.content_paths import find_project_absolute_mentions

    assert find_project_absolute_mentions("looked in /src/x.html, and failed.", TOP) == [
        "/src/x.html"
    ]


def test_a_mention_of_something_this_project_does_not_have_is_declined():
    from rudra.middleware.content_paths import find_project_absolute_mentions

    text = "No such file or directory: '/usr/bin/env'\nGET /api/v1/users 404\n"
    assert find_project_absolute_mentions(text, TOP) == []


def test_a_url_is_not_a_project_path():
    from rudra.middleware.content_paths import find_project_absolute_mentions

    assert find_project_absolute_mentions("fetched https://example.com/src/x", TOP) == []


def test_mentions_are_reported_once_in_order():
    from rudra.middleware.content_paths import find_project_absolute_mentions

    text = "at /src/a\nand /tests/b\nand /src/a again"
    assert find_project_absolute_mentions(text, TOP) == ["/src/a", "/tests/b"]


# --------------------------------------------------------------------------
# OPEN-141: a literal is a project path only if it names a file on disk
# --------------------------------------------------------------------------


def _fastapi_project(tmp_path: Path) -> Path:
    """Run 19cde7ef0661's layout: an `api/` package serving `/api/...` routes."""
    (tmp_path / "api" / "routes").mkdir(parents=True)
    (tmp_path / "api" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "routes" / "todo.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_a_url_route_is_not_explained_even_when_the_project_has_that_directory(tmp_path):
    """The live false positive, six times in one run: `/api/v1/todos` in a
    test of a FastAPI app whose package is `api/`. The first segment is a real
    top-level entry; the path it spells is not."""
    root = _fastapi_project(tmp_path)
    usage, trace = _Usage(), _Trace()
    mw = ContentPathMiddleware("coder", project_path=root, usage=usage, trace=trace)
    content = (
        'response = client.get("/api/v1/todos")\n'
        'response = client.get("/api/v1/todos?page=2&page_size=5")\n'
        'response = client.get("/api/v1/todos/{todo_id}")\n'
    )
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_routes_todo.py", content=content),
        _Handler("Updated file /tests/test_routes_todo.py"),
    )
    assert out == "Updated file /tests/test_routes_todo.py"
    assert usage.flagged == []
    assert trace.notices == []


def test_a_route_prefix_spelling_a_real_directory_is_declined(tmp_path):
    """The route that DOES spell something on disk: a router prefix beside
    the package of the same name. `api/` and `api/v1/` both exist, and a
    prefix is still not a path -- so a directory is not evidence, only a file
    is (OPEN-141, the owner's choice over "exists")."""
    root = _fastapi_project(tmp_path)
    (root / "api" / "v1").mkdir()
    mw = ContentPathMiddleware("coder", project_path=root)
    content = (
        'router = APIRouter(prefix="/api")\n'
        'app.include_router(router, prefix="/api/v1")\n'
        'bp = Blueprint("api", __name__, url_prefix="/api")\n'
    )
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/main.py", content=content),
        _Handler("Updated file /main.py"),
    )
    assert out == "Updated file /main.py"


def test_a_literal_naming_a_real_file_in_that_directory_is_still_explained(tmp_path):
    """The same project, and the mistake OPEN-93 exists for: a spelling that
    names a file this project really holds."""
    root = _fastapi_project(tmp_path)
    mw = ContentPathMiddleware("coder", project_path=root)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/api/routes/todo.py"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert "api/routes/todo.py" in out


def test_a_literal_naming_nothing_yet_is_declined(tmp_path):
    """The price of OPEN-141, pinned: a test written before the file it
    names is not explained. The module errs toward declining; the gate's own
    `_test_path_note` still reports the path once the test fails on it."""
    root = _project(tmp_path)
    mw = ContentPathMiddleware("tester", project_path=root)
    out = mw.wrap_tool_call(
        _request("write_file", file_path="/tests/test_x.py", content='P = "/src/later.html"'),
        _Handler("Updated file /tests/test_x.py"),
    )
    assert out == "Updated file /tests/test_x.py"
