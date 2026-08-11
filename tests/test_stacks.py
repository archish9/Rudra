"""Tests for rudra.stacks — deterministic target-stack detection (C11.1).

Rudra's own toolchain is always Python. This module is about the *user's*
project, which may be Python, Rust, Node, React or Angular (TODO.md D18).

Detection reads marker files off disk. It never infers a stack from prose --
that is the model's job for greenfield work, per TODO.md §0.5 -- and it never
executes a subprocess; running the commands it reports belongs to C3.6.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from rudra.stacks import ALL_SKIP_DIRS, PROFILES, detect, resolve_test_command


def test_profile_is_immutable():
    """Profiles are shared module-level constants; mutation would leak globally."""
    profile = PROFILES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "mutated"


def test_every_target_stack_has_a_profile():
    assert {p.name for p in PROFILES} == {"python", "rust", "node", "react", "angular"}


def test_profile_names_are_unique():
    names = [p.name for p in PROFILES]
    assert len(names) == len(set(names))


def test_node_stacks_defer_their_test_command_to_the_project():
    """package.json's scripts.test is the project's own declared answer.

    Guessing between jest and vitest from devDependencies would be inference
    where an observation is available.
    """
    for name in ("node", "react", "angular"):
        profile = next(p for p in PROFILES if p.name == name)
        assert profile.test_command is None, f"{name} must read scripts.test, not hardcode"


def test_only_rust_declares_a_fixed_test_command():
    """Python joined Node in deferring, for a different reason.

    Node defers because the project declares its own in scripts.test.
    Python defers because no single argv is launchable everywhere: a bare
    "pytest" is not on PATH in a virtualenv Rudra did not activate, and is
    wrong outright for Django or stdlib unittest (TODO.md A1.33(b)).
    Rust's `cargo test` genuinely is fixed.
    """
    commands = {p.name: p.test_command for p in PROFILES if p.test_command is not None}
    assert commands == {"rust": ("cargo", "test")}


def test_more_specific_stacks_outrank_generic_node():
    node = next(p for p in PROFILES if p.name == "node")
    for name in ("react", "angular"):
        specific = next(p for p in PROFILES if p.name == name)
        assert specific.specificity > node.specificity


def test_all_skip_dirs_covers_every_targets_build_output():
    for expected in (
        "target",
        ".next",
        "out",
        "dist",
        "build",
        ".angular",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    ):
        assert expected in ALL_SKIP_DIRS


def test_all_skip_dirs_is_the_union_of_the_profiles():
    union = frozenset().union(*(p.skip_dirs for p in PROFILES))
    assert ALL_SKIP_DIRS == union


def _write(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _package_json(root: Path, *, deps: dict | None = None, test: str | None = None) -> None:
    body: dict = {"name": "app"}
    if deps is not None:
        body["dependencies"] = deps
    if test is not None:
        body["scripts"] = {"test": test}
    _write(root, "package.json", json.dumps(body))


def test_empty_directory_detects_nothing(tmp_path: Path):
    """Greenfield: the model picks the stack and writes the marker file."""
    assert detect(tmp_path) == []


def test_cargo_toml_detects_rust(tmp_path: Path):
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    assert [p.name for p in detect(tmp_path)] == ["rust"]


def test_pyproject_detects_python(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_requirements_txt_alone_detects_python(tmp_path: Path):
    """markers are ANY-of: one Python marker is enough."""
    _write(tmp_path, "requirements.txt", "flask\n")
    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_package_json_alone_detects_node(tmp_path: Path):
    _package_json(tmp_path, test="jest")
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_angular_outranks_node_and_both_are_reported(tmp_path: Path):
    _package_json(tmp_path, test="ng test")
    _write(tmp_path, "angular.json", "{}")
    assert [p.name for p in detect(tmp_path)] == ["angular", "node"]


def test_react_needs_the_dependency_not_just_package_json(tmp_path: Path):
    _package_json(tmp_path, deps={"vue": "^3.0.0"}, test="vitest run")
    assert [p.name for p in detect(tmp_path)] == ["node"]

    _package_json(tmp_path, deps={"react": "^18.0.0"}, test="vitest run")
    assert [p.name for p in detect(tmp_path)] == ["react", "node"]


def test_tauri_style_project_reports_both_stacks(tmp_path: Path):
    """A repo genuinely can be two stacks. Forcing one answer would be wrong."""
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    _package_json(tmp_path, test="vitest run")
    assert sorted(p.name for p in detect(tmp_path)) == ["node", "rust"]


def test_angular_json_without_package_json_is_not_angular(tmp_path: Path):
    """`requires` is ALL-of: angular.json alone is not an Angular workspace."""
    _write(tmp_path, "angular.json", "{}")
    assert detect(tmp_path) == []


def test_malformed_package_json_does_not_crash_detection(tmp_path: Path):
    """A half-written package.json is normal mid-task. Degrade, don't raise."""
    _write(tmp_path, "package.json", "{ not valid json")
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_list_shaped_package_json_does_not_crash_detection(tmp_path: Path):
    """Valid JSON that isn't an object (e.g. a bare array) must degrade too."""
    _write(tmp_path, "package.json", '["react", "vue"]')
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_scalar_shaped_package_json_does_not_crash_detection(tmp_path: Path):
    """Valid JSON that isn't an object (e.g. a bare string) must degrade too."""
    _write(tmp_path, "package.json", '"just a string"')
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_missing_directory_detects_nothing(tmp_path: Path):
    assert detect(tmp_path / "does-not-exist") == []


def test_resolve_test_command_returns_the_declared_command(tmp_path: Path):
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    rust = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, rust) == ["cargo", "test"]


def test_resolve_test_command_reads_scripts_test_for_node(tmp_path: Path):
    _package_json(tmp_path, test="vitest run")
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) == ["npm", "test"]


def test_resolve_test_command_is_none_when_node_declares_no_test(tmp_path: Path):
    """Reported as absent, never fabricated. A missing gate is actionable;
    an invented one is not."""
    _package_json(tmp_path)
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None


def test_resolve_test_command_is_none_when_package_json_is_malformed(tmp_path: Path):
    _write(tmp_path, "package.json", "{ not valid json")
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None


def test_resolve_test_command_is_none_when_package_json_is_list_shaped(tmp_path: Path):
    """Valid JSON that isn't an object must degrade, not raise."""
    _write(tmp_path, "package.json", '["react", "vue"]')
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None


def test_resolve_test_command_is_none_when_package_json_is_scalar_shaped(tmp_path: Path):
    """Valid JSON that isn't an object must degrade, not raise."""
    _write(tmp_path, "package.json", '"just a string"')
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None


# --- A1.33(b): a Python test command that can actually launch ---


def _python_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return tmp_path


def _venv_with(tmp_path: Path, *executables: str, directory: str = ".venv") -> None:
    binaries = tmp_path / directory / "bin"
    binaries.mkdir(parents=True)
    for name in executables:
        path = binaries / name
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)


def _python() -> object:
    return next(p for p in PROFILES if p.name == "python")


def test_python_test_command_is_never_a_bare_executable_name(tmp_path: Path):
    """A1.33(b): 'pytest' alone is not on PATH in a venv Rudra did not activate."""
    command = resolve_test_command(_python_project(tmp_path), _python())
    assert command is not None
    assert command[0] != "pytest"


def test_python_prefers_the_project_venv_pytest(tmp_path: Path):
    _python_project(tmp_path)
    _venv_with(tmp_path, "pytest", "python")
    assert resolve_test_command(tmp_path, _python()) == [str(tmp_path / ".venv" / "bin" / "pytest")]


def test_python_accepts_a_venv_named_venv(tmp_path: Path):
    _python_project(tmp_path)
    _venv_with(tmp_path, "pytest", directory="venv")
    assert resolve_test_command(tmp_path, _python()) == [str(tmp_path / "venv" / "bin" / "pytest")]


def test_python_uses_the_venv_interpreter_when_pytest_is_not_installed(tmp_path: Path):
    _python_project(tmp_path)
    _venv_with(tmp_path, "python")
    assert resolve_test_command(tmp_path, _python()) == [
        str(tmp_path / ".venv" / "bin" / "python"),
        "-m",
        "pytest",
    ]


def test_python_recognises_a_django_project(tmp_path: Path):
    _python_project(tmp_path)
    (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["manage.py", "test"]
    assert shutil.which(command[0]), "the interpreter must exist on PATH (A1.56)"


def test_python_uses_pytest_when_the_project_declares_it(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\ndependencies=['pytest']\n", encoding="utf-8"
    )
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]
    assert shutil.which(command[0]), "the interpreter must exist on PATH (A1.56)"


def test_python_reads_pytest_from_requirements_txt(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("pytest>=8\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]
    assert shutil.which(command[0]), "the interpreter must exist on PATH (A1.56)"


def test_python_falls_back_to_unittest_with_no_venv_and_no_pytest(tmp_path: Path):
    command = resolve_test_command(_python_project(tmp_path), _python())
    assert command[1:] == ["-m", "unittest", "discover"]
    assert shutil.which(command[0]), "the interpreter must exist on PATH (A1.56)"


def test_venv_resolution_never_reads_rudras_own_venv(tmp_path: Path):
    """D18: Rudra being a Python project and the target being one must not be conflated."""
    command = resolve_test_command(_python_project(tmp_path), _python())
    assert "Rudra" not in " ".join(command)


def test_resolution_still_executes_nothing(tmp_path: Path, monkeypatch):
    """stacks/ observes; running the command it reports belongs to C3.6."""
    import subprocess

    def _boom(*args, **kwargs):
        raise AssertionError("stacks must not start a subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    resolve_test_command(_python_project(tmp_path), _python())


def test_no_fallback_names_a_bare_python(tmp_path: Path):
    """A1.56: macOS ships no bare `python`, so emitting it repeats A1.33(b)."""
    for setup in (
        lambda: None,
        lambda: (tmp_path / "manage.py").write_text("# django\n", encoding="utf-8"),
    ):
        _python_project(tmp_path)
        setup()
        command = resolve_test_command(tmp_path, _python())
        assert command[0] != "python", command
        assert shutil.which(command[0]) is not None, command
