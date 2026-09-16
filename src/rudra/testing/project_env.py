"""The project's own virtualenv, built and kept in sync by Rudra (OPEN-120).

WHY RUDRA WRITES A DIRECTORY INTO THE USER'S PROJECT
----------------------------------------------------
Run ``a04f89bd2ed6``'s tester, in a Flask project with no venv, ran
``pip install -r requirements.txt`` (no ``pip`` on PATH), then ``which
pip3``, then ``pip3 install Flask==3.0.3 SQLAlchemy==2.0.29`` -- into
``/Library/Frameworks/Python.framework/Versions/3.12``, the MACHINE's
interpreter, uninstalling the user's Flask 3.1.3 and SQLAlchemy 2.0.50 to do
it. OPEN-80 had already taken Rudra's own venv off PATH, so in a project with
none of its own ``pip3`` meant the machine.

``permissions/env.py`` now sets ``PIP_REQUIRE_VIRTUALENV=1`` and pip refuses.
That alone leaves a Python project nowhere to install anything. So for a
Python project -- and only one -- Rudra builds ``.venv`` and installs into it
what the project DECLARES. The owner's requirement, 2026-09-15.

WHY RUDRA INSTALLS, AND NOT AN AGENT
------------------------------------
Once ``.venv/bin/python`` exists, ``stacks/detect.py::_python_test_command``
runs the gate under it. An empty venv has no pytest, which
``testing/runner.py::_runner_not_importable`` reports as a launch error --
``MISSING_TOOL`` with ``escalate=True`` (verify/pipeline.py:693-703) -- and an
escalation ends the run. The project's own dependencies would be missing too,
and a missing module is reported to the coder, which holds no shell
(``subagents/registry.py:23``). So Python decides here, as it decides DONE:
the model edits ``requirements.txt`` and Rudra installs it.

THREE STEPS, KEPT APART
-----------------------
``create`` (the MACHINE's interpreter, never Rudra's -- D18), ``pytest``, then
``deps``. pytest is its own step because pip resolves a whole install before
writing any of it: one bad pin in a combined call installs nothing, pytest
included, and the gate escalates. Apart, a bad pin leaves pytest in place, the
tests fail on a missing module, and the fix loop is shown pip's output beside
them (loop/engine.py::_env_sync_note).

WHAT IT NEVER TOUCHES
---------------------
A ``.venv`` or ``venv`` it did not build -- ``MARKER`` is how it knows -- and a
project whose shell already has a virtualenv active. Both are the user's.
Nothing runs without a permission gate: every command is ``execute`` through
``shell/runner.py::run_gated``, so ``ask`` mode shows each one and ``--auto``
without ``--allow-shell`` denies them, as it denies the gate's own tests.

Known limits, recorded rather than handled: no editable install and no
``setup.py`` -- a flat-layout editable install fails on ordinary projects --
and conda, whose environments pip does not count as virtualenvs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import CommandResult, run_gated
from rudra.stacks.detect import VENV_DIRS, detect, system_interpreter, venv_executable
from rudra.testing.runner import _tail
from rudra.trace.redact import redact

_LOG = logging.getLogger(__name__)

PROJECT_ENV_KIND = "project_env"
"""The `kind` every record here carries in `debug-<id>.jsonl`. It is what a
maintainer greps for, so it has one spelling."""

PROJECT_ENV_NOTICE = "project-env"
"""The NOTICE name for a command this module ran."""

VENV_DIR = ".venv"

MARKER = ".rudra-sync.json"
"""Inside `.venv`: present only in a venv Rudra built, holding the fingerprint
of the last successful install. It lives WITH the venv on purpose -- delete
the venv and the fingerprint goes with it."""

REQUIREMENTS_GLOB = "requirements*.txt"

OK = "ok"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
FAILED = "failed"
DENIED = "denied"

_FAILURE_LINES = {
    "create": "Could not build .venv for this Python project; the gate runs as before, "
    "and pip still refuses to install outside a virtualenv.",
    "pytest": "Could not install pytest into .venv, so the test stage cannot run.",
    "deps": "Installing this project's declared dependencies into .venv failed; the next "
    "failing gate shows pip's output to the coder.",
}


@dataclass
class ProjectEnvState:
    """What one run has learned about the project's venv. Never persisted.

    `failed` holds fingerprints whose install failed this run, so an offline
    machine pays pip's network timeout once per change to the declarations
    rather than once per gate run. `failure` is pip's tail from the latest
    failed install, "" once one succeeds; the engine appends it to the next
    blocker. `denied` and `create_failed` stop a declined or impossible
    create from being attempted again at every gate run.
    """

    failed: set[str] = field(default_factory=set)
    failure: str = ""
    denied: bool = False
    create_failed: bool = False
    skips_logged: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Call:
    """One `ensure_project_env` call's collaborators, so helpers take one argument."""

    project_path: Path
    gate: Any
    console: Console
    cfg: Any
    state: ProjectEnvState
    usage: Any
    trace: Any


def ensure_project_env(
    project_path: Path,
    *,
    gate: Any,
    console: Console,
    cfg: Any,
    state: ProjectEnvState,
    usage: Any = None,
    trace: Any = None,
) -> str:
    """Build `.venv` if this Python project has none, then install what it declares.

    Returns OK, UNCHANGED, SKIPPED, FAILED or DENIED. A command that fails
    is a return value, never an exception; the caller still guards the
    call, because an internal error here must degrade to the gate as it ran
    before OPEN-120 rather than end a run.
    """
    project_path = Path(project_path)
    reason = _skip_reason(project_path, gate=gate, cfg=cfg, state=state)
    if reason is not None:
        _log_skip(state, reason)
        return SKIPPED

    call = _Call(project_path, gate, console, cfg, state, usage, trace)
    venv = project_path / VENV_DIR
    if not venv.exists():
        outcome = _create(call)
        if outcome != OK:
            return outcome

    python = venv_executable(project_path, "python")
    if python is None:  # pragma: no cover - _create removes a venv with no python
        return FAILED

    if venv_executable(project_path, "pytest") is None:
        outcome, _ = _step(call, "pytest", [str(python), "-m", "pip", "install", "pytest"])
        if outcome != OK:
            return outcome

    fingerprint, requirement_files, names = declared_dependencies(project_path, str(python))
    if _read_marker(venv).get("fingerprint") == fingerprint:
        state.failure = ""
        return UNCHANGED
    if fingerprint in state.failed:
        return FAILED
    if requirement_files or names:
        argv = [str(python), "-m", "pip", "install"]
        for name in requirement_files:
            argv += ["-r", name]
        argv += names
        outcome, tail = _step(call, "deps", argv)
        if outcome == FAILED:
            state.failed.add(fingerprint)
            state.failure = tail
        if outcome != OK:
            return outcome
    _write_marker(venv, fingerprint)
    state.failure = ""
    return OK


def declared_dependencies(project_path: Path, interpreter: str) -> tuple[str, list[str], list[str]]:
    """(fingerprint, requirement files, requirement strings) this project declares.

    The fingerprint covers the interpreter and every declaration file's name
    and bytes, so editing a pin, adding a requirements file or rebuilding the
    venv under another Python all install again, and nothing else does. An
    unparseable `pyproject.toml` names nothing but still changes it.
    """
    project_path = Path(project_path)
    hasher = hashlib.sha256(interpreter.encode("utf-8"))
    files = sorted(path.name for path in project_path.glob(REQUIREMENTS_GLOB) if path.is_file())
    for name in files:
        hasher.update(b"\0" + name.encode("utf-8") + b"\0" + _bytes(project_path / name))
    names: list[str] = []
    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file():
        raw = _bytes(pyproject)
        hasher.update(b"\0pyproject.toml\0" + raw)
        names = _pyproject_requirements(raw)
    return hasher.hexdigest(), files, names


def _skip_reason(project_path: Path, *, gate: Any, cfg: Any, state: ProjectEnvState) -> str | None:
    """Why nothing may run here, or None when this project's venv is Rudra's to build."""
    if gate is None:
        # run_gated skips the decision when there is no gate, and an install
        # nobody could have approved is not one to run.
        return "no permission gate"
    if state.denied:
        return "a venv command was denied this run"
    if state.create_failed:
        return "building .venv failed this run"
    if not any(profile.name == "python" for profile in detect(project_path)):
        return "not a python project"
    if scrubbed_env(cfg).get("VIRTUAL_ENV"):
        return "a virtualenv is already active in the agents' shell"
    for name in VENV_DIRS:
        candidate = project_path / name
        if candidate.exists() and not (candidate / MARKER).is_file():
            return f"{name}/ was not built by Rudra"
    return None


def _create(call: _Call) -> str:
    """`<machine python> -m venv .venv`, then the marker and a `.gitignore` inside it."""
    venv = call.project_path / VENV_DIR
    interpreter = system_interpreter()
    outcome, _ = _step(call, "create", [interpreter, "-m", "venv", VENV_DIR])
    if outcome == OK and venv_executable(call.project_path, "python") is None:
        outcome = FAILED
    if outcome != OK:
        # A failed create can leave a half-built directory -- Debian's python3
        # without python3-venv writes bin/python and then fails in ensurepip
        # -- and stacks/detect.py:472 would run every later gate under that
        # python, which has no pip and no pytest.
        shutil.rmtree(venv, ignore_errors=True)
        if outcome == FAILED:
            call.state.create_failed = True
        return outcome
    _write_marker(venv, None)
    ignore = venv / ".gitignore"
    if not ignore.exists():
        # What `python -m venv` writes itself from 3.13 on. 3.12 does not, and
        # without it a user's `git status` lists the whole environment.
        try:
            ignore.write_text("*\n", encoding="utf-8")
        except OSError:
            _LOG.debug("could not write .venv/.gitignore", exc_info=True)
    call.console.print(
        f"[dim]Built .venv for this Python project with {escape(interpreter)}. Rudra "
        "installs the project's declared dependencies into it before every gate run.[/dim]"
    )
    return OK


def _step(call: _Call, step: str, argv: list[str]) -> tuple[str, str]:
    """Run one command through the gate, record it, and return (outcome, pip's tail)."""
    started = time.monotonic()
    result = run_gated(
        argv,
        cwd=call.project_path,
        gate=call.gate,
        console=call.console,
        timeout=call.cfg.tools.test_timeout,
        env=scrubbed_env(call.cfg),
    )
    seconds = time.monotonic() - started
    if result.denied:
        outcome, tail = DENIED, result.denial_reason or "denied by the permission gate"
        call.state.denied = True
    elif result.ok:
        outcome, tail = OK, ""
    else:
        combined = f"{result.stdout}\n{result.stderr}".strip()
        outcome, tail = FAILED, _tail(combined) or "the command could not be started"
    _record(call, step, outcome, seconds, result, tail)
    return outcome, tail


def _record(
    call: _Call, step: str, outcome: str, seconds: float, result: CommandResult, tail: str
) -> None:
    """Write one command to the run log, the tally, the trace and -- on failure -- the console.

    CLAUDE.md §8a. Swallows its own failure: a run must not be reported
    failed because a log line could not be written. `command` and `tail` are
    redacted before they enter the event -- the same rule every other
    diagnostic in this project follows (`trace/redact.py`): redaction happens
    where the event is BUILT, not where it is rendered. `trace.notice()`
    below already redacts through `TraceSink`, so this is what makes the two
    agree instead of one showing `<redacted>` and the other showing the
    credential.
    """
    try:
        _LOG.debug(
            "project env %s: %s",
            step,
            outcome,
            extra={
                "event": {
                    "kind": PROJECT_ENV_KIND,
                    "step": step,
                    "outcome": outcome,
                    "seconds": round(seconds, 2),
                    "command": redact(result.command),
                    "exit_code": result.exit_code,
                    "tail": redact(tail),
                }
            },
        )
    except Exception:  # noqa: BLE001 - bookkeeping may never end a run
        pass
    try:
        if call.usage is not None and outcome != DENIED:
            call.usage.record_env_sync(seconds)
    except Exception:  # noqa: BLE001 - same rule
        _LOG.debug("project env step not counted", exc_info=True)
    try:
        if call.trace is not None:
            detail = f"\n{tail}" if tail else ""
            call.trace.notice(
                f"{step} {outcome} in {seconds:.1f}s: {result.command}{detail}",
                role="rudra",
                name=PROJECT_ENV_NOTICE,
            )
    except Exception:  # noqa: BLE001 - same rule
        _LOG.debug("project env step not announced", exc_info=True)
    if outcome == FAILED:
        call.console.print(f"[yellow]{_FAILURE_LINES[step]}[/yellow]")


def _log_skip(state: ProjectEnvState, reason: str) -> None:
    """Write a skip reason once per run, so a Python project with no record says why."""
    if reason in state.skips_logged:
        return
    state.skips_logged.add(reason)
    try:
        _LOG.debug(
            "project env skipped: %s",
            reason,
            extra={
                "event": {
                    "kind": PROJECT_ENV_KIND,
                    "step": "check",
                    "outcome": SKIPPED,
                    "reason": reason,
                }
            },
        )
    except Exception:  # noqa: BLE001 - bookkeeping may never end a run
        pass


def _pyproject_requirements(raw: bytes) -> list[str]:
    """Requirement strings from `[project]` and `[dependency-groups]`, in order, once each."""
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    found = _strings(project.get("dependencies"))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group in optional.values():
            found += _strings(group)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        # A `{include-group = "..."}` entry is a table, not a requirement;
        # the group it names is already in this loop.
        for group in groups.values():
            found += _strings(group)
    return list(dict.fromkeys(found))


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _read_marker(venv: Path) -> dict[str, Any]:
    try:
        data = json.loads((venv / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(venv: Path, fingerprint: str | None) -> None:
    try:
        (venv / MARKER).write_text(
            json.dumps({"created_by": "rudra", "fingerprint": fingerprint}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        _LOG.debug("could not write %s", MARKER, exc_info=True)


__all__ = [
    "DENIED",
    "FAILED",
    "MARKER",
    "OK",
    "PROJECT_ENV_KIND",
    "PROJECT_ENV_NOTICE",
    "SKIPPED",
    "UNCHANGED",
    "VENV_DIR",
    "ProjectEnvState",
    "declared_dependencies",
    "ensure_project_env",
]
