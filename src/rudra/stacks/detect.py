"""Detection: read marker files off disk and report the target stacks.

This module observes. It does not infer a stack from prose (that is the
model's job for greenfield work, TODO.md §0.5) and it does not execute
anything (that is C3.6's job, Step 8).
"""

from __future__ import annotations

import json
from pathlib import Path

from rudra.stacks.profile import StackProfile
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

    python_bin = _venv_executable(project_path, "python")
    if python_bin is not None:
        return [str(python_bin), "-m", "pytest"]

    if (project_path / "manage.py").is_file():
        return ["python", "manage.py", "test"]
    if _declares_pytest(project_path):
        return ["python", "-m", "pytest"]
    return ["python", "-m", "unittest", "discover"]


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
