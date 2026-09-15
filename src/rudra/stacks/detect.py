"""Detection: read marker files off disk and report the target stacks.

This module observes. It does not infer a stack from prose (that is the
model's job for greenfield work, TODO.md §0.5) and it does not execute
anything (that is C3.6's job, Step 8).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from rudra.compat.own_interpreter import is_own_interpreter, search_path_without_own
from rudra.stacks.profile import (
    MISSING_TOOL,
    NOT_APPLICABLE,
    OK,
    CommandResolution,
    StackProfile,
)
from rudra.stacks.registry import ALL_SKIP_DIRS, PROFILES


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


# How far down to look for source files when no marker matched. Deep enough
# for `pkg/sub/mod.py` and a `tests/` beside it, shallow enough that this
# cannot walk a monorepo -- it runs on every gate call.
_INFERENCE_DEPTH = 4


def _has_source_file(project_path: Path, suffix: str, skip: frozenset[str]) -> bool:
    """Is there a `suffix` file here that belongs to THIS project?

    Bounded and short-circuiting. `skip` keeps `.venv` out, which matters
    more than it looks: a virtualenv holds thousands of .py files belonging
    to somebody else, and counting them would call every directory holding
    one a Python project -- including a Rust project that built a venv for
    its tooling.
    """

    def walk(directory: Path, depth: int) -> bool:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return False
        for entry in entries:
            if entry.is_file() and entry.suffix == suffix:
                return True
        if depth <= 0:
            return False
        for entry in entries:
            if entry.is_dir() and entry.name not in skip and not entry.name.startswith("."):
                if walk(entry, depth - 1):
                    return True
        return False

    return walk(project_path, _INFERENCE_DEPTH)


def _inferred(project_path: Path) -> list[StackProfile]:
    """The stack a project's SOURCE FILES imply, when no marker named one.

    Only Python, and only as a last resort (OPEN-28).

    **Python is the one marker-optional stack**, which is what makes this a
    correction rather than a guess. A Rust project cannot exist without
    `Cargo.toml`, nor a Node one without `package.json`; those markers are
    structural, so their absence really does mean "not that stack". Python's
    markers are conventional -- `pyproject.toml`, `setup.py`,
    `requirements.txt` are all optional -- so their absence means nothing,
    and a directory of `.py` files with a `tests/` beside it is a Python
    project whatever its packaging says.

    Measured 2026-08-26 (run4): without this, a finished 13-task run had
    lint, typecheck and test ALL report `not_applicable`. Since
    `not_applicable` is deliberately non-halting (A1.57) the gate passed, so
    nine tasks were marked DONE on a syntax parse, the fix loop never had
    input, and the three test files the run wrote were never executed.

    This fires only where `detect` would otherwise return [], so no project
    that already resolves a stack can be changed by it.
    """
    from rudra.stacks.registry import PYTHON

    if _has_source_file(project_path, ".py", PYTHON.skip_dirs):
        return [PYTHON]
    return []


def detect(project_path: Path) -> list[StackProfile]:
    """Target stacks present in `project_path`, most specific first.

    Returns a list, not one profile: a Tauri app genuinely is both Rust and
    Node, and monorepos are ordinary. Returning a single "the" stack would
    force a wrong answer, and D18 forbids Rudra forcing the user's hand.

    An empty list means greenfield -- nothing here to verify yet. A project
    with source files but no marker is NOT that case, and reporting it as one
    is OPEN-28: see `_inferred`.
    """
    project_path = Path(project_path)
    if not project_path.is_dir():
        return []

    package_json = _load_package_json(project_path)
    matched = [p for p in PROFILES if _matches(project_path, p, package_json)]
    if not matched:
        # Last resort only. A real marker always decides, so a Rust project
        # with one helper script stays Rust (OPEN-28).
        return _inferred(project_path)
    return sorted(matched, key=lambda p: (-p.specificity, p.name))


_VENV_DIRS = (".venv", "venv")
# Windows puts executables in Scripts/, POSIX in bin/. Checking both costs
# two stat calls and avoids a platform branch.
_VENV_BIN_DIRS = ("bin", "Scripts")
_PYTEST_DECLARATIONS = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "tox.ini",
)

# Files that exist for no other tool. pytest reads `pytest.ini` as its
# rootdir marker, so an EMPTY one still declares pytest and the text match
# below would miss it (OPEN-47). run8's project had one, holding
# `[pytest]`, `norecursedirs` and `testpaths`, and the gate ran unittest
# anyway.
_PYTEST_ONLY_FILES = ("pytest.ini",)

# A test written for pytest rather than for unittest, by shape:
#
#   def test_x():        <- module level, no indent. `unittest discover`
#                           enters the file and collects nothing from it.
#   import pytest        <- the file cannot run under unittest either way.
#
# The anchor matters: a TestCase's `def test_x(self)` is INDENTED, and a
# pattern that matched it would route every unittest project to pytest.
_MODULE_LEVEL_TEST = re.compile(r"^def test", re.MULTILINE)
_IMPORTS_PYTEST = re.compile(r"^\s*(?:import pytest\b|from pytest\b)", re.MULTILINE)


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
    and parsing five formats to answer one yes/no question is not worth it.

    `pytest.ini` is asked a different question -- does it EXIST -- because
    it exists for nothing else, and an empty one is still a declaration
    (OPEN-47).
    """
    for filename in _PYTEST_ONLY_FILES:
        if (project_path / filename).is_file():
            return True
    for filename in _PYTEST_DECLARATIONS:
        try:
            if "pytest" in (project_path / filename).read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _system_interpreter() -> str:
    """A python that actually exists on PATH, and that is not Rudra's own.

    `python` is not a safe name to emit. macOS has shipped no bare `python`
    since it dropped system Python 2, and on Linux it is a distribution
    choice rather than a guarantee -- measured under a clean PATH,
    `command -v python` finds nothing while `python3` resolves (A1.56).
    Emitting it anyway would repeat A1.33(b) one level down: naming an
    executable and assuming PATH resolves it.

    **PATH is searched PAST Rudra's own bin directory, not merely checked
    against it (OPEN-80).** This docstring used to say "Deliberately NOT
    `sys.executable`. D18 forbids conflating Rudra's own interpreter with
    the target project's, and the venv branches above already handle a
    project that brought its own" -- and then called `shutil.which("python3")`.
    Rudra runs from its own virtualenv, so that directory is FIRST on PATH:
    the answer was `sys.executable` by another route, and the second half of
    that sentence is the assumption that fails, because a project which
    brought NOTHING falls through to here.

    Rejecting the match is not enough for the same reason. `shutil.which`
    returns the first hit and stops, so on the very machine the defect lives
    on a rejecting check would leave this with nothing to find. The search
    is re-run over a PATH that does not contain Rudra
    (`compat/own_interpreter.py`, one definition shared with
    `permissions/env.py`).

    Returns an ABSOLUTE path when it finds one. The bare name is what hid
    this: `verify.log` recorded `command: python3 -m pytest`, which is the
    same string on a machine that ran the right interpreter and one that ran
    Rudra's, so no log of run `689f0ea263be` could have been read as wrong.

    Falls back to "python3" so the argv is still reportable when nothing is
    found -- `run_tests` then surfaces a launch_error, which is the honest
    answer and which blocks and escalates (verify/pipeline.py:634).
    """
    search = search_path_without_own()
    for name in ("python3", "python"):
        found = shutil.which(name, path=search) if search else None
        if found is not None and not is_own_interpreter(found):
            return found
    return "python3"


# Public spellings for testing/project_env.py (OPEN-120), which BUILDS the
# venv these find. Aliases rather than renames: tests/test_stacks.py imports
# `_system_interpreter` by name, and inside this module the private names
# still read as the gate's own resolution.
VENV_DIRS = _VENV_DIRS
venv_executable = _venv_executable
system_interpreter = _system_interpreter


_TEST_DIRS = ("tests", "test")


def _is_discoverable(root: Path, module: Path) -> bool:
    """Can `unittest discover` walk from `root` down to `module`?

    Discovery recurses only into importable directories, so EVERY directory
    on the path -- `root` itself included -- needs an `__init__.py`. One gap
    anywhere in the chain and the module below it is unreachable.
    """
    current = root
    if not (current / "__init__.py").is_file():
        return False
    for part in module.relative_to(root).parts[:-1]:
        current = current / part
        if not (current / "__init__.py").is_file():
            return False
    return True


def _has_undiscoverable_tests(project_path: Path) -> bool:
    """Are there test files `unittest discover` structurally cannot reach?

    Measured 2026-08-26 against run4's finished project, whose `tests/` holds
    three test modules and no `__init__.py`:

        python3 -m unittest discover           Ran 0 tests.  NO TESTS RAN
        python3 -m unittest discover -s tests  ImportError: not importable
        python3 -m pytest tests -q             7 failed, 22 passed

    Shape, not execution: this module observes and does not run anything
    (see the module docstring), so the question asked is "can unittest even
    enter this directory", which `__init__.py` answers on disk.

    **The walk is recursive and the check is per-file (OPEN-34).** OPEN-28
    asked this of `tests/`'s DIRECT children and skipped the directory
    outright when `tests/__init__.py` existed, and both halves were wrong one
    level down. run6's project keeps its modules in `tests/unit/` and
    `tests/integration/`, so `tests/` itself held only directories, the
    `any()` was False, and the gate ran `unittest discover` against a pytest
    layout for a whole run -- collecting nothing, reporting `not_applicable`,
    and therefore passing every task. A package `tests/` whose SUBdirectories
    are not packages is undiscoverable for the same reason, which is why
    `tests/__init__.py` no longer ends the question.
    """
    for name in _TEST_DIRS:
        directory = project_path / name
        if not directory.is_dir():
            continue
        try:
            modules = [path for path in directory.rglob("test*.py") if path.is_file()]
        except OSError:
            continue
        if any(not _is_discoverable(directory, module) for module in modules):
            return True
    return False


def _python_test_modules(project_path: Path) -> list[Path]:
    """Python test files at the root and under `tests/`, unbounded.

    Deliberately not `find_test_files`: that one is capped at five and
    exists to name files in a message, while this one answers a question
    where a missed file is a wrong verdict.
    """
    modules: list[Path] = []
    try:
        modules.extend(
            path
            for path in project_path.glob("*.py")
            if path.is_file() and _is_test_filename(path.name)
        )
    except OSError:
        return modules
    for name in _TEST_DIRS:
        directory = project_path / name
        if not directory.is_dir():
            continue
        try:
            modules.extend(
                path
                for path in directory.rglob("*.py")
                if path.is_file() and _is_test_filename(path.name)
            )
        except OSError:
            continue
    return modules


def _has_uncollectable_tests(project_path: Path) -> bool:
    """Are there tests `unittest discover` would silently not collect?

    The question `_has_undiscoverable_tests` does NOT ask (OPEN-47). That
    one asks whether unittest can *enter* a directory, which `__init__.py`
    answers; this one asks whether unittest can *collect* what is inside,
    which only the file's own shape answers. run8 failed on exactly that
    gap: `tests/__init__.py` existed, so the directory was reachable, and
    every test in it was a bare `def test_*`, so unittest collected none of
    them. The gate ran 10 of 23 tests and reported `passed`.

    Measured on the finished project, `/Users/archish/Documents/ai-ml/test-rudra-run8`:

        python3 -m unittest discover        Ran 10 tests -- OK
        python3 -m pytest --collect-only    23 tests collected

    run6 failed loudly at "collected nothing" and OPEN-34 caught it; run8
    failed silently at "collected 43% of them" and nothing did.

    Shape, not execution, like everything else in this module -- the file is
    read, never imported.
    """
    for module in _python_test_modules(project_path):
        try:
            text = module.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _MODULE_LEVEL_TEST.search(text) or _IMPORTS_PYTEST.search(text):
            return True
    return False


# Suffixes a test file can carry, across every stack the gate knows. Rust is
# absent on purpose: `#[test]` lives inline in the module it tests, so a Rust
# project with a full suite has no test-named file and would be reported as
# having none. Claiming less than we can see is the safe direction here --
# see `find_test_files`.
_TEST_FILE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go"})

# Enough names to make the gate's message concrete without pasting a suite
# into it.
_MAX_REPORTED_TEST_FILES = 5


def _is_test_filename(name: str) -> bool:
    """Does this filename claim to be a test, in any stack's spelling?

    `test_models.py`, `models_test.py`, `models_test.go`, `api.test.ts` and
    `api.spec.tsx` all do.
    """
    path = Path(name)
    if path.suffix not in _TEST_FILE_SUFFIXES:
        return False
    stem = path.stem
    return (
        name.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


def find_test_files(project_path: Path) -> list[str]:
    """Test files visible on disk, project-relative, capped and sorted.

    The question the gate needs answered before it reads "no tests were
    collected" as a pass (OPEN-34): are there tests here that the runner
    failed to find? An empty list is the greenfield case A1.57 protects --
    tests not written yet -- and a non-empty one is a broken gate.

    Bounded and skip-aware for the same reason `_has_source_file` is: a
    `.venv` or `node_modules` holds thousands of somebody else's test files,
    and counting them would fail the gate of every project that has one.
    """
    found: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if len(found) >= _MAX_REPORTED_TEST_FILES:
            return
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if len(found) >= _MAX_REPORTED_TEST_FILES:
                return
            if entry.is_file() and _is_test_filename(entry.name):
                found.append(entry.relative_to(project_path).as_posix())
        if depth <= 0:
            return
        for entry in entries:
            if (
                entry.is_dir()
                and entry.name not in ALL_SKIP_DIRS
                and not entry.name.startswith(".")
            ):
                walk(entry, depth - 1)

    walk(project_path, _INFERENCE_DEPTH)
    return sorted(found)


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
    # The same reasoning one step further, on layout evidence rather than on
    # a venv (OPEN-28). `unittest discover` cannot enter a directory that is
    # not an importable package, so a `tests/` folder without `__init__.py`
    # is a pytest layout by construction -- emitting unittest for it produces
    # "Ran 0 tests" forever, which the gate reports as `not_applicable` and
    # therefore PASSES.
    if _has_undiscoverable_tests(project_path):
        return [interpreter, "-m", "pytest"]
    # And one question further still (OPEN-47): a directory unittest CAN
    # enter may hold tests it cannot collect. That is the silent half --
    # "Ran 10 tests, OK" over a suite of 23 -- and it passes the gate.
    if _has_uncollectable_tests(project_path):
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
        #
        # --explicit-package-bases because without it mypy REFUSES TO RUN --
        # not a type error, a flat refusal -- on a project whose directory
        # name is not a valid Python identifier and which has a root
        # __init__.py (OPEN-29). Both conditions are ordinary: `my-app` and
        # `todo-cli` are normal names, and run5's coder wrote a root
        # __init__.py unprompted, which blocked that run's first task and
        # cost three more to a cascade of drops.
        #
        # Measured 2026-08-26: with the flag the same project checks clean
        # AND a real `Incompatible return value type` still surfaces, and an
        # ordinary src/ or flat layout is byte-identical with and without.
        # So the gate keeps BLOCKING -- this is a fix to how mypy is invoked,
        # not a retreat from type-checking generated code.
        #
        # Only on Rudra's own invocation. The venv branch above is the
        # project's mypy: it has adopted the tool, and its own config -- which
        # may set this very option -- must outrank Rudra's opinion.
        # advisory because `--ignore-missing-imports` makes the verdict a
        # function of RUDRA's site-packages, not the project's (OPEN-38).
        # Measured 2026-08-27 on run6's `src/models.py`: with flask_sqlalchemy
        # installed here mypy resolved it for real and reported 3 errors; on a
        # clean Rudra install the same file reports 2. Same code, same mypy,
        # two answers -- and this stage used to BLOCK on whichever one it got.
        # The detail string below already said the dependencies are not
        # resolved; the stage simply treated that verdict as authoritative
        # anyway. It is reported in full, exactly as lint is (S9a.2).
        # --no-site-packages and MYPYPATH="" for the other half of OPEN-38.
        # Advisory says the verdict must not fail a task; these two say it
        # must at least be the SAME verdict on every machine, because it is
        # still printed and a finding nobody else can reproduce is not worth
        # reading. They do not make the failing file pass -- measured
        # 2026-08-27, run6's `Model = SQLAlchemy.Model` goes from 3 errors to
        # 2, because `Model = <variable>` used as a base class is a
        # valid-type error however the import resolved.
        #
        # MYPYPATH="" rather than a deletion: an empty value and an unset one
        # produce byte-identical output, so the record needs no way to say
        # "remove this variable".
        return CommandResolution(
            _bundled(
                "mypy",
                "--ignore-missing-imports",
                "--explicit-package-bases",
                "--no-site-packages",
                ".",
            ),
            OK,
            detail="Rudra's bundled mypy; project dependencies are not resolved",
            tool="mypy",
            advisory=True,
            env=(("MYPYPATH", ""),),
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
