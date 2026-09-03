"""OPEN-92: `read_file`'s two-space gutter becomes indentation in `old_string`.

`format_content_with_line_numbers` (deepagents/backends/utils.py:243) renders
`f"{marker:>{marker_width}}  {line}"` -- the line number, two spaces, then the
raw line. A model composing an `edit_file` argument from what it was shown
carries those two spaces into the content, so every line after the first is
`+2` against the file and `content.count(old_string)` (utils.py:523) is zero.

Measured on run `fc543fb2b82f`: seven coder `edit_file` calls against
`index.html`, zero bytes changed, two guard halts. The delta was exactly `+2`
on every line of both distinct edits, and an anchored search finds a unique
match for all seven.

The tester's edits in the same run SUCCEEDED, because it was editing a file it
had written in the same invocation -- its `old_string` never round-tripped
through the gutter. That asymmetry is the whole diagnosis: the defect bites
exactly when an agent edits what it did not just write.
"""

from __future__ import annotations

from types import SimpleNamespace

from rudra.middleware.gutter_indent import GutterIndentMiddleware, repair_indent

# A file whose lines are indented, which is the only precondition the defect
# needs. 8 / 12 / 8, the shape run fc543fb2b82f actually failed on.
SOURCE = "<style>\n        .section {\n            padding: 4rem 0;\n        }\n</style>\n"

# What the model sends after copying the gutter: first line stripped of its
# indent entirely (it reads everything before the first non-space as marker),
# every later line carrying the gutter's two spaces on top of the file's own.
GUTTERED_OLD = ".section {\n              padding: 4rem 0;\n          }"
GUTTERED_NEW = ".section {\n              padding: 8rem 0;\n          }"

# The same block as the file actually holds it.
TRUE_OLD = "        .section {\n            padding: 4rem 0;\n        }"
TRUE_NEW = "        .section {\n            padding: 8rem 0;\n        }"


def _request(name: str = "edit_file", **args):
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": "call-1"})


class _Handler:
    """Records the args the tool actually receives."""

    def __init__(self, result: str = "OK"):
        self.result = result
        self.seen: list[dict] = []

    def __call__(self, request):
        self.seen.append(dict(request.tool_call.get("args", {})))
        return self.result


def _write(tmp_path, content: str = SOURCE, name: str = "index.html"):
    (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# The pure function
# --------------------------------------------------------------------------


def test_the_guttered_string_matches_nothing_which_is_the_defect():
    """The premise. Without this the rest of the file proves nothing."""
    assert SOURCE.count(GUTTERED_OLD) == 0
    assert SOURCE.count(TRUE_OLD) == 1


def test_repair_returns_the_files_own_bytes_and_they_match_exactly_once():
    old, new = repair_indent(SOURCE, GUTTERED_OLD, GUTTERED_NEW)
    assert old == TRUE_OLD
    assert SOURCE.count(old) == 1


def test_new_string_is_dedented_by_the_same_delta_as_old_string():
    """§8 test 3. Assert the resulting FILE CONTENT, not merely that the
    edit succeeded -- a replacement landing at the wrong indent still
    'succeeds' and still corrupts the file."""
    old, new = repair_indent(SOURCE, GUTTERED_OLD, GUTTERED_NEW)
    assert new == TRUE_NEW
    assert SOURCE.replace(old, new) == (
        "<style>\n        .section {\n            padding: 8rem 0;\n        }\n</style>\n"
    )


def test_an_ambiguous_repair_is_refused():
    """§8 test 2, the most important test in the item. A dedented string
    matching twice is ambiguous, and silently editing the wrong block is
    worse than the failure this is fixing."""
    twice = SOURCE + SOURCE
    assert twice.count(TRUE_OLD) == 2
    assert repair_indent(twice, GUTTERED_OLD, GUTTERED_NEW) is None


def test_a_correct_old_string_is_never_touched():
    """§8 test 5. Pin the happy path: the repair must be invisible to an
    edit that would have worked."""
    assert repair_indent(SOURCE, TRUE_OLD, TRUE_NEW) is None


def test_a_string_absent_for_any_other_reason_is_declined():
    """Not every failed match is this defect. One that is not must fall
    through to the real error rather than be guessed at."""
    assert repair_indent(SOURCE, "nothing like this\n  at all", "x\n  y") is None


def test_a_single_line_old_string_is_declined():
    """The model strips the first line's indent rather than over-indenting
    it, so a one-line argument carries no gutter delta to infer from."""
    assert repair_indent(SOURCE, ".section {", ".card {") is None


def test_a_tab_indented_file_is_repaired_from_its_own_bytes():
    """The gutter is two spaces whatever the file indents with, so a tab
    file fails the same way -- and returning the file's own bytes repairs
    it without the caller ever reasoning about tabs."""
    src = "<style>\n\t.section {\n\t\tpadding: 4rem 0;\n\t}\n</style>\n"
    old, new = repair_indent(
        src,
        "  \t.section {\n  \t\tpadding: 4rem 0;\n  \t}",
        "  \t.section {\n  \t\tpadding: 8rem 0;\n  \t}",
    )
    assert old == "\t.section {\n\t\tpadding: 4rem 0;\n\t}"
    assert new == "\t.section {\n\t\tpadding: 8rem 0;\n\t}"
    assert src.count(old) == 1


def test_a_non_uniform_delta_is_declined():
    """The defect adds the SAME two spaces to every line. A block whose
    lines differ by different amounts is not this bug, and repairing it
    would be a guess."""
    assert (
        repair_indent(
            SOURCE, ".section {\n               padding: 4rem 0;\n          }", "x\n y\n z"
        )
        is None
    )


def test_a_new_string_line_shallower_than_the_delta_is_declined():
    """Only leading SPACES may be removed. A non-blank replacement line
    with less indent than the delta is not a uniformly shifted block."""
    assert repair_indent(SOURCE, GUTTERED_OLD, ".section {\npadding: 8rem 0;\n          }") is None


def test_blank_lines_in_the_replacement_survive_untouched():
    """A blank line has no indent to remove and must not be declined over."""
    old, new = repair_indent(
        SOURCE, GUTTERED_OLD, ".section {\n\n              padding: 8rem 0;\n          }"
    )
    assert new == "        .section {\n\n            padding: 8rem 0;\n        }"


# --------------------------------------------------------------------------
# The middleware
# --------------------------------------------------------------------------


def test_the_middleware_rewrites_the_arguments_before_the_tool_runs(tmp_path):
    _write(tmp_path)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert handler.seen == [
        {"file_path": "/index.html", "old_string": TRUE_OLD, "new_string": TRUE_NEW}
    ]


def test_the_middleware_leaves_a_working_edit_alone(tmp_path):
    _write(tmp_path)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=TRUE_OLD, new_string=TRUE_NEW),
        handler,
    )

    assert handler.seen == [
        {"file_path": "/index.html", "old_string": TRUE_OLD, "new_string": TRUE_NEW}
    ]


def test_the_middleware_ignores_every_tool_but_edit_file(tmp_path):
    _write(tmp_path)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    mw.wrap_tool_call(_request("write_file", file_path="/index.html", content="x"), handler)

    assert handler.seen == [{"file_path": "/index.html", "content": "x"}]


def test_a_missing_file_degrades_to_passthrough(tmp_path):
    """No project path, a backend route outside the project, or a file that
    is gone -- every uncertain case performs the call unchanged and lets the
    real error be reported."""
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    mw.wrap_tool_call(
        _request(file_path="/gone.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert handler.seen == [
        {"file_path": "/gone.html", "old_string": GUTTERED_OLD, "new_string": GUTTERED_NEW}
    ]


def test_no_project_path_degrades_to_passthrough():
    mw = GutterIndentMiddleware(role="coder", project_path=None)
    handler = _Handler()

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert handler.seen[0]["old_string"] == GUTTERED_OLD


def test_a_repair_is_counted_so_the_item_can_be_shown_to_have_paid(tmp_path):
    """CLAUDE.md §8a: a number that would answer a user's complaint must be
    WRITTEN. A repair that fires silently is indistinguishable from one that
    was never needed."""
    _write(tmp_path)
    counted: list[tuple] = []
    usage = SimpleNamespace(record_edit_reindented=lambda role: counted.append(("fixed", role)))
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path, usage=usage)

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler := _Handler(),
    )
    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=TRUE_OLD, new_string=TRUE_NEW),
        handler,
    )

    assert counted == [("fixed", "coder")]


def test_a_repair_emits_a_notice_because_it_changed_what_the_run_did(tmp_path):
    """TODO.md lesson 5: a new guard is a new silence unless it is wired to
    something. This rewrites a model's arguments, so it says so."""
    _write(tmp_path)
    said: list[dict] = []
    trace = SimpleNamespace(notice=lambda payload, **kw: said.append({"payload": payload, **kw}))
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path, trace=trace)

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        _Handler(),
    )

    assert len(said) == 1
    assert said[0]["name"] == "reindent"
    assert said[0]["role"] == "coder"
    assert "index.html" in said[0]["payload"]


def test_bookkeeping_failure_never_ends_a_run(tmp_path):
    """CLAUDE.md §8a: every writer here swallows its own failure. A run that
    did its work must not be reported failed because a counter raised."""
    _write(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("counter exploded")

    mw = GutterIndentMiddleware(
        role="coder",
        project_path=tmp_path,
        usage=SimpleNamespace(record_edit_reindented=_boom),
        trace=SimpleNamespace(notice=_boom),
    )
    handler = _Handler()

    mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert handler.seen[0]["old_string"] == TRUE_OLD


# --------------------------------------------------------------------------
# The reported run, from its own bytes
# --------------------------------------------------------------------------

# Lifted verbatim from run `fc543fb2b82f`: the coder's t2 `edit_file` argument
# at ts 3701 (`debug-fc543fb2b82f.jsonl`), and the region of `index.html` it
# was aimed at. Embedded rather than read from
# `~/.local/state/rudra/runs/test-rudra-d80a39ab/fc543fb2b82f/` so this keeps
# testing after the archive is pruned (newest 20 runs per project).
RUN_OLD = (
    "@media (max-width: 768px) {\n"
    "              .section { padding: var(--spacing-3xl) 0; }\n"
    "          }"
)
RUN_NEW = (
    "@media (max-width: 768px) {\n"
    "              .section { padding: var(--spacing-3xl) 0; }\n"
    "          }\n"
    "\n"
    "          @media (max-width: 375px) {\n"
    "              .section { padding: var(--spacing-2xl) 0; }\n"
    "          }"
)
RUN_FILE = (
    "        .section {\n"
    "            padding: var(--spacing-4xl) 0;\n"
    "        }\n"
    "\n"
    "        @media (max-width: 768px) {\n"
    "            .section { padding: var(--spacing-3xl) 0; }\n"
    "        }\n"
    "\n"
    "        /* ==================================== */\n"
)


def test_the_reported_runs_own_edit_failed_and_is_repaired():
    """§8 test 6. The seven coder edits of run fc543fb2b82f changed zero
    bytes and cost two guard halts; this is the first of them."""
    assert RUN_FILE.count(RUN_OLD) == 0  # the defect, in the run's own bytes

    old, new = repair_indent(RUN_FILE, RUN_OLD, RUN_NEW)

    assert RUN_FILE.count(old) == 1
    assert old == (
        "        @media (max-width: 768px) {\n"
        "            .section { padding: var(--spacing-3xl) 0; }\n"
        "        }"
    )
    # Every line of the replacement lands at the file's own indentation --
    # 8 / 12 / 8, not the model's 0 / 14 / 10.
    assert new == (
        "        @media (max-width: 768px) {\n"
        "            .section { padding: var(--spacing-3xl) 0; }\n"
        "        }\n"
        "\n"
        "        @media (max-width: 375px) {\n"
        "            .section { padding: var(--spacing-2xl) 0; }\n"
        "        }"
    )
    assert RUN_FILE.replace(old, new).count("@media") == 2


def test_the_delta_the_run_exhibited_was_exactly_the_gutter_width():
    """Pins the mechanism, not just the outcome: `read_file` renders
    `f"{marker:>{marker_width}}  {line}"`, and two is that gutter."""
    file_lines = RUN_FILE.split("\n")
    old_lines = RUN_OLD.split("\n")
    start = 4  # the `@media` line in RUN_FILE

    deltas = {
        len(old_lines[k])
        - len(old_lines[k].lstrip(" "))
        - (len(file_lines[start + k]) - len(file_lines[start + k].lstrip(" ")))
        for k in range(1, len(old_lines))
    }

    assert deltas == {2}


# --------------------------------------------------------------------------
# The contract with upstream
# --------------------------------------------------------------------------


def test_upstream_still_renders_the_two_space_gutter_this_repairs():
    """The whole defect is this format string. If upstream changes it, this
    middleware is repairing a delta that no longer exists -- so pin it here
    rather than discover it from a run (`tests/test_deepagents_contract.py`
    is the same argument for `permissions=`)."""
    from deepagents.backends.utils import format_content_with_line_numbers

    shown = format_content_with_line_numbers("        .section {\n", start_line=166)

    assert shown == "166          .section {"
    # marker, then exactly two spaces, then the file's own eight.
    assert shown[len("166") : len("166") + 2] == "  "


def test_the_repaired_arguments_satisfy_upstreams_real_matcher():
    """End to end against the function that actually rejected these calls
    (`perform_string_replacement`, backends/utils.py:523), not a local
    reimplementation of it."""
    from deepagents.backends.utils import perform_string_replacement

    # The model's own argument: upstream refuses it, which is the run.
    refused = perform_string_replacement(SOURCE, GUTTERED_OLD, GUTTERED_NEW)
    assert isinstance(refused, str)
    assert refused.startswith("Error: String not found in file")

    # The repaired argument: upstream accepts it, and lands the replacement at
    # the file's own indentation.
    old, new = repair_indent(SOURCE, GUTTERED_OLD, GUTTERED_NEW)
    content, occurrences = perform_string_replacement(SOURCE, old, new)

    assert occurrences == 1
    assert (
        content
        == "<style>\n        .section {\n            padding: 8rem 0;\n        }\n</style>\n"
    )


def test_the_middleware_is_registered_for_every_writer():
    """A repair nothing constructs is a repair that never fires. `build.py`
    is the ONLY assembly path (CLAUDE.md §3), so asserting it there covers
    the coder, the tester and every other spec."""
    import ast
    import pathlib

    source = pathlib.Path("src/rudra/subagents/build.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "GutterIndentMiddleware" in called


# --------------------------------------------------------------------------
# The residue: what the model is told when the repair declines
# --------------------------------------------------------------------------
#
# Option B of the plan, and the reason it is needed: upstream's message is the
# model's own argument echoed back -- `Error: String not found in file:
# '<old_string>'` (backends/utils.py:552). It says nothing about indentation,
# so from the model's position it sent a string, the string came back
# unchanged, and nothing indicated what to change. Run fc543fb2b82f shows what
# follows: the same call re-sent byte-identically until the repeat guard
# killed the invocation, twice.


def test_an_ambiguous_match_is_told_where_the_text_actually_is(tmp_path):
    """The repair declines here on purpose -- editing the wrong one of two
    identical blocks is worse than failing. But declining to GUESS is not a
    reason to decline to EXPLAIN."""
    _write(tmp_path, SOURCE + SOURCE)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    result = mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert handler.seen == []  # the tool never ran: its answer is already known
    assert result.status == "error"
    assert "2 times at a different indentation" in result.content
    assert "indentation" in result.content


def test_the_actionable_message_quotes_the_files_own_bytes(tmp_path):
    """A model cannot correct an error whose message is its own input. The
    message must carry the file's version, at the file's indentation."""
    _write(tmp_path, SOURCE + SOURCE)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)

    result = mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        _Handler(),
    )

    assert TRUE_OLD in result.content
    assert "2-space" in result.content


def test_a_string_absent_for_another_reason_still_gets_upstreams_error(tmp_path):
    """Only pre-empt when there is something better to say. Upstream's own
    handler carries a trailing-newline recovery hint this must not shadow."""
    _write(tmp_path)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler("Error: String not found in file: 'nope'")

    result = mw.wrap_tool_call(
        _request(file_path="/index.html", old_string="nope\n  nothing", new_string="x\n  y"),
        handler,
    )

    assert len(handler.seen) == 1
    assert result == "Error: String not found in file: 'nope'"


def test_a_repaired_edit_never_reaches_the_message(tmp_path):
    """The happy path of the repair is still the happy path."""
    _write(tmp_path)
    mw = GutterIndentMiddleware(role="coder", project_path=tmp_path)
    handler = _Handler()

    result = mw.wrap_tool_call(
        _request(file_path="/index.html", old_string=GUTTERED_OLD, new_string=GUTTERED_NEW),
        handler,
    )

    assert result == "OK"
    assert handler.seen[0]["old_string"] == TRUE_OLD
