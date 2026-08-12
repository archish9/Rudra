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
