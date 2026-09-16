"""OPEN-120: Rudra builds and syncs a Python project's .venv, and nothing else's.

Run a04f89bd2ed6's tester pip3-installed Flask and SQLAlchemy into the
machine's Python. PIP_REQUIRE_VIRTUALENV now refuses that, so a Python project
needs somewhere pip MAY install -- and the coder, which holds no shell, needs
Rudra to do the installing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from rich.console import Console

from rudra.context.usage import RunUsage
from rudra.shell.runner import CommandResult
from rudra.testing import project_env
from rudra.testing.project_env import (
    DENIED,
    FAILED,
    MARKER,
    OK,
    PROJECT_ENV_KIND,
    PROJECT_ENV_NOTICE,
    SKIPPED,
    UNCHANGED,
    ProjectEnvState,
    declared_dependencies,
    ensure_project_env,
)

MACHINE_PYTHON = "/usr/bin/python3"
GATE = object()


@dataclass
class FakeToolsCfg:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    tools: FakeToolsCfg = field(default_factory=FakeToolsCfg)
    models: dict = field(default_factory=dict)


class FakeTrace:
    def __init__(self):
        self.notices: list[dict] = []

    def notice(self, payload, *, role="", name="", **kwargs):
        self.notices.append({"payload": payload, "role": role, "name": name})


def _step_of(argv: tuple[str, ...]) -> str:
    if argv[1:3] == ("-m", "venv"):
        return "create"
    if argv[3:] == ("install", "pytest"):
        return "pytest"
    return "deps"


class FakeShell:
    """Stands in for run_gated, and leaves on disk what the real command would.

    `fail` and `deny` name steps. `half_built` makes a failed create leave
    `bin/python` behind, which is what Debian's python3 without python3-venv
    does. `layout="Scripts"` writes Windows' `Scripts/python.exe`.
    """

    def __init__(self, project: Path, *, fail=(), deny=(), layout="bin", half_built=False):
        self.project = project
        self.fail = set(fail)
        self.deny = set(deny)
        self.layout = layout
        self.half_built = half_built
        self.calls: list[tuple[str, ...]] = []

    def _touch(self, name: str) -> None:
        suffix = ".exe" if self.layout == "Scripts" else ""
        directory = self.project / ".venv" / self.layout
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}{suffix}").write_text("", encoding="utf-8")

    def __call__(self, argv, *, cwd, gate, console, timeout, env=None, read_only=False):
        argv = tuple(str(part) for part in argv)
        self.calls.append(argv)
        step = _step_of(argv)
        command = " ".join(argv)
        if step in self.deny:
            return CommandResult(
                argv, command, None, "", "", denied=True, denial_reason="the user rejected it"
            )
        failed = step in self.fail
        if step == "create" and (not failed or self.half_built):
            self._touch("python")
        if step == "pytest" and not failed:
            self._touch("pytest")
        if failed:
            return CommandResult(argv, command, 1, "", f"ERROR: {step} went wrong")
        return CommandResult(argv, command, 0, "done", "")


@pytest.fixture(autouse=True)
def _machine(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(project_env, "system_interpreter", lambda: MACHINE_PYTHON)


@pytest.fixture
def events():
    """The records the run log would receive. A real handler rather than
    caplog, for tests/test_run_cost_logging.py's reason: trace/debug.py turns
    `rudra` propagation off."""
    seen: list[dict] = []
    logger = logging.getLogger("rudra.testing.project_env")

    class _Capture(logging.Handler):
        def emit(self, record):
            event = getattr(record, "event", None)
            if isinstance(event, dict):
                seen.append(event)

    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield seen
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _shell(monkeypatch, project: Path, **kwargs) -> FakeShell:
    shell = FakeShell(project, **kwargs)
    monkeypatch.setattr(project_env, "run_gated", shell)
    return shell


def _ensure(project: Path, state: ProjectEnvState | None = None, *, gate=GATE, **kwargs) -> str:
    return ensure_project_env(
        project,
        gate=gate,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        state=state if state is not None else ProjectEnvState(),
        **kwargs,
    )


def _requirements(project: Path, text: str = "flask\n") -> None:
    (project / "requirements.txt").write_text(text, encoding="utf-8")


# --- 1. When nothing may run -------------------------------------------------


def test_a_project_that_is_not_python_runs_nothing(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path) == SKIPPED
    assert shell.calls == []
    assert not (tmp_path / ".venv").exists()


def test_no_gate_means_no_command_even_in_a_python_project(tmp_path, monkeypatch):
    """run_gated skips the decision when there is no gate. Every loop test
    builds a context without one, and must never find a real `python3 -m
    venv` behind `_verify`."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path, gate=None) == SKIPPED
    assert shell.calls == []


@pytest.mark.parametrize("directory", [".venv", "venv"])
def test_a_venv_rudra_did_not_build_is_left_alone(tmp_path, monkeypatch, directory):
    """The user's uv- or poetry-managed venv is theirs. Installing into it is
    the defect this module exists to prevent, one directory closer."""
    _requirements(tmp_path)
    (tmp_path / directory / "bin").mkdir(parents=True)
    (tmp_path / directory / "bin" / "python").write_text("", encoding="utf-8")
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path) == SKIPPED
    assert shell.calls == []


def test_an_active_virtualenv_in_the_agents_shell_means_no_venv_is_built(tmp_path, monkeypatch):
    """A Poetry or Pipenv environment lives outside the project, and the
    gate already finds its python first on PATH."""
    _requirements(tmp_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path.parent / "elsewhere" / "env"))
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path) == SKIPPED
    assert shell.calls == []


# --- 2. Building it ----------------------------------------------------------


def test_a_python_project_gets_a_venv_from_the_machine_interpreter(tmp_path, monkeypatch):
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path) == OK

    assert shell.calls[0] == (MACHINE_PYTHON, "-m", "venv", ".venv")
    marker = json.loads((tmp_path / ".venv" / MARKER).read_text(encoding="utf-8"))
    assert marker["created_by"] == "rudra"
    assert (tmp_path / ".venv" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_a_project_of_bare_py_files_is_python_too(tmp_path, monkeypatch):
    """Python is the one marker-optional stack (stacks/detect.py::_inferred).
    With nothing declared, the deps step does not run at all."""
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    shell = _shell(monkeypatch, tmp_path)

    assert _ensure(tmp_path) == OK
    assert [_step_of(call) for call in shell.calls] == ["create", "pytest"]


def test_pytest_is_installed_on_its_own_before_the_declared_dependencies(tmp_path, monkeypatch):
    """pip resolves a whole install before writing any of it, so a bad pin in
    a combined call would leave no pytest -- and a venv with no pytest makes
    the gate escalate and end the run (verify/pipeline.py:693-703)."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)

    _ensure(tmp_path)

    python = str(tmp_path / ".venv" / "bin" / "python")
    assert shell.calls[1] == (python, "-m", "pip", "install", "pytest")
    assert shell.calls[2] == (python, "-m", "pip", "install", "-r", "requirements.txt")


def test_a_failed_create_leaves_no_half_built_venv_and_is_not_retried(tmp_path, monkeypatch):
    """Left on disk, stacks/detect.py:472 would run the gate under a python
    with no pip and no pytest."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path, fail={"create"}, half_built=True)
    state = ProjectEnvState()

    assert _ensure(tmp_path, state) == FAILED
    assert not (tmp_path / ".venv").exists()
    assert _ensure(tmp_path, state) == SKIPPED
    assert len(shell.calls) == 1


def test_a_denied_command_is_not_asked_again_this_run(tmp_path, monkeypatch):
    """ask mode prompts for it. A user who said no is not asked at every gate run."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path, deny={"create"})
    state = ProjectEnvState()

    assert _ensure(tmp_path, state) == DENIED
    assert not (tmp_path / ".venv").exists()
    assert _ensure(tmp_path, state) == SKIPPED
    assert len(shell.calls) == 1


def test_a_windows_layout_is_found_by_shape(tmp_path, monkeypatch):
    """`Scripts/python.exe` on any host (CLAUDE.md §1.8)."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path, layout="Scripts")

    assert _ensure(tmp_path) == OK
    assert Path(shell.calls[1][0]).name == "python.exe"
    assert [_step_of(call) for call in shell.calls] == ["create", "pytest", "deps"]


# --- 3. Keeping it in sync ---------------------------------------------------


def test_an_unchanged_declaration_runs_no_pip(tmp_path, monkeypatch):
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)
    state = ProjectEnvState()
    _ensure(tmp_path, state)
    before = len(shell.calls)

    assert _ensure(tmp_path, state) == UNCHANGED
    assert len(shell.calls) == before


def test_editing_a_declaration_installs_again_and_only_that(tmp_path, monkeypatch):
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)
    state = ProjectEnvState()
    _ensure(tmp_path, state)
    before = len(shell.calls)

    _requirements(tmp_path, "flask\nsqlalchemy\n")

    assert _ensure(tmp_path, state) == OK
    assert [_step_of(call) for call in shell.calls[before:]] == ["deps"]


def test_a_later_run_trusts_the_fingerprint_on_disk(tmp_path, monkeypatch):
    """The marker lives in the venv, so a fresh process installs nothing that
    has not changed -- and deleting the venv deletes the fingerprint with it."""
    _requirements(tmp_path)
    shell = _shell(monkeypatch, tmp_path)
    _ensure(tmp_path)
    before = len(shell.calls)

    assert _ensure(tmp_path, ProjectEnvState()) == UNCHANGED
    assert len(shell.calls) == before


def test_a_failed_install_is_kept_for_the_blocker_and_not_retried(tmp_path, monkeypatch):
    """Offline, pip spends its whole retry budget on every call, and _verify
    runs every attempt. The same declarations are tried once per run."""
    _requirements(tmp_path, "flask==99\n")
    shell = _shell(monkeypatch, tmp_path, fail={"deps"})
    state = ProjectEnvState()

    assert _ensure(tmp_path, state) == FAILED
    assert "deps went wrong" in state.failure
    marker = json.loads((tmp_path / ".venv" / MARKER).read_text(encoding="utf-8"))
    assert marker["fingerprint"] is None
    before = len(shell.calls)

    assert _ensure(tmp_path, state) == FAILED
    assert len(shell.calls) == before

    shell.fail.clear()
    _requirements(tmp_path, "flask\n")
    assert _ensure(tmp_path, state) == OK
    assert state.failure == ""


def test_pyproject_declarations_are_read_without_building_the_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "todo"\n'
        'dependencies = ["flask>=3", "sqlalchemy"]\n'
        "[project.optional-dependencies]\n"
        'test = ["pytest-cov", "flask>=3"]\n'
        "[dependency-groups]\n"
        'dev = ["ruff", {include-group = "test"}]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text("black\n", encoding="utf-8")
    _requirements(tmp_path)

    _, files, names = declared_dependencies(tmp_path, MACHINE_PYTHON)

    assert files == ["requirements-dev.txt", "requirements.txt"]
    assert names == ["flask>=3", "sqlalchemy", "pytest-cov", "ruff"]


def test_an_unparseable_pyproject_names_nothing_but_still_changes_the_fingerprint(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    first, _, names = declared_dependencies(tmp_path, MACHINE_PYTHON)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    second, _, _ = declared_dependencies(tmp_path, MACHINE_PYTHON)

    assert names == []
    assert first != second


# --- 4. What the run log says (CLAUDE.md §8a) --------------------------------


def test_every_step_reaches_the_run_log_the_tally_and_the_trace(tmp_path, monkeypatch, events):
    _requirements(tmp_path)
    _shell(monkeypatch, tmp_path)
    usage, trace = RunUsage(), FakeTrace()

    _ensure(tmp_path, usage=usage, trace=trace)

    steps = [e for e in events if e["kind"] == PROJECT_ENV_KIND]
    assert [(e["step"], e["outcome"]) for e in steps] == [
        ("create", OK),
        ("pytest", OK),
        ("deps", OK),
    ]
    assert all(e["command"] and "seconds" in e for e in steps)
    assert usage.as_log()["run"]["env_syncs"] == 3
    assert [n["name"] for n in trace.notices] == [PROJECT_ENV_NOTICE] * 3


def test_a_credential_in_the_command_or_output_never_reaches_the_event_raw(
    tmp_path, monkeypatch, events
):
    """Final whole-branch review, finding 1: `_record` put `result.command` and
    `tail` straight into the debug event with no redaction, while
    `TraceSink.notice()` a few lines below redacts the very same command
    string -- so a PEP 508 direct URL with basic-auth credentials (a real
    private-index pattern) reached debug-<id>.jsonl once redacted (via the
    notice) and once raw (via the event). CLAUDE.md §8a: "Never put a secret
    in a diagnostic. Redaction happens where the event is built."."""
    secret = "ghp_abcdefgh12345678"
    _requirements(tmp_path, f"pkg @ https://x-access-token:{secret}@github.com/org/repo.git\n")

    def fake_run_gated(argv, *, cwd, gate, console, timeout, env=None, read_only=False):
        argv = tuple(str(part) for part in argv)
        step = _step_of(argv)
        if step == "create":
            directory = tmp_path / ".venv" / "bin"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "python").write_text("", encoding="utf-8")
            return CommandResult(argv, " ".join(argv), 0, "done", "")
        if step == "pytest":
            (tmp_path / ".venv" / "bin" / "pytest").write_text("", encoding="utf-8")
            return CommandResult(argv, " ".join(argv), 0, "done", "")
        command = f'pip install "pkg @ https://x-access-token:{secret}@github.com/org/repo.git"'
        stderr = f"ERROR: could not fetch https://x-access-token:{secret}@github.com/org/repo.git"
        return CommandResult(argv, command, 1, "", stderr)

    monkeypatch.setattr(project_env, "run_gated", fake_run_gated)

    assert _ensure(tmp_path) == FAILED

    steps = [e for e in events if e["kind"] == PROJECT_ENV_KIND and e["step"] == "deps"]
    assert len(steps) == 1
    event = steps[0]
    assert secret not in event["command"], "the secret leaked into the debug event's command"
    assert secret not in event["tail"], "the secret leaked into the debug event's tail"
    # The rest of the text must survive -- a redactor that eats the whole
    # line defeats the point of having a diagnostic at all.
    assert "pip install" in event["command"]
    assert "could not fetch" in event["tail"]


def test_a_skip_is_written_once_per_reason(tmp_path, monkeypatch, events):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    _shell(monkeypatch, tmp_path)
    state = ProjectEnvState()

    _ensure(tmp_path, state)
    _ensure(tmp_path, state)

    assert [(e["outcome"], e["reason"]) for e in events] == [(SKIPPED, "not a python project")]
