"""Tests for rudra.stacks — deterministic target-stack detection (C11.1).

Rudra's own toolchain is always Python. This module is about the *user's*
project, which may be Python, Rust, Node, React or Angular (TODO.md D18).

Detection reads marker files off disk. It never infers a stack from prose --
that is the model's job for greenfield work, per TODO.md §0.5 -- and it never
executes a subprocess; running the commands it reports belongs to C3.6.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from rudra.compat.own_interpreter import (
    is_own_interpreter,
    path_without_own,
    running_in_own_virtualenv,
    search_path_without_own,
)
from rudra.stacks import ALL_SKIP_DIRS, PROFILES, detect, resolve_test_command
from rudra.stacks.detect import _python_test_command, _system_interpreter


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


def test_python_runs_the_project_venv_pytest_through_its_interpreter(tmp_path: Path):
    """OPEN-140: `python -m pytest`, never the `pytest` script, when both exist.

    The script puts its own `bin/` on sys.path; `python -m` puts the project
    root there, which is what a flat layout's `from models import X` needs.
    """
    _python_project(tmp_path)
    _venv_with(tmp_path, "pytest", "python")
    assert resolve_test_command(tmp_path, _python()) == [
        str(tmp_path / ".venv" / "bin" / "python"),
        "-m",
        "pytest",
    ]


def test_a_venv_pytest_with_no_interpreter_beside_it_is_run_directly(tmp_path: Path):
    """The script is still the answer when it is all the venv holds."""
    _python_project(tmp_path)
    _venv_with(tmp_path, "pytest")
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


def test_a_root_pytest_ini_declares_pytest(tmp_path: Path):
    """OPEN-47. `pytest.ini` exists for no other tool, and run8 had one."""
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths =\n    tests\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_an_empty_pytest_ini_still_declares_pytest(tmp_path: Path):
    """Its presence is the declaration; pytest treats it as the rootdir marker."""
    (tmp_path / "pytest.ini").write_text("", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_setup_cfg_declares_pytest(tmp_path: Path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = -q\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_tox_ini_declares_pytest(tmp_path: Path):
    (tmp_path / "tox.ini").write_text("[testenv]\ndeps = pytest\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_bare_function_tests_are_a_pytest_layout_however_the_package_looks(tmp_path: Path):
    """OPEN-47, and this is run8's exact shape.

    `tests/__init__.py` makes the package *discoverable*, so
    `_has_undiscoverable_tests` said no -- but every test in it is a bare
    `def test_*`, which `unittest discover` enters and collects nothing
    from. The gate ran 10 of the project's 23 tests and reported `passed`.
    Structural reachability and collectability are different questions.
    """
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_db.py").write_text("def test_create():\n    assert True\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_a_root_level_bare_function_test_is_a_pytest_layout(tmp_path: Path):
    """No `tests/` directory at all -- the walk must look at the root too."""
    (tmp_path / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_a_test_file_importing_pytest_is_a_pytest_layout(tmp_path: Path):
    """Importing pytest means unittest cannot run the file either."""
    (tmp_path / "test_app.py").write_text(
        "import pytest\nimport unittest\n\n\n"
        "class T(unittest.TestCase):\n"
        "    def test_raises(self):\n"
        "        with pytest.raises(ValueError):\n"
        "            raise ValueError\n",
        encoding="utf-8",
    )
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "pytest"]


def test_a_pure_unittest_project_still_gets_unittest(tmp_path: Path):
    """The check must not swallow the branch it sits in front of.

    Every test here is a TestCase method, which `unittest discover` collects
    perfectly well, and nothing on disk names pytest.
    """
    (tmp_path / "test_app.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
        encoding="utf-8",
    )
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "unittest", "discover"]


def test_an_indented_def_test_is_not_a_module_level_function(tmp_path: Path):
    """A TestCase method is indented; reading it as a bare function would
    route every unittest project to pytest."""
    (tmp_path / "test_app.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n"
        "    def test_inside(self):\n        pass\n",
        encoding="utf-8",
    )
    command = resolve_test_command(tmp_path, _python())
    assert command[1:] == ["-m", "unittest", "discover"]


def test_venv_resolution_never_reads_rudras_own_venv(tmp_path: Path):
    """D18: Rudra being a Python project and the target being one must not be conflated.

    **The substring check alone was blind, which is OPEN-80.** `_system_interpreter`
    returned the bare name `python3`, which contains no "Rudra" and passed this
    assertion while resolving -- through `PATH`, because Rudra runs from its own
    virtualenv -- to exactly the interpreter D18 forbids. Run `689f0ea263be` ran a
    user's suite under `<rudra>/.venv/bin/python3` with this test green.

    So the argv is now resolved the way a shell would resolve it before it is
    judged. A bare name that finds Rudra's interpreter fails here.
    """
    command = resolve_test_command(_python_project(tmp_path), _python())
    assert "Rudra" not in " ".join(command)
    assert not _resolves_into_rudras_venv(command[0]), command


def _resolves_into_rudras_venv(argv0: str) -> bool:
    """Would a shell running `argv0` reach Rudra's own interpreter?

    Both spellings of Rudra's bin directory are compared -- as `sys.executable`
    gives it and as it resolves -- for the reason `virtual_paths` compares two
    spellings of the project root: on macOS the two differ whenever the path
    sits behind a symlink, and a miss reads as "not Rudra" (A1.58).

    The candidate's own parent is compared, never where the candidate RESOLVES
    to: a venv's `python3` is usually a symlink to the base interpreter it was
    built from, so following it lands in `/usr/bin` and reports Rudra's own
    binary as somebody else's.
    """
    found = shutil.which(argv0)
    if found is None:
        return False
    own = {Path(sys.executable).parent.as_posix()}
    with contextlib.suppress(OSError, RuntimeError):
        own.add(Path(sys.executable).parent.resolve().as_posix())
    parent = Path(found).parent
    spellings = {parent.as_posix()}
    with contextlib.suppress(OSError, RuntimeError):
        spellings.add(parent.resolve().as_posix())
    return bool(spellings & own)


def test_system_interpreter_is_an_absolute_path(tmp_path: Path):
    """OPEN-80: `verify.log` recorded `command: python3 -m pytest`, and that
    string is the same on a machine that ran the right interpreter and one that
    ran Rudra's. The log has to say which."""
    command = resolve_test_command(_python_project(tmp_path), _python())
    assert Path(command[0]).is_absolute(), command


def test_system_interpreter_skips_rudras_bin_and_keeps_searching(tmp_path: Path, monkeypatch):
    """Rejecting is not enough -- `PATH` must be searched PAST Rudra's entry.

    Rudra runs from its own virtualenv, so its bin directory is FIRST on `PATH`
    and `shutil.which` returns it and stops. A check that only rejects would
    leave the fallback with nothing to find on the very machine the defect
    lives on.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = elsewhere / "python3"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)

    rudra_bin = Path(sys.executable).parent
    monkeypatch.setenv("PATH", os.pathsep.join([str(rudra_bin), str(elsewhere)]))

    assert _system_interpreter() == str(real)


def test_the_fallback_cannot_reach_rudra_through_the_env_it_will_run_in(monkeypatch):
    """PATH holds Rudra's bin and nothing else: what does the fallback mean?

    It stays the bare name `python3` -- A1.56 wants the argv reportable so
    `run_tests` can surface a launch_error rather than a blank. The bare name
    is only safe because the OTHER half of OPEN-80 landed with this one: the
    environment the command actually runs in is `scrubbed_env`, whose PATH no
    longer contains Rudra either. So this asserts the property that matters --
    resolved through the *execution* environment, the fallback reaches nothing.

    Resolving it through the parent's own PATH, as an earlier version of this
    test did, asks a question no command is ever asked.
    """
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent))
    result = _system_interpreter()
    assert result == "python3", "A1.56: the argv stays reportable when nothing is found"
    # The literal stripped string, NOT `... or None`: `shutil.which(path=None)`
    # falls back to the ambient PATH, which is the one thing the child will not
    # have. An earlier version of this line asked that question and reported
    # Rudra's interpreter -- correctly, for a question no command is ever asked.
    assert shutil.which(result, path=path_without_own(os.environ["PATH"])) is None


def test_the_two_halves_agree_about_which_python_is_rudras():
    """One definition, two consumers -- the `is_build_output` lesson (OPEN-64).

    `stacks/detect.py` picks the interpreter and `permissions/env.py` builds the
    environment it runs in. If those disagreed, a resolution that carefully
    avoided Rudra could be executed with a PATH that finds it again, or a
    correct interpreter could be made unreachable. Both call
    `compat/own_interpreter.py`, and this is what says so.
    """
    resolved = _system_interpreter()
    if resolved != "python3":
        assert not is_own_interpreter(resolved), resolved
    assert path_without_own(os.environ["PATH"]) != os.environ["PATH"] or not (
        running_in_own_virtualenv()
    )


@pytest.mark.skipif(
    not running_in_own_virtualenv(),
    reason="nothing to strip: Rudra is not running from a virtualenv of its own",
)
def test_the_search_path_actually_loses_rudras_bin():
    """The guard above is not decoration -- see `own_interpreter`'s docstring on
    what an unguarded version does to a distribution-packaged install."""
    assert str(Path(sys.executable).parent) not in (search_path_without_own() or "")


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


def test_a_django_project_with_a_venv_gets_manage_py_not_pytest(tmp_path: Path) -> None:
    """CR-D6: the venv branch returned `[python, "-m", "pytest"]` outright,
    above the manage.py and _declares_pytest checks -- so a Django project
    with a virtualenv but no pytest installed got `No module named pytest`
    on every run and `manage.py test` was never reached. The test stage
    blocks, so the fix loop saw a permanent non-test failure it could not
    repair. Interpreter and runner are separate questions.
    """
    (tmp_path / "manage.py").write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("Django>=5\n", encoding="utf-8")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    command = _python_test_command(tmp_path)

    # The venv interpreter is still preferred -- that part was right.
    assert command == [str(python), "manage.py", "test"]
