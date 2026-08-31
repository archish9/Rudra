"""The stub scanner — the stage a test-only gate cannot have."""

from __future__ import annotations

from rudra.verify.stubs import project_files, scan_stubs, source_files


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def messages(findings):
    return [finding.message for finding in findings]


def test_bare_pass_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    pass\n")
    findings = scan_stubs(tmp_path, [name])
    assert len(findings) == 1
    assert findings[0].line == 1
    assert "pass" in findings[0].message


def test_pass_in_an_except_block_is_not_a_stub(tmp_path):
    name = write(
        tmp_path,
        "a.py",
        "def handler():\n    try:\n        go()\n    except OSError:\n        pass\n",
    )
    assert scan_stubs(tmp_path, [name]) == ()


def test_empty_class_body_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "class Marker:\n    pass\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_while_true_pass_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def spin():\n    while True:\n        pass\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_ellipsis_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    ...\n")
    assert "..." in messages(scan_stubs(tmp_path, [name]))[0]


def test_docstring_only_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", 'def handler():\n    """Does the thing."""\n')
    assert "docstring" in messages(scan_stubs(tmp_path, [name]))[0]


def test_not_implemented_error_is_a_stub_bare_and_called(tmp_path):
    bare = write(tmp_path, "a.py", "def a():\n    raise NotImplementedError\n")
    called = write(tmp_path, "b.py", 'def b():\n    raise NotImplementedError("later")\n')
    assert len(scan_stubs(tmp_path, [bare, called])) == 2


def test_raising_another_error_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", 'def a():\n    raise ValueError("bad input")\n')
    assert scan_stubs(tmp_path, [name]) == ()


def test_todo_comment_is_reported(tmp_path):
    name = write(tmp_path, "a.py", "def a():\n    return 1  # TODO: handle None\n")
    findings = scan_stubs(tmp_path, [name])
    assert len(findings) == 1
    assert findings[0].line == 2


def test_the_word_todo_in_a_string_is_not_a_comment(tmp_path):
    # tokenize, not regex: a regex over lines cannot tell these apart.
    name = write(tmp_path, "a.py", 'def a():\n    return "TODO list"\n')
    assert scan_stubs(tmp_path, [name]) == ()


def test_unparseable_python_yields_nothing(tmp_path):
    # The syntax stage blocks first, so stubs never sees this in practice.
    name = write(tmp_path, "a.py", "def broken(\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_rust_todo_macro_is_a_stub(tmp_path):
    name = write(tmp_path, "a.rs", "fn go() {\n    todo!()\n}\n")
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_rust_unimplemented_macro_is_a_stub(tmp_path):
    name = write(tmp_path, "a.rs", "fn go() {\n    unimplemented!()\n}\n")
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_js_not_implemented_throw_is_a_stub(tmp_path):
    name = write(tmp_path, "a.ts", 'function go() {\n  throw new Error("Not implemented");\n}\n')
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_unknown_extension_is_skipped(tmp_path):
    name = write(tmp_path, "notes.md", "TODO: write this up\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_a_missing_file_is_skipped_not_raised(tmp_path):
    assert scan_stubs(tmp_path, ["gone.py"]) == ()


def test_source_files_prunes_skip_dirs(tmp_path):
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "node_modules/dep/index.js", "x\n")
    write(tmp_path, ".venv/lib/thing.py", "x = 1\n")
    write(tmp_path, "target/debug/build.rs", "fn a() {}\n")
    assert source_files(tmp_path) == ("app.py",)


# --- OPEN-63: two questions, one walk ---
#
# `source_files` answers "what can I scan for placeholders?" and
# `project_files` answers "what does this project contain?". They were one
# function, so `loop/engine.py::tree_snapshot` -- which asks the second --
# got the first one's answer and could not see a file the coder wrote
# unless it ended in a source suffix.


def test_project_files_reports_what_source_files_filters_out(tmp_path):
    """The four shapes the suffix filter drops, and all four are real.

    `requirements.txt` is run12's: a deliverable the ledger lost. The
    SQLite-URI name is run13's, twice. `README.md` is run10's completion
    message. `DONE` is OPEN-42's marker, the only one of the four the old
    behaviour was right about, and it was right by accident.
    """
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "requirements.txt", "flask\n")
    write(tmp_path, "README.md", "All tests should now pass.\n")
    write(tmp_path, "DONE", "finished\n")
    write(tmp_path, "file::memory:?cache=shared", "SQLite format 3\n")

    assert source_files(tmp_path) == ("app.py",)
    assert project_files(tmp_path) == (
        "DONE",
        "README.md",
        "app.py",
        "file::memory:?cache=shared",
        "requirements.txt",
    )


def test_project_files_prunes_skip_dirs(tmp_path):
    """Widening the suffix filter must not widen the directory pruning.

    Every one of these would otherwise be fingerprinted on every attempt --
    `node_modules` alone by the thousand -- and `.rudra/run/ledger.json`
    changes *during* the attempt that would be reading it.
    """
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "node_modules/pkg/package.json", "{}\n")
    write(tmp_path, "__pycache__/app.cpython-312.pyc", "\n")
    write(tmp_path, ".rudra/run/ledger.json", '{"tasks": []}\n')
    write(tmp_path, ".venv/pyvenv.cfg", "home = /usr\n")

    assert project_files(tmp_path) == ("app.py",)


def test_project_files_keeps_the_root_anchored_split(tmp_path):
    """A1.29 survives the widening: `out`, `build`, `dist`, `target` and
    `coverage` are build output AT THE ROOT and ordinary English words
    below it. Matched at every depth they hid `src/out/handler.py` from the
    gate, and a non-source file in the same place would go the same way."""
    write(tmp_path, "out/bundle.txt", "generated\n")
    write(tmp_path, "src/out/notes.txt", "hand-written\n")

    assert project_files(tmp_path) == ("src/out/notes.txt",)


def test_source_files_is_project_files_plus_the_suffix_filter(tmp_path):
    """The pin that keeps them one walk. If they ever diverge on pruning,
    the gate and the ledger have two opinions about build output again."""
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "src/main.rs", "fn main() {}\n")
    write(tmp_path, "notes.md", "hello\n")
    write(tmp_path, "node_modules/pkg/index.js", "module.exports = 1;\n")

    assert set(source_files(tmp_path)) <= set(project_files(tmp_path))
    assert set(project_files(tmp_path)) - set(source_files(tmp_path)) == {"notes.md"}


def test_the_three_copies_of_the_pruning_rule_agree(tmp_path):
    """The pin this module's own comment has claimed since A1.29 (OPEN-64).

    `stubs.py` says "tests/test_verify_stubs.py checks the two agree" and
    no such test existed, so three copies of one rule drifted twice in two
    days: OPEN-63 on the suffix filter, OPEN-64 on the scoping. The
    root-anchored names must be identical everywhere; the any-depth sets
    may differ only by the editor directories §7 of OPEN-64's document
    records as a separate, unfiled question.
    """
    from rudra.filesystem import tree
    from rudra.verify import stubs

    assert tree._ROOT_ANCHORED_SKIP_DIRS == stubs._ROOT_ANCHORED_SKIP_DIRS

    # `.idea`/`.vscode`: the gate skips them, the model's tree does not.
    # Known, recorded, and not this item -- but pinned so a fourth
    # divergence cannot arrive unnoticed.
    assert stubs._ALWAYS_SKIP_DIRS - tree._ALWAYS_SKIP_DIRS == {".idea", ".vscode"}
    assert tree._ALWAYS_SKIP_DIRS - stubs._ALWAYS_SKIP_DIRS == set()


def test_is_build_output_applies_the_root_anchored_split(tmp_path):
    """The one predicate both walks call, and the whole of OPEN-64.

    `loop/engine.py` kept its own any-depth copy, so in a git project
    `src/out/handler.py` never reached `files_touched`.
    """
    from rudra.verify.stubs import is_build_output

    assert is_build_output("out/bundle.js") is True
    assert is_build_output("node_modules/pkg/index.js") is True
    assert is_build_output("src/out/handler.py") is False
    assert is_build_output("packages/web/dist/index.ts") is False
    assert is_build_output("app.py") is False


def test_a_file_named_like_a_build_dir_is_not_build_output(tmp_path):
    """A *file* called `dist` at the root is a deliverable, not a directory.

    `parts[:-1]` -- the filename is never a directory name. Recorded as a
    behaviour change rather than smuggled: `filesystem/tree.py` still
    prunes it, so the model is not shown a file the ledger records. That
    divergence is a smaller member of this family and is its own item.
    """
    from rudra.verify.stubs import is_build_output

    assert is_build_output("dist") is False
    write(tmp_path, "dist", "the deliverable\n")

    assert project_files(tmp_path) == ("dist",)
