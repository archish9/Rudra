"""Detection: read marker files off disk and report the target stacks.

This module observes. It does not infer a stack from prose (that is the
model's job for greenfield work, TODO.md §0.5) and it does not execute
anything (that is C3.6's job, Step 8).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from rudra.stacks.profile import (
    MISSING_TOOL,
    NOT_APPLICABLE,
    OK,
    CommandResolution,
    StackProfile,
)
from rudra.stacks.registry import PROFILES


def _load_package_json(project_path: Path) -> dict:
    """package.json as a dict, or {} when absent, unparseable, or not a JSON object.

    A half-written package.json is a normal intermediate state while an agent
    is working. Degrading to {} keeps detection usable; raising would abort a
    run over a file the agent is about to finish writing.
    """
    path = project_path / "package.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _declares_dependency(package_json: dict, name: str) -> bool:
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = package_json.get(section)
        if isinstance(block, dict) and name in block:
            return True
    return False


def _matches(project_path: Path, profile: StackProfile, package_json: dict) -> bool:
    if not any((project_path / marker).is_file() for marker in profile.markers):
        return False
    if not all((project_path / required).is_file() for required in profile.requires):
        return False
    if profile.dependency is not None:
        return _declares_dependency(package_json, profile.dependency)
    return True


def detect(project_path: Path) -> list[StackProfile]:
    """Target stacks present in `project_path`, most specific first.

    Returns a list, not one profile: a Tauri app genuinely is both Rust and
    Node, and monorepos are ordinary. Returning a single "the" stack would
    force a wrong answer, and D18 forbids Rudra forcing the user's hand.

    An empty list means greenfield -- no marker files yet.
    """
    project_path = Path(project_path)
    if not project_path.is_dir():
        return []

    package_json = _load_package_json(project_path)
    matched = [p for p in PROFILES if _matches(project_path, p, package_json)]
    return sorted(matched, key=lambda p: (-p.specificity, p.name))


_VENV_DIRS = (".venv", "venv")
# Windows puts executables in Scripts/, POSIX in bin/. Checking both costs
# two stat calls and avoids a platform branch.
_VENV_BIN_DIRS = ("bin", "Scripts")
_PYTEST_DECLARATIONS = ("pyproject.toml", "requirements.txt", "setup.py")


def _venv_executable(project_path: Path, name: str) -> Path | None:
    for venv in _VENV_DIRS:
        for binaries in _VENV_BIN_DIRS:
            for suffix in ("", ".exe"):
                candidate = project_path / venv / binaries / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate
    return None


def _declares_pytest(project_path: Path) -> bool:
    """Does the project name pytest anywhere obvious?

    Text matching, not parsing: pytest can be declared in [project]
    dependencies, a dependency-group, [tool.poetry], or requirements.txt,
    and parsing four formats to answer one yes/no question is not worth it.
    """
    for filename in _PYTEST_DECLARATIONS:
        try:
            if "pytest" in (project_path / filename).read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _system_interpreter() -> str:
    """A python that actually exists on PATH.

    `python` is not a safe name to emit. macOS has shipped no bare `python`
    since it dropped system Python 2, and on Linux it is a distribution
    choice rather than a guarantee -- measured under a clean PATH,
    `command -v python` finds nothing while `python3` resolves (A1.56).
    Emitting it anyway would repeat A1.33(b) one level down: naming an
    executable and assuming PATH resolves it.

    Deliberately NOT `sys.executable`. D18 forbids conflating Rudra's own
    interpreter with the target project's, and the venv branches above
    already handle a project that brought its own. Falls back to "python3"
    so the argv is still reportable when neither is found -- `run_tests`
    then surfaces a launch_error, which is the honest answer.
    """
    for name in ("python3", "python"):
        if shutil.which(name):
            return name
    return "python3"


def _python_test_command(project_path: Path) -> list[str]:
    """The launchable argv for a Python project's tests.

    Ordered by how specific the evidence is. A project virtualenv is the
    strongest signal available, because it is the interpreter the project's
    own tooling uses -- and it is exactly what a bare `pytest` misses
    (TODO.md A1.33(b)).

    Never falls back to Rudra's own interpreter or venv: D18 is explicit
    that Rudra being a Python project and the target being one must not be
    conflated. Every path here is read from `project_path`.
    """
    pytest_bin = _venv_executable(project_path, "pytest")
    if pytest_bin is not None:
        return [str(pytest_bin)]

    # Interpreter and runner are two separate questions, and conflating them
    # was the bug: the venv branch used to `return [python, "-m", "pytest"]`
    # outright, above the manage.py and _declares_pytest checks -- so a
    # Django project with a virtualenv but no pytest got
    # `.venv/bin/python -m pytest` and `No module named pytest` on every
    # run, and `manage.py test` was never reached. Same for a stdlib
    # unittest project with a venv. The test stage blocks, so the fix loop
    # saw a permanent non-test failure it could not repair (CR-D6).
    python_bin = _venv_executable(project_path, "python")
    interpreter = str(python_bin) if python_bin is not None else _system_interpreter()

    if (project_path / "manage.py").is_file():
        return [interpreter, "manage.py", "test"]
    if _declares_pytest(project_path):
        return [interpreter, "-m", "pytest"]
    # A venv with no pytest installed and no declaration either: pytest is
    # still the likelier intent for a project that built a venv at all, and
    # `unittest discover` on a pytest layout finds nothing.
    if python_bin is not None:
        return [interpreter, "-m", "pytest"]
    return [interpreter, "-m", "unittest", "discover"]


def resolve_test_command(project_path: Path, profile: StackProfile) -> list[str] | None:
    """The argv for this stack's tests, or None if the project declares none.

    None is a real answer, not a failure: the fix loop can act on "this
    project has no test command". Inventing one would give it a gate that
    does not correspond to anything.

    Python declares no fixed command and is worked out from the project's
    own layout instead -- see `_python_test_command` and TODO.md A1.33(b).
    Node reads `scripts.test`. Rust's is fixed.
    """
    if profile.test_command is not None:
        return list(profile.test_command)

    if profile.name == "python":
        return _python_test_command(Path(project_path))

    scripts = _load_package_json(Path(project_path)).get("scripts")
    if (
        isinstance(scripts, dict)
        and isinstance(scripts.get("test"), str)
        and scripts["test"].strip()
    ):
        return ["npm", "test"]
    return None


# Stacks whose lint and typecheck answers come from package.json rather
# than a fixed command.
_NODE_FAMILY = frozenset({"node", "react", "angular"})


def _bundled(tool: str, *arguments: str) -> tuple[str, ...]:
    """Rudra's own copy of a tool, invoked through its own interpreter.

    `sys.executable`, not `shutil.which`: PATH is unreliable under
    `uv tool install`, and permissions/env.py:4 records that scrubbed_env
    produces an environment in which `which ruff` finds nothing.

    This is the one place sys.executable is correct, and it is worth saying
    so out loud because `_system_interpreter` above forbids it. That
    prohibition is about the *target project's* interpreter -- D18 is
    explicit that the user's tests must never run under Rudra's Python.
    Here the call deliberately invokes Rudra's own bundled tool, which is
    exactly what sys.executable names.
    """
    return (sys.executable, "-m", tool, *arguments)


def _node_binary(project_path: Path, name: str) -> Path | None:
    candidate = Path(project_path) / "node_modules" / ".bin" / name
    return candidate if candidate.is_file() else None


def resolve_lint_command(project_path: Path, profile: StackProfile) -> CommandResolution:
    """The argv for this stack's linter.

    Lint is advisory (spec S9a.2), so nothing here can fail a task -- but
    it still distinguishes "this project has no linter" from "it declares
    one that is not installed", because the report shows the difference and
    a user acting on it needs to know which.
    """
    project_path = Path(project_path)

    if profile.lint_command is not None:
        return CommandResolution(tuple(profile.lint_command), OK, tool=profile.lint_command[0])

    if profile.name == "python":
        ruff = _venv_executable(project_path, "ruff")
        if ruff is not None:
            return CommandResolution((str(ruff), "check", "."), OK, tool="ruff")
        return CommandResolution(_bundled("ruff", "check", "."), OK, tool="ruff")

    if profile.name not in _NODE_FAMILY:
        return CommandResolution(
            None, NOT_APPLICABLE, detail=f"no linter is defined for the {profile.name} stack"
        )

    package_json = _load_package_json(project_path)
    if not _declares_dependency(package_json, "eslint"):
        return CommandResolution(
            None, NOT_APPLICABLE, detail="this project declares no eslint", tool="eslint"
        )
    eslint = _node_binary(project_path, "eslint")
    if eslint is None:
        return CommandResolution(
            None,
            MISSING_TOOL,
            detail="package.json declares eslint but node_modules/.bin/eslint is absent",
            tool="eslint",
        )
    return CommandResolution((str(eslint), "."), OK, tool="eslint")


def resolve_typecheck_command(project_path: Path, profile: StackProfile) -> CommandResolution:
    """The argv for this stack's type checker.

    Typecheck blocks, so the not_applicable / missing_tool split carries
    real weight here: plain JavaScript has no type checker and must pass,
    while a TypeScript project without tsc must stop and tell the user.
    """
    project_path = Path(project_path)

    if profile.typecheck_command is not None:
        return CommandResolution(
            tuple(profile.typecheck_command), OK, tool=profile.typecheck_command[0]
        )

    if profile.name == "python":
        mypy = _venv_executable(project_path, "mypy")
        if mypy is not None:
            return CommandResolution((str(mypy), "."), OK, tool="mypy")
        # --ignore-missing-imports because Rudra's mypy cannot see the
        # project's dependencies. It still catches type errors in the
        # project's own code, which is where generated code goes wrong.
        return CommandResolution(
            _bundled("mypy", "--ignore-missing-imports", "."),
            OK,
            detail="Rudra's bundled mypy; project dependencies are not resolved",
            tool="mypy",
        )

    if profile.name not in _NODE_FAMILY:
        return CommandResolution(
            None, NOT_APPLICABLE, detail=f"no type checker is defined for the {profile.name} stack"
        )

    package_json = _load_package_json(project_path)
    typescript = (
        _declares_dependency(package_json, "typescript")
        or (project_path / "tsconfig.json").is_file()
    )
    if not typescript:
        return CommandResolution(
            None,
            NOT_APPLICABLE,
            detail="plain JavaScript project -- no type checker applies",
            tool="tsc",
        )
    tsc = _node_binary(project_path, "tsc")
    if tsc is None:
        return CommandResolution(
            None,
            MISSING_TOOL,
            detail="this project is TypeScript but node_modules/.bin/tsc is absent",
            tool="tsc",
        )
    return CommandResolution((str(tsc), "--noEmit"), OK, tool="tsc")
