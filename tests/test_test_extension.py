"""Unit tests for TestExtensionMiddleware (OPEN-99).

Run 5775ba1f9855's tester wrote 7,619 bytes of Python to
`tests/test_iphone15_responsive.html`, said in its own output that pytest
collects only `.py` files, and shipped it. `stacks/detect.py:120` then found
no Python stack, `verify/pipeline.py` reported "this project declares no test
command", and the run finished DONE with zero runnable tests.

The must-NOT-refuse half of the table below is the load-bearing half: a false
positive here is a REFUSED REAL WRITE, and a test suite legitimately holds
`tests/fixture.html`, `tests/data.json` and `tests/conf.yaml`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rudra.middleware.test_extension import (
    _UNCOLLECTABLE,
    TEST_EXTENSION_NOTICE,
    TestExtensionMiddleware,
    has_test_name,
    is_test_path,
    is_uncollectable_test,
    suggested_name,
)
from rudra.trace.stream import _FIRST_LINE_MARKERS, is_rudra_refusal, message_is_error

# The shape of what the tester actually wrote, trimmed. Module docstring, an
# import, a test function -- Python by `ast.parse`, `.html` by name.
PY_SOURCE = (
    '"""Tests for iphone15_responsive.html."""\n\n'
    "import html.parser\n\n\n"
    "def test_doctype():\n"
    "    assert True\n"
)


# --------------------------------------------------------------------------
# is_test_path -- two independent readings, either sufficient
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_page.html",
        "tests/unit/deep/test_page.html",
        "test/test_page.html",
        "spec/thing.html",
        "specs/thing.html",
        "TESTS/test_page.html",
        "app/user_test.py",
        "app/test_user.py",
        r"tests\test_page.html",
    ],
)
def test_paths_inside_the_suite_or_named_like_a_test(path):
    """A directory segment, or a basename in pytest's own discovery shape.
    The backslash spelling reads the same, for `virtual_paths.py`'s reason:
    the shape of the path decides, never the host OS."""
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    ["src/page.html", "index.html", "src/latest.py", "", "docs/testing.md"],
)
def test_paths_that_are_neither(path):
    """`docs/testing.md` is the near miss: a segment CONTAINING "test" is not
    a segment that IS one, and `latest.py` ends in "test" only as letters."""
    assert is_test_path(path) is False


# --------------------------------------------------------------------------
# is_uncollectable_test -- section 7.2's table, both halves
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_page.html",
        "tests/unit/test_page.html",
        "app/user_test.html",
        "tests/test_page.htm",
        "tests/test_page.xml",
        "tests/test_page.css",
    ],
)
def test_python_written_under_an_uncollectable_name_is_refused(path):
    assert is_uncollectable_test(path, PY_SOURCE) is True


@pytest.mark.parametrize(
    ("path", "content"),
    [
        # Already correct -- `.py` is not in the closed set at all.
        ("tests/test_page.py", PY_SOURCE),
        # Not a test path: the coder's deliverable, which it was asked for.
        ("src/page.html", "<!DOCTYPE html>\n<html></html>\n"),
        ("index.html", PY_SOURCE),
        # A real fixture inside the suite. HTML is not valid Python, so the
        # ast.parse condition declines it without needing to guess intent.
        ("tests/fixture.html", "<!DOCTYPE html>\n<html></html>\n"),
        ("tests/style.css", "body { color: red; }"),
        # A JS test in a JS project. `.js` is deliberately outside the set.
        ("tests/test_page.js", "describe('x', () => { it('y', () => {}); });"),
        # Empty, whitespace, and not a string at all.
        ("tests/test_page.html", ""),
        ("tests/test_page.html", "   \n  "),
        ("tests/test_page.html", ["x"]),
        ("tests/test_page.html", None),
        # Not parseable as Python: an ordinary HTML document under a test
        # name is a fixture, not a misnamed test.
        ("tests/test_page.html", "<!DOCTYPE html>\n<html></html>\n"),
    ],
)
def test_calls_that_must_not_be_refused(path, content):
    """A false positive here is a refused real write (scope 6)."""
    assert is_uncollectable_test(path, content) is False


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("tests/data.json", '{"a": 1}'),
        ("tests/list.json", "[1, 2, 3]"),
        ("tests/conf.yaml", "name: rudra\n"),
        ("tests/conf.yml", "a: 1\nb: 2\n"),
    ],
)
def test_json_and_yaml_fixtures_are_never_refused(path, content):
    """MEASURED, not assumed. `ast.parse('{"a": 1}')` is a dict literal,
    `ast.parse('name: rudra')` is an AnnAssign -- ordinary fixture content in
    all three suffixes parses as Python, so keeping them in the set would
    refuse an ordinary `tests/data.json`. They are out, and this pins it."""
    assert is_uncollectable_test(path, content) is False


def test_the_uncollectable_set_holds_no_suffix_whose_fixtures_parse():
    """The other direction of the same rule, so a later session that
    "completes" the set breaks a test rather than a run."""
    assert _UNCOLLECTABLE == frozenset({".html", ".htm", ".xml", ".css"})
    for absent in (".json", ".yaml", ".yml", ".py", ".txt", ".md", ".js"):
        assert absent not in _UNCOLLECTABLE


def test_a_test_named_file_outside_a_test_dir_still_counts():
    """`is_test_path`'s second reading. A project keeping tests beside their
    source is the case this covers."""
    assert is_uncollectable_test("app/user_test.html", PY_SOURCE) is True


# --------------------------------------------------------------------------
# suggested_name
# --------------------------------------------------------------------------


def test_the_suggestion_is_the_same_file_under_a_collectable_name():
    assert suggested_name("tests/test_page.html") == "tests/test_page.py"
    assert suggested_name("tests/unit/test_page.htm") == "tests/unit/test_page.py"
    assert suggested_name(r"tests\test_page.html") == "tests/test_page.py"


# --------------------------------------------------------------------------
# The middleware -- section 7.3, the bytes are the damage
# --------------------------------------------------------------------------


def _request(path: str, content: object, *, name: str = "write_file"):
    return SimpleNamespace(
        tool_call={"name": name, "args": {"file_path": path, "content": content}, "id": "c1"}
    )


def test_the_write_is_refused_before_the_backend_sees_it(tmp_path):
    """Section 8.2: the bytes on disk must be untouched, not merely a string
    returned. The whole value is that the file does not land."""
    target = tmp_path / "test_page.html"
    reached = []

    def handler(_req):
        target.write_text(PY_SOURCE, encoding="utf-8")
        reached.append(True)
        return "Updated file"

    result = TestExtensionMiddleware("tester").wrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), handler
    )

    assert reached == []
    assert not target.exists()
    assert result.status == "error"
    assert result.content.startswith("REJECTED:")
    # It must name the correction, or the model has nothing to re-issue.
    assert "tests/test_page.py" in result.content


async def test_the_async_path_refuses_the_same_call(tmp_path):
    reached = []

    async def handler(_req):
        reached.append(True)
        return "Updated file"

    result = await TestExtensionMiddleware("tester").awrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), handler
    )

    assert reached == []
    assert result.content.startswith("REJECTED:")


def test_a_call_it_declines_reaches_the_handler_untouched():
    reached = []

    def handler(req):
        reached.append(req)
        return "Updated file"

    request = _request("tests/fixture.html", "<!DOCTYPE html>")
    result = TestExtensionMiddleware("tester").wrap_tool_call(request, handler)

    assert reached == [request]
    assert result == "Updated file"


@pytest.mark.parametrize("tool", ["edit_file", "read_file", "execute", "ls"])
def test_only_write_file_is_judged(tool):
    """`edit_file` cannot create a file, so the extension was decided by an
    earlier `write_file` this already saw. Judging it again would refuse a
    repair of a file already on disk."""
    reached = []

    def handler(req):
        reached.append(req)
        return "ok"

    request = _request("tests/test_page.html", PY_SOURCE, name=tool)
    assert TestExtensionMiddleware("tester").wrap_tool_call(request, handler) == "ok"
    assert reached == [request]


def test_a_non_string_path_is_declined_rather_than_crashing():
    def handler(_req):
        return "ok"

    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": None, "content": PY_SOURCE},
            "id": "c1",
        }
    )
    assert TestExtensionMiddleware("tester").wrap_tool_call(request, handler) == "ok"


# --------------------------------------------------------------------------
# Section 8.3 -- how the failure counter reads the refusal (OPEN-118)
# --------------------------------------------------------------------------


def test_the_refusal_is_counted_as_a_tool_failure():
    """Pinned against the counter itself, not the text (OPEN-118).

    Section 8.3 asked for the opposite, and this test used to "prove" it by
    asserting `looks_like_error(result.content)` is False. That function is
    never reached for this message: `message_is_error` -- what
    `subagents/runner.py` calls -- answers from `status="error"` first, so
    three in a row DO halt the tester. Kept counted on purpose, the owner's
    OPEN-103 decision: an uncounted refusal a model ignores is bounded only
    by 80 calls."""
    result = TestExtensionMiddleware("tester").wrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), lambda _req: "Updated file"
    )
    assert result.status == "error"
    assert message_is_error(result) is True
    assert is_rudra_refusal(result) is False


def test_rejected_is_not_a_first_line_marker():
    """Pinned so that adding `REJECTED:` to that tuple breaks a test rather
    than a run. Not for this middleware's sake -- its refusal is counted by
    `status` either way (OPEN-118) -- but for the tools that answer a
    malformed call with `REJECTED:` and a SUCCESSFUL status: `record_fact`
    and `add_tasks` did so 17 times across five archived runs, and a marker
    would start counting every one of them toward a halt."""
    assert "REJECTED:" not in _FIRST_LINE_MARKERS


# --------------------------------------------------------------------------
# Section 8.4 -- counted and announced (CLAUDE.md 8a)
# --------------------------------------------------------------------------


def test_the_refusal_is_counted_once_and_names_the_path():
    counted: list[str] = []
    notices: list[tuple[str, str]] = []

    usage = SimpleNamespace(record_test_write_rejected=counted.append)
    trace = SimpleNamespace(notice=lambda text, role=None, name=None: notices.append((name, text)))

    TestExtensionMiddleware("tester", usage=usage, trace=trace).wrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), lambda _req: "Updated file"
    )

    assert counted == ["tester"]
    assert len(notices) == 1
    name, text = notices[0]
    assert name == TEST_EXTENSION_NOTICE
    # A false positive here is a refused real write, so the notice has to
    # name the path or the count cannot be checked by hand.
    assert "tests/test_page.html" in text


def test_a_declined_call_counts_nothing():
    counted: list[str] = []
    usage = SimpleNamespace(record_test_write_rejected=counted.append)

    TestExtensionMiddleware("tester", usage=usage).wrap_tool_call(
        _request("tests/fixture.html", "<!DOCTYPE html>"), lambda _req: "Updated file"
    )

    assert counted == []


def test_bookkeeping_may_never_end_a_run():
    """CLAUDE.md 8a: every writer here swallows its own failure. A run that
    did its work must not be reported failed because a log line could not be
    written -- and the refusal itself must still happen."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("no")

    usage = SimpleNamespace(record_test_write_rejected=boom)
    trace = SimpleNamespace(notice=boom)

    result = TestExtensionMiddleware("tester", usage=usage, trace=trace).wrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), lambda _req: "Updated file"
    )

    assert result.content.startswith("REJECTED:")


def test_it_refuses_with_no_usage_and_no_trace():
    """Section 8.6. Counting is the optional half; the bytes are the damage."""
    result = TestExtensionMiddleware().wrap_tool_call(
        _request("tests/test_page.html", PY_SOURCE), lambda _req: "Updated file"
    )
    assert result.content.startswith("REJECTED:")


# --------------------------------------------------------------------------
# has_test_name -- the shared spelling, and the neighbour it is NOT
# --------------------------------------------------------------------------


def test_has_test_name_reads_the_basename_only():
    """The reading `verify/pipeline.py` asks alone, so a legitimate
    `tests/fixture.html` is not quoted in the gate's message."""
    assert has_test_name("tests/test_page.html") is True
    assert has_test_name("app/user_test.html") is True
    assert has_test_name("tests/fixture.html") is False
    assert has_test_name("tests/data.json") is False
    assert has_test_name("") is False


def test_it_is_not_the_stack_detector_s_predicate():
    """`stacks/detect.py::_is_test_filename` asks whether a file is a test the
    gate should COUNT, and is gated on source suffixes -- so it answers False
    for every suffix this module exists to catch. Two neighbouring predicates
    answering two different questions, which is `source_files` versus
    `project_files` again (OPEN-63). Collapsing them would make this guard
    blind to exactly its own case."""
    from rudra.stacks.detect import _is_test_filename

    assert _is_test_filename("test_page.html") is False
    assert has_test_name("test_page.html") is True
    # ...and in the other direction: a TS spelling pytest collects under no
    # name at all is a test to the detector and not to this.
    assert _is_test_filename("api.test.ts") is True
    assert has_test_name("api.test.ts") is False
