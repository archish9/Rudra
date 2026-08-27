"""Lint and typecheck command resolution, per stack."""

from __future__ import annotations

import json
import sys

from rudra.stacks.detect import resolve_lint_command, resolve_typecheck_command
from rudra.stacks.registry import ANGULAR, NODE, PYTHON, REACT, RUST


def make_venv_binary(tmp_path, name):
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    executable = binaries / name
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def write_package_json(tmp_path, payload):
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")


def test_python_lint_prefers_the_project_venv(tmp_path):
    executable = make_venv_binary(tmp_path, "ruff")
    resolution = resolve_lint_command(tmp_path, PYTHON)
    assert resolution.status == "ok"
    assert resolution.argv[0] == str(executable)
    assert "check" in resolution.argv


def test_python_lint_falls_back_to_the_bundled_ruff(tmp_path):
    resolution = resolve_lint_command(tmp_path, PYTHON)
    assert resolution.status == "ok"
    assert resolution.argv[:3] == (sys.executable, "-m", "ruff")


def test_python_typecheck_prefers_the_project_venv(tmp_path):
    executable = make_venv_binary(tmp_path, "mypy")
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.argv[0] == str(executable)
    assert "--ignore-missing-imports" not in resolution.argv


def test_bundled_mypy_ignores_missing_imports(tmp_path):
    # Rudra's mypy cannot resolve the project's dependencies, so without
    # this flag every run drowns in "Cannot find implementation or library
    # stub for module". Spec S9a.4.
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.argv[:3] == (sys.executable, "-m", "mypy")
    assert "--ignore-missing-imports" in resolution.argv


def test_bundled_mypy_sets_explicit_package_bases(tmp_path):
    """OPEN-29. Without this flag mypy REFUSES TO RUN -- it does not report a
    type error, it declines to start -- on a project whose directory name is
    not a valid Python identifier and which has a root `__init__.py`.

    Measured 2026-08-26 with Rudra's own bundled mypy. Both conditions are
    required, and both are ordinary: `my-app` and `todo-cli` are normal
    project names, and run5's coder wrote a root `__init__.py` unprompted.

        /tmp/goodname   + root __init__.py   Success: no issues found
        /tmp/bad-name   + root __init__.py   bad-name contains __init__.py but is
                                             not a valid Python package name
        /tmp/bad-name2  no root __init__.py  Success: no issues found

    With the flag the same project type-checks AND real errors still surface:

        + --explicit-package-bases  pkg/bad.py:2: error: Incompatible return
                                    value type (got "str", expected "int")

    Verified harmless on an ordinary `src/` layout and a flat one -- identical
    output with and without. This is why OPEN-29 was fixed by a flag rather
    than by making typecheck advisory: the gate keeps blocking.
    """
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert "--explicit-package-bases" in resolution.argv


def test_a_project_venv_mypy_is_left_alone(tmp_path):
    """The flag goes on Rudra's OWN invocation only. A project that installed
    mypy has adopted it, and its config -- which may set this very option --
    must win over Rudra's opinion."""
    make_venv_binary(tmp_path, "mypy")
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert "--explicit-package-bases" not in resolution.argv


def test_rust_uses_the_profile_commands(tmp_path):
    assert resolve_lint_command(tmp_path, RUST).argv == ("cargo", "clippy")
    assert resolve_typecheck_command(tmp_path, RUST).argv == ("cargo", "check")


def test_plain_javascript_has_no_typechecker(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    resolution = resolve_typecheck_command(tmp_path, NODE)
    assert resolution.status == "not_applicable"
    assert "JavaScript" in resolution.detail


def test_typescript_project_without_tsc_is_a_missing_tool(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"typescript": "^5"}})
    resolution = resolve_typecheck_command(tmp_path, REACT)
    assert resolution.status == "missing_tool"
    assert resolution.tool == "tsc"


def test_typescript_project_with_tsc_resolves_to_noemit(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"typescript": "^5"}})
    binary = tmp_path / "node_modules" / ".bin" / "tsc"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    resolution = resolve_typecheck_command(tmp_path, ANGULAR)
    assert resolution.status == "ok"
    assert resolution.argv == (str(binary), "--noEmit")


def test_a_tsconfig_alone_makes_typecheck_applicable(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert resolve_typecheck_command(tmp_path, NODE).status == "missing_tool"


def test_node_without_eslint_makes_lint_not_applicable(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    resolution = resolve_lint_command(tmp_path, NODE)
    assert resolution.status == "not_applicable"


def test_node_declaring_eslint_without_installing_it_is_a_missing_tool(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"eslint": "^9"}})
    resolution = resolve_lint_command(tmp_path, NODE)
    assert resolution.status == "missing_tool"
    assert resolution.tool == "eslint"


def test_bundled_mypy_resolution_is_marked_advisory(tmp_path):
    """OPEN-38. The flag rides on the resolution rather than on the stage,
    because only `resolve_typecheck_command` knows which mypy it picked."""
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.advisory is True


def test_a_project_venv_mypy_resolution_is_authoritative(tmp_path):
    make_venv_binary(tmp_path, "mypy")
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.advisory is False


def test_every_other_resolution_is_authoritative(tmp_path):
    """Nothing else acquires the flag by accident -- rust's cargo check and
    a project's own tsc both keep blocking."""
    assert resolve_typecheck_command(tmp_path, RUST).advisory is False
    assert resolve_lint_command(tmp_path, PYTHON).advisory is False
