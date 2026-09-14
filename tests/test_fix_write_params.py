"""Unit tests for markdown-fence stripping and path-argument repair.

The fence half is the behavior compat/overwrite_backend.py used to provide
at the backend layer, with the regex `^```[^\n]*\n(.*)\n```$`. That file no
longer exists, so no line number is cited (A4.9).
U.3 deletes that backend, so the middleware must cover at least as much.

The path half is OPEN-8: a model that emits `/. rudra/AGENTS.md` for
`/.rudra/AGENTS.md`.
"""

from __future__ import annotations

from types import SimpleNamespace

from rudra.middleware.fix_write_params import (
    FixWriteParamsMiddleware,
    _is_directory_placeholder,
    _is_prose_not_content,
    _repair_split_dot_segment,
    _strip_fences,
)


def _fixed(name: str, args: dict, *, sandbox: bool = False) -> dict:
    """Run one tool call through the middleware and return its args."""
    request = SimpleNamespace(tool_call={"name": name, "args": args})
    middleware = FixWriteParamsMiddleware(strip_sandbox_prefixes=sandbox)
    return middleware._fix_args(request).tool_call["args"]


def test_strips_language_tagged_fence():
    assert _strip_fences('```python\nprint("hi")\n```') == 'print("hi")\n'


def test_strips_bare_fence():
    assert _strip_fences('```\nprint("hi")\n```') == 'print("hi")\n'


def test_leaves_unfenced_content_alone():
    source = 'print("hi")\n'
    assert _strip_fences(source) == source


def test_strips_fence_with_trailing_newline():
    assert _strip_fences('```python\nprint("hi")\n```\n') == 'print("hi")\n'


def test_strips_fence_with_info_string_attributes():
    r"""The backend's `[^\n]*` matched this; the middleware's
    `[a-zA-Z0-9_\-]*` did not. Deleting the backend must not shrink
    coverage. See TODO.md U.15."""
    assert _strip_fences('```py title="x"\nprint("hi")\n```') == 'print("hi")\n'


def test_keeps_inner_fences_when_stripping_outer():
    content = "```markdown\nSee:\n```\ninner\n```\n```"
    assert _strip_fences(content) == "See:\n```\ninner\n```\n"


def test_leaves_unterminated_fence_alone():
    source = '```python\nprint("hi")\n'
    assert _strip_fences(source) == source


def test_keeps_a_readme_that_legitimately_opens_and_closes_with_a_fence():
    """The mirror image of test_keeps_inner_fences_when_stripping_outer.

    Both inputs start with ``` and end with ```, and both contain further
    fences inside. What separates them is the info string: a model that
    wraps a whole markdown file announces that with ```markdown, so the
    outer fence is a wrapper and comes off. A README's own first line is
    a ```bash block -- the fences are the content, and stripping them
    deletes the file's first opening fence and last closing fence.
    """
    readme = "```bash\nnpm i\n```\n\nSome text\n\n```js\nconst a = 1;\n```"
    assert _strip_fences(readme) == readme


# --- OPEN-8: a dot-segment the model split with whitespace -----------------


def test_repairs_the_observed_malformation():
    """Measured, not invented. nvidia/nemotron emitted this four times in a
    row against a project whose `ls` had just returned `/.rudra/`."""
    assert _repair_split_dot_segment("/. rudra/AGENTS.md") == "/.rudra/AGENTS.md"


def test_repairs_a_dot_segment_anywhere_in_the_path():
    assert _repair_split_dot_segment("/src/. github/workflows") == "/src/.github/workflows"


def test_repairs_a_relative_path_at_the_start_of_the_string():
    assert _repair_split_dot_segment(". rudra/config.toml") == ".rudra/config.toml"


def test_leaves_a_legitimate_space_in_a_filename_alone():
    """The reason the rule anchors to a dot-segment rather than stripping
    whitespace: `My Documents` is a real directory on every desktop OS."""
    for path in ("/My Documents/notes.txt", "/a/b c/d e.py", "/Program Files/x"):
        assert _repair_split_dot_segment(path) == path, path


def test_leaves_ordinary_dot_segments_alone():
    """`.` and `..` are path syntax, and a dot with no whitespace after it
    is not the malformation."""
    for path in ("./x", "../x", "/a/./b", "/a/../b", "/.rudra/AGENTS.md", "."):
        assert _repair_split_dot_segment(path) == path, path


def test_leaves_a_dot_inside_a_segment_alone():
    """Anchored to `^` or `/`, so a dot mid-segment is never touched -- a
    version directory like `v1. 2` keeps whatever the user named it."""
    assert _repair_split_dot_segment("/pkg/v1. 2/mod.py") == "/pkg/v1. 2/mod.py"


def test_leaves_a_trailing_dot_space_alone():
    """Nothing follows the whitespace, so there is no segment to rejoin and
    no evidence the model meant a dotfile."""
    assert _repair_split_dot_segment("/a/. ") == "/a/. "


# --- the repair reaches the tools, with sandbox stripping OFF -------------


def test_read_file_is_repaired_by_default():
    """The failing call from the report. `read_file` was previously
    untouched with `[compat] sandbox_paths` off, because the only path loop
    sat inside that opt-in branch."""
    args = _fixed("read_file", {"file_path": "/. rudra/AGENTS.md"})
    assert args["file_path"] == "/.rudra/AGENTS.md"


def test_write_file_is_repaired_by_default():
    """The severity case. A read of the mangled path errors loudly; a WRITE
    creates a directory literally named `. rudra` and reports success --
    and the shipped AGENTS.md tells the model to `edit_file` that path."""
    args = _fixed("write_file", {"file_path": "/. rudra/AGENTS.md", "content": "x"})
    assert args["file_path"] == "/.rudra/AGENTS.md"


def test_edit_file_and_ls_and_glob_are_repaired_too():
    assert _fixed("edit_file", {"file_path": "/. rudra/x"})["file_path"] == "/.rudra/x"
    assert _fixed("ls", {"path": "/. rudra"})["path"] == "/.rudra"
    assert _fixed("glob", {"pattern": "/. rudra/**"})["pattern"] == "/.rudra/**"


def test_the_repair_survives_the_filename_alias():
    """`filename` is renamed to `file_path` first, so the repair has to run
    after the alias or it would clean a key that no longer exists."""
    args = _fixed("write_file", {"filename": "/. rudra/x", "content": "y"})
    assert args["file_path"] == "/.rudra/x"


def test_content_is_never_path_repaired():
    """File content is data. A line reading `. rudra` inside a document is
    not a path and must survive verbatim."""
    args = _fixed("write_file", {"file_path": "/a.md", "content": "see /. rudra/x\n"})
    assert args["content"] == "see /. rudra/x\n"


# --- OPEN-22: a write whose only purpose is to make a directory -----------
#
# Measured in the first full --auto --allow-shell run: the tester wanted a
# `tests/` directory, had no mkdir tool, and wrote
# `write_file('/tests', '# This is a placeholder to create the directory')`.
# That succeeded and left a 47-byte FILE named `tests`, which doomed the
# five later tasks needing `tests/` to be a directory.


def _handled(name: str, args: dict):
    """Run one call through wrap_tool_call; returns the refusal or SENTINEL."""
    sentinel = object()
    request = SimpleNamespace(tool_call={"name": name, "args": args, "id": "c1"})
    middleware = FixWriteParamsMiddleware()
    return middleware.wrap_tool_call(request, lambda _req: sentinel), sentinel


def test_the_measured_directory_placeholder_is_refused():
    result, sentinel = _handled(
        "write_file",
        {"file_path": "/tests", "content": "# This is a placeholder to create the directory"},
    )
    assert result is not sentinel
    assert result.status == "error"
    assert "created implicitly" in result.content
    # It must say what to do instead, or the model retries the same shape.
    assert "/tests/<name>.py" in result.content


def test_a_real_file_with_that_name_still_writes():
    """`tests/test_models.py` is the call the model should have made."""
    result, sentinel = _handled(
        "write_file",
        {"file_path": "tests/test_models.py", "content": "# a comment\nassert True\n"},
    )
    assert result is sentinel


def test_suffixless_files_people_actually_write_are_untouched():
    """The bar this guard is held to: it must not fire on a real file.

    LICENSE, Makefile and Dockerfile are all suffix-less; all carry real
    content, so none of them look like a directory placeholder.
    """
    for name, content in (
        ("LICENSE", "Apache License\nVersion 2.0\n"),
        ("Makefile", "# build\nall:\n\tpytest\n"),
        ("Dockerfile", "# base\nFROM python:3.12\n"),
    ):
        assert not _is_directory_placeholder(name, content), name


def test_dotfiles_are_never_treated_as_directory_placeholders():
    """`.gitignore` and `.env` have no suffix either, and a comment-only
    `.gitignore` is useless but legal. Refusing one would be a false
    positive on a file the user may well have asked for."""
    assert not _is_directory_placeholder(".gitignore", "# nothing yet")
    assert not _is_directory_placeholder(".env", "# set me")


def test_a_long_comment_only_file_is_not_a_placeholder():
    """Three lines is the cutoff. A comment-only file of real length is
    someone's notes, not a mkdir substitute."""
    assert not _is_directory_placeholder("notes", "# one\n# two\n# three\n# four")


def test_content_that_is_not_a_string_is_not_a_placeholder():
    """A model emitting a list for `content` gets the existing correctable
    error from the tool, not an AttributeError in here (CR-E11's lesson)."""
    assert not _is_directory_placeholder("tests", ["# x"])


def test_a_trailing_slash_still_reads_as_a_directory_attempt():
    assert _is_directory_placeholder("tests/", "# placeholder")


# ---------------------------------------------------------------------------
# OPEN-97: the closing summary written INTO the deliverable.
#
# Run `2cde3406f7d6` at=936.0: the coder replaced 24,800 bytes of finished
# HTML with 329 bytes of English beginning "Done. Created ...". `write_file`
# answered "Updated file /src/iphone15.html" and seven mechanisms let it
# through -- `_is_directory_placeholder` among them, because it declines any
# path carrying a suffix. The next agent spent 180 s and seventeen `execute`
# calls proving with `xxd` that an .html file contained English.
#
# The must-NOT-refuse half of this table is the important half: this rule's
# failure mode is refusing real work.

PROSE = (
    "Done. Created /src/iphone15.html with all required elements: responsive "
    "CSS with mobile-first design and breakpoints, Apple design language "
    "styling (SF Pro fonts, Apple color palette), JavaScript-based "
    "tabs/accordion/form functionality, and user content placeholders for "
    "iPhone 15 marketing page. Single self-contained HTML file."
)


def test_the_measured_prose_write_is_not_content():
    assert _is_prose_not_content("src/iphone15.html", PROSE) is True


def test_markup_containing_its_own_syntax_is_content():
    """Property 3 is the load-bearing one: no valid HTML has no "<"."""
    assert _is_prose_not_content("src/x.html", "<p>Done. Created the page.</p>") is False


def test_unknown_file_types_decline():
    """A closed suffix table. Prose in a .md or .txt is the point of them."""
    assert _is_prose_not_content("README.md", PROSE) is False
    assert _is_prose_not_content("notes.txt", PROSE) is False
    assert _is_prose_not_content("LICENSE", PROSE) is False


def test_template_markers_decline():
    """A Jinja/Handlebars fragment legitimately carries no markup of its own."""
    for body in ("{{ body }}", "{% block x %}", "<%= x %>", "${name}", "$(name)"):
        assert _is_prose_not_content("t.html", body) is False, body


def test_an_empty_write_is_a_different_intention():
    """Truncation to zero is out of scope and possibly legitimate."""
    assert _is_prose_not_content("t.html", "") is False
    assert _is_prose_not_content("t.html", "   \n  ") is False


def test_real_source_carrying_its_own_syntax_is_content():
    assert _is_prose_not_content("s.py", "x = 1") is False
    assert _is_prose_not_content("s.py", "# TODO: write this later.") is False
    assert _is_prose_not_content("s.css", "body { color: red; }") is False
    assert _is_prose_not_content("s.json", '{"a": 1}') is False
    assert _is_prose_not_content("s.js", "const a = 1;") is False


def test_long_content_declines_whatever_it_says():
    """1,000 bytes is the cutoff. A summary is short; a file is not."""
    assert _is_prose_not_content("a.html", "<" + "x" * 2000) is False
    assert _is_prose_not_content("a.html", "Done. " + "word " * 400 + ".") is False


def test_a_short_prose_line_that_is_not_prose_shaped_declines():
    """Four conditions, ALL required -- the fourth is sentence shape."""
    # No terminal punctuation.
    assert _is_prose_not_content("a.html", "Done creating the page for iphone fifteen now") is False
    # Not capitalised.
    assert (
        _is_prose_not_content("a.html", "done. created the page with all required parts.") is False
    )
    # Too few words to be a sentence about anything.
    assert _is_prose_not_content("a.html", "All done here.") is False


def test_content_that_is_not_a_string_is_not_prose():
    assert _is_prose_not_content("a.html", ["Done."]) is False


def test_up_to_three_lines_of_prose_still_reads_as_a_summary():
    body = (
        "Done. Created the page as requested.\nIt has every element listed.\nNothing else remains."
    )
    assert _is_prose_not_content("a.html", body) is True
    assert _is_prose_not_content("a.html", body + "\nAnd a fourth line here.") is False


def test_the_prose_write_is_refused_before_the_backend_sees_it(tmp_path):
    """§8.2: the bytes on disk must be untouched, not merely a string returned."""
    target = tmp_path / "iphone15.html"
    target.write_text("<html><body>real work</body></html>", encoding="utf-8")

    reached = []

    def handler(_req):
        target.write_text(PROSE, encoding="utf-8")
        reached.append(True)
        return "Updated file"

    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/src/iphone15.html", "content": PROSE},
            "id": "c1",
        }
    )
    result = FixWriteParamsMiddleware().wrap_tool_call(request, handler)

    assert reached == []
    assert target.read_text(encoding="utf-8") == "<html><body>real work</body></html>"
    assert result.status == "error"
    assert result.content.startswith("REJECTED:")
    # It must name the channel the model actually wanted, or it retries.
    assert "REPLY" in result.content


def test_the_prose_refusal_does_not_lead_with_a_failure_marker():
    """§10's first rule, and OPEN-94's bug. `Error:` here would be counted by
    `subagents/runner.py` and three in a row kill the invocation."""
    from rudra.middleware.fix_write_params import _PROSE_NOT_CONTENT
    from rudra.trace.stream import _FIRST_LINE_MARKERS, looks_like_error

    text = _PROSE_NOT_CONTENT.format(path="src/x.html", suffix=".html")
    assert text.startswith("REJECTED:")
    assert not looks_like_error(text)
    assert "REJECTED:" not in _FIRST_LINE_MARKERS


def test_a_real_html_write_still_reaches_the_backend():
    result, sentinel = _handled(
        "write_file",
        {"file_path": "src/index.html", "content": "<!doctype html><h1>Hi</h1>"},
    )
    assert result is sentinel


def test_the_two_content_rules_do_not_shadow_each_other():
    """§8.5. Independent rules: each fires on its own shape and neither on
    the other's."""
    assert _is_directory_placeholder("tests", "# placeholder") is True
    assert _is_prose_not_content("tests", "# placeholder") is False
    assert _is_prose_not_content("src/iphone15.html", PROSE) is True
    assert _is_directory_placeholder("src/iphone15.html", PROSE) is False


def test_a_refused_prose_write_is_counted_and_announced():
    """CLAUDE.md §8a: what the guard prevents leaves no mark on tokens,
    seconds or tool results, so it needs its own number."""
    counted: list[str] = []
    notices: list[dict] = []
    usage = SimpleNamespace(record_write_rejected_as_prose=counted.append)
    trace = SimpleNamespace(
        notice=lambda message, role=None, name=None: notices.append(
            {"message": message, "role": role, "name": name}
        )
    )
    middleware = FixWriteParamsMiddleware(role="coder", usage=usage, trace=trace)
    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/src/iphone15.html", "content": PROSE},
            "id": "c1",
        }
    )
    middleware.wrap_tool_call(request, lambda _req: "unreachable")

    assert counted == ["coder"]
    assert len(notices) == 1
    assert notices[0]["name"] == "prose-write"
    assert notices[0]["role"] == "coder"
    # The path must be in the payload: a false positive is a REFUSED REAL
    # WRITE and has to be visible by eye without a parser.
    assert "/src/iphone15.html" in notices[0]["message"]


def test_bookkeeping_failure_never_reaches_the_caller():
    """A run that did its work must not fail because a counter raised."""

    def boom(*_a, **_k):
        raise RuntimeError("no")

    middleware = FixWriteParamsMiddleware(
        role="coder",
        usage=SimpleNamespace(record_write_rejected_as_prose=boom),
        trace=SimpleNamespace(notice=boom),
    )
    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": "/src/x.html", "content": PROSE},
            "id": "c1",
        }
    )
    result = middleware.wrap_tool_call(request, lambda _req: "unreachable")
    assert result.content.startswith("REJECTED:")


# ---------------------------------------------------------------------------
# OPEN-104: a file written to ANNOUNCE that the work is finished.
#
# Run `f845b496a2aa`: the coder wrote `COMPLETION` into the project root
# twice, spelled with the host path -- "All tasks complete." at=121.7, then
# "Task t2 complete: database.py implemented ..." at=176.1 -- and
# `write_file` answered "Updated file /COMPLETION" both times. OPEN-42's run7
# wrote `/DONE` and `/task_complete.txt` eleven times. Neither content rule
# above can see either: the placeholder rule wants comments, and the prose
# rule wants a suffix from its closed table and eight words, while the first
# measured write is three.
#
# As with OPEN-97, the must-NOT-refuse half is the important half.

MEASURED_ROOT = "/private/tmp/rudra-verify-a-20260909-182903"
MEASURED_COMPLETIONS = (
    "All tasks complete.",
    "Task t2 complete: database.py implemented with SQLite CRUD operations for "
    "Todo items (create, read, update, delete).",
)


def _announces(path: str, content: object, root: str | None = MEASURED_ROOT) -> bool:
    from pathlib import Path

    from rudra.middleware.fix_write_params import _is_completion_announcement

    return _is_completion_announcement(path, content, Path(root) if root else None)


def _completion_call(path: str, content: str, *, root: str | None = MEASURED_ROOT, **kwargs):
    from pathlib import Path

    reached: list[bool] = []
    request = SimpleNamespace(
        tool_call={
            "name": "write_file",
            "args": {"file_path": path, "content": content},
            "id": "c1",
        }
    )
    middleware = FixWriteParamsMiddleware(project_path=Path(root) if root else None, **kwargs)
    result = middleware.wrap_tool_call(request, lambda _req: reached.append(True) or "Updated file")
    return result, reached


def test_a_completion_file_at_the_root_is_refused():
    """The regression pin: run `f845b496a2aa`'s two writes, verbatim."""
    for content in MEASURED_COMPLETIONS:
        result, reached = _completion_call(f"{MEASURED_ROOT}/COMPLETION", content)

        assert reached == [], content
        assert result.status == "error"
        assert result.content.startswith("REJECTED:")
        # It must name the channel the model actually wanted, or it retries.
        assert "reply" in result.content.lower()
        assert "call no tool" in result.content


def test_open_42s_markers_are_refused_too():
    """Run7's names, four runs earlier, in the virtual spelling it used."""
    sentence = "Task t5 completed: JSON validation fixed in Flask todo backend."
    assert _announces("/DONE", sentence)
    assert _announces("/task_complete.txt", sentence)


def test_the_marker_is_a_category_not_one_runs_spelling():
    """OPEN-42 §10: the model invents the name, so a list of names seen so
    far would not cover the next one. The NAME is read as words."""
    sentence = "All tasks are complete."
    for name in (
        "COMPLETE.md",
        "ALL_DONE",
        "tasks-completed.txt",
        "FINISHED",
        "SUCCESS",
        "Task_Done.md",
        "completion.txt",
    ):
        assert _announces(name, sentence), name


def test_a_makefile_is_not_refused():
    """The pin against over-widening, and the one that matters: suffix-less
    and root-level files a person writes by hand, each with a sentence in
    it, every one a REFUSED REAL WRITE if this fired."""
    sentence = "Build the project before running the tests."
    for name in (
        "Makefile",
        "Dockerfile",
        "LICENSE",
        "NOTICE",
        "CHANGELOG",
        "Procfile",
        "CODEOWNERS",
        "Jenkinsfile",
        ".gitignore",
        "README.md",
        "TODO.md",
        "STATUS.md",
        "done.py",
        "completion.sh",
        "success_page.html",
    ):
        assert not _announces(name, sentence), name
        assert not _announces(f"/{name}", sentence), name
        assert not _announces(f"{MEASURED_ROOT}/{name}", sentence), name


def test_a_completion_name_with_real_content_is_not_refused():
    """Both conditions are required. A `COMPLETE` stamp file is a real build
    artefact, and what it holds is not a sentence."""
    for content in (
        "",
        "1",
        '{"status": "ok"}',
        "2026-09-14T10:00:00Z",
        "complete -F _rudra rudra",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "{{ build_status }}",
        "Done.",
    ):
        assert not _announces("COMPLETE", content), content
    four_lines = "Build finished.\nAll stages passed.\nArtifacts uploaded.\nSee the log."
    assert not _announces("DONE.md", four_lines)
    assert not _announces("DONE", "Task complete. " * 100)
    assert not _announces("DONE", ["Task complete."])


def test_a_completion_name_outside_the_root_is_not_refused():
    """`build/COMPLETE` is somebody's artefact, whatever it says."""
    sentence = "Build complete."
    assert not _announces("build/COMPLETE", sentence)
    assert not _announces("/build/COMPLETE", sentence)
    assert not _announces(f"{MEASURED_ROOT}/build/DONE", sentence)
    assert not _announces("/src/done.txt", sentence)


def test_without_a_project_root_only_a_single_segment_is_judged():
    """With no root the host spelling cannot be placed, so it declines --
    the degraded mode is the old accepting one, never a wider refusal."""
    sentence = "All tasks complete."
    assert _announces("/COMPLETION", sentence, root=None)
    assert _announces("COMPLETION", sentence, root=None)
    assert not _announces(f"{MEASURED_ROOT}/COMPLETION", sentence, root=None)


def test_the_completion_refusal_is_counted_as_a_tool_failure():
    """Pinned against the counter itself, not the text.

    The plan (§7.1.5) asked for the opposite, inheriting a claim OPEN-118
    shows is false for every `REJECTED:` refusal: `message_is_error` answers
    from `status="error"` before it reads a word. Kept counted on purpose --
    the owner's OPEN-103 decision: a coder halt still runs the gate, while an
    uncounted refusal a model ignores is bounded only by 80 calls.
    """
    from rudra.trace.stream import is_rudra_refusal, message_is_error

    result, _ = _completion_call("/COMPLETION", MEASURED_COMPLETIONS[0])

    assert message_is_error(result) is True
    assert is_rudra_refusal(result) is False


def test_a_refused_completion_file_is_counted_and_announced():
    """CLAUDE.md §8a: bytes that never reach disk leave no other mark."""
    from rudra.middleware.fix_write_params import COMPLETION_FILE_NOTICE

    counted: list[str] = []
    notices: list[dict] = []
    usage = SimpleNamespace(record_completion_file_refused=counted.append)
    trace = SimpleNamespace(
        notice=lambda message, role=None, name=None: notices.append(
            {"message": message, "role": role, "name": name}
        )
    )
    _completion_call(
        f"{MEASURED_ROOT}/COMPLETION",
        MEASURED_COMPLETIONS[1],
        role="coder",
        usage=usage,
        trace=trace,
    )

    assert COMPLETION_FILE_NOTICE == "completion-file"
    assert counted == ["coder"]
    assert len(notices) == 1
    assert notices[0]["name"] == COMPLETION_FILE_NOTICE
    assert notices[0]["role"] == "coder"
    # A false positive is a REFUSED REAL WRITE: the path, visible by eye.
    assert "COMPLETION" in notices[0]["message"]


def test_a_completion_bookkeeping_failure_never_reaches_the_caller():
    def boom(*_a, **_k):
        raise RuntimeError("no")

    result, reached = _completion_call(
        "/DONE",
        MEASURED_COMPLETIONS[0],
        role="coder",
        usage=SimpleNamespace(record_completion_file_refused=boom),
        trace=SimpleNamespace(notice=boom),
    )
    assert reached == []
    assert result.content.startswith("REJECTED:")


def test_the_three_content_rules_do_not_shadow_each_other():
    """Each fires on its own shape and neither of the others does."""
    measured = MEASURED_COMPLETIONS[0]
    assert _announces("/COMPLETION", measured) is True
    assert _is_directory_placeholder("/COMPLETION", measured) is False
    assert _is_prose_not_content("/COMPLETION", measured) is False
    assert _announces("src/iphone15.html", PROSE) is False
    assert _announces("tests", "# placeholder") is False
