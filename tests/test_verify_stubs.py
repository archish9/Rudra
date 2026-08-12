"""The stub scanner — the stage a test-only gate cannot have."""

from __future__ import annotations

from rudra.verify.stubs import scan_stubs, source_files


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
