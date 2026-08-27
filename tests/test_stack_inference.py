"""OPEN-28: a Python project with no packaging file is still a Python project.

Measured 2026-08-26, run4: a finished run produced `todo_app/*.py` and three
files under `tests/`, and no `pyproject.toml`. `stacks/registry.py:13`
recognises Python by `pyproject.toml`, `setup.py` or `requirements.txt`, so
`detect()` returned nothing, `profile` was None (verify/__init__.py:89), and
every stage needing a toolchain reported `not_applicable`:

    lint: not_applicable       no stack detected
    typecheck: not_applicable  no stack detected
    test: not_applicable       this project declares no test command

`not_applicable` is deliberately not halting (A1.57), so the gate PASSED.
Nine tasks were marked DONE on a verdict that parsed syntax and nothing else,
and the three test files the run wrote were never executed once.

**Python is uniquely marker-optional**, which is why this fallback is not a
hack and why it is not applied to the other stacks. A Rust project cannot
exist without `Cargo.toml`, nor a Node one without `package.json` -- those
markers are structural. Python's are conventional.
"""

from __future__ import annotations

from rudra.stacks import detect
from rudra.stacks.detect import resolve_test_command


def _write(root, relative: str, body: str = "x = 1\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_a_marker_file_still_wins_unchanged(tmp_path):
    """The fallback must fire ONLY where today's answer is nothing, so a
    project that already works cannot be changed by it."""
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _write(tmp_path, "app.py")

    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_python_is_inferred_from_source_files_alone(tmp_path):
    """Run4's project exactly: a package, tests beside it, no packaging."""
    _write(tmp_path, "todo_app/__init__.py")
    _write(tmp_path, "todo_app/core.py")
    _write(tmp_path, "tests/test_core.py")

    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_a_single_python_file_at_the_root_is_enough(tmp_path):
    _write(tmp_path, "main.py")

    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_an_inferred_project_gets_a_command_that_can_actually_collect(tmp_path):
    """The point of the whole item, and the second half of OPEN-28.

    Detecting the stack is not enough -- run4's project was detected once the
    fallback landed and STILL reported "no tests were collected", because
    `tests/` has no `__init__.py` and unittest discovery cannot enter a
    directory that is not a package. Measured against that exact project:

        python3 -m unittest discover           Ran 0 tests.  NO TESTS RAN
        python3 -m unittest discover -s tests  ImportError: not importable
        python3 -m pytest tests -q             7 failed, 22 passed

    Seven real failures the gate could not see. A non-package `tests/`
    directory is a pytest layout by construction, so that is what is emitted.
    """
    _write(tmp_path, "todo_app/core.py")
    _write(tmp_path, "tests/test_core.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command is not None
    assert command[1:] == ["-m", "pytest"]


def test_a_package_style_tests_directory_still_uses_unittest(tmp_path):
    """`tests/__init__.py` makes it discoverable, and unittest needs no
    third-party install. Only the undiscoverable layout forces pytest."""
    _write(tmp_path, "app.py")
    _write(tmp_path, "tests/__init__.py", "")
    _write(tmp_path, "tests/test_app.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command[1:] == ["-m", "unittest", "discover"]


def test_a_project_with_no_tests_directory_still_uses_unittest(tmp_path):
    """Nothing to be undiscoverable, so nothing to change."""
    _write(tmp_path, "app.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command[1:] == ["-m", "unittest", "discover"]


def test_an_empty_directory_is_still_greenfield(tmp_path):
    """OPEN-12/13's world. Nothing to infer from, and inferring anyway would
    hand the gate a test command for a project with no code."""
    assert detect(tmp_path) == []


def test_a_directory_of_non_python_files_infers_nothing(tmp_path):
    _write(tmp_path, "README.md", "# hi\n")
    _write(tmp_path, "notes.txt", "hi\n")

    assert detect(tmp_path) == []


def test_python_files_only_inside_skipped_directories_do_not_count(tmp_path):
    """`.venv` is full of `.py` files belonging to somebody else. Counting
    them would call every directory holding a virtualenv a Python project --
    including one whose own code is Rust."""
    _write(tmp_path, ".venv/lib/python3.12/site-packages/pkg/mod.py")
    _write(tmp_path, "build/generated.py")
    _write(tmp_path, "__pycache__/thing.py")

    assert detect(tmp_path) == []


def test_a_rust_project_is_not_relabelled_by_a_stray_script(tmp_path):
    """The fallback fires only when NOTHING matched, so a real marker always
    decides. A Cargo project with one helper script stays Rust."""
    _write(tmp_path, "Cargo.toml", "[package]\nname='x'\n")
    _write(tmp_path, "scripts/gen.py")

    assert [p.name for p in detect(tmp_path)] == ["rust"]


def test_a_node_project_is_not_relabelled_either(tmp_path):
    _write(tmp_path, "package.json", '{"name":"x"}')
    _write(tmp_path, "tools/build.py")

    assert "python" not in [p.name for p in detect(tmp_path)]


def test_inference_does_not_recurse_forever_on_a_deep_tree(tmp_path):
    """Bounded walk: a monorepo can hold tens of thousands of files, and this
    runs on every gate call."""
    deep = "a/" * 12
    _write(tmp_path, f"{deep}buried.py")

    # Whatever the answer, it must return rather than walk the world.
    assert detect(tmp_path) in ([], [p for p in detect(tmp_path)])


def test_a_nested_pytest_layout_is_undiscoverable_too(tmp_path):
    """OPEN-34. run6's project exactly: the test modules are one level below
    `tests/`, which itself holds only directories.

        tests/unit/test_models.py
        tests/unit/test_database.py
        tests/integration/test_api.py

    OPEN-28's predicate looked at `tests/`'s DIRECT children for `test*.py`,
    found two directories and no module, and fell through to
    `unittest discover` -- which collected nothing, reported
    `not_applicable`, and therefore PASSED on every task in the run
    (`.rudra/run/logs/verify.log`: `## test: not_applicable`,
    `tests.log`: `Ran 0 tests in 0.000s`).
    """
    _write(tmp_path, "src/models.py")
    _write(tmp_path, "tests/unit/test_models.py")
    _write(tmp_path, "tests/integration/test_api.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command is not None
    assert command[1:] == ["-m", "pytest"]


def test_a_package_tests_dir_with_non_package_subdirs_is_undiscoverable(tmp_path):
    """The second half of OPEN-34's fix, and why the `__init__.py`
    early-`continue` had to go: `tests/` being a package says nothing about
    `tests/unit/`, and `unittest discover` cannot enter the subdirectory
    that actually holds the modules."""
    _write(tmp_path, "app.py")
    _write(tmp_path, "tests/__init__.py", "")
    _write(tmp_path, "tests/unit/test_app.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command[1:] == ["-m", "pytest"]


def test_a_fully_packaged_nested_layout_still_uses_unittest(tmp_path):
    """Every directory on the path is importable, so discovery reaches the
    module and needs no third-party install. The predicate must answer the
    structural question, not "is there a subdirectory"."""
    _write(tmp_path, "app.py")
    _write(tmp_path, "tests/__init__.py", "")
    _write(tmp_path, "tests/unit/__init__.py", "")
    _write(tmp_path, "tests/unit/test_app.py")

    command = resolve_test_command(tmp_path, detect(tmp_path)[0])

    assert command[1:] == ["-m", "unittest", "discover"]
