"""OPEN-143: the permission rules the docs print must allow what the gate runs.

`Documentation/06-troubleshooting.md` offered `allow = ["execute:pytest*"]` as
the narrow alternative to `--allow-shell` for `run_tests`, and it matched no
command the gate has ever run: the gate asks about an ABSOLUTE argv
(`stacks/detect.py::_python_test_command`) and a permissive rule is matched
against the command as written (`permissions/rules.py::_execute_matches`), so
`pytest*` could only match a command starting with the bare word.

Naming the venv's command was not enough either, which re-verifying the plan
found: under `--auto` without shell Rudra's own `.venv` build is denied, so the
tests run under the machine's interpreter, and the type-check -- Rudra's own
interpreter -- stops the run before they are reached. So this reads the
section's TOML block and puts the resolver's real commands, with a venv and
without one, through the real engine. It fails if the docs drift from the code
again -- A4.1's lesson (`test_docs_match_cli.py`): prose is checked by whoever
happens to read it.
"""

from __future__ import annotations

import re
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

from rudra.permissions.rules import PermissionEngine
from rudra.stacks.detect import PROFILES, resolve_test_command, resolve_typecheck_command

REPO = Path(__file__).resolve().parent.parent
TROUBLESHOOTING = REPO / "Documentation" / "06-troubleshooting.md"
HEADING = '### `run_tests` says "Running tests was not permitted"'
# The section's example paths, replaced by real ones before the engine sees them.
PROJECT_PLACEHOLDER = "/home/you/todo"
RUDRA_PLACEHOLDER = "/home/you/.local/share/pipx/venvs/rudra/bin/python"
PYTHON = next(profile for profile in PROFILES if profile.name == "python")


def _section() -> str:
    text = TROUBLESHOOTING.read_text(encoding="utf-8")
    return text.split(HEADING, 1)[1].split("\n### ", 1)[0]


def _documented_allow_rules() -> list[str]:
    rules: list[str] = []
    for block in re.findall(r"```toml\n(.*?)```", _section(), flags=re.S):
        rules += tomllib.loads(block).get("permissions", {}).get("allow", [])
    return rules


def _project(root: Path, *, venv: bool) -> Path:
    """A Python project declaring pytest, with or without a venv holding it."""
    project = root / "todo"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'todo'\ndependencies = ['pytest']\n", encoding="utf-8"
    )
    if venv:
        binaries = project / ".venv" / "bin"
        binaries.mkdir(parents=True)
        for name in ("python", "pytest"):
            (binaries / name).write_text("#!/bin/sh\n", encoding="utf-8")
            (binaries / name).chmod(0o755)
    return project


def _test_command(project: Path) -> str:
    """What the gate's test stage, and `run_tests`, ask the engine about."""
    argv = resolve_test_command(project, PYTHON)
    assert argv is not None
    return shlex.join(argv)


def _typecheck_command(project: Path) -> str:
    argv = resolve_typecheck_command(project, PYTHON).argv
    assert argv is not None
    return shlex.join(argv)


def _allows(project: Path, rules: list[str] | tuple[str, ...], command: str) -> bool:
    engine = PermissionEngine(
        mode="auto", allow=tuple(rules), deny=(), floor_disable=(), project_root=project
    )
    return engine.decide("execute", {"command": command}).effect == "allow"


def test_the_section_documents_allow_rules_and_where_to_copy_them_from():
    rules = _documented_allow_rules()
    assert rules, "the section's TOML block lost its allow list"
    # A wildcard at the head of a permissive rule allows arbitrary commands.
    assert not [rule for rule in rules if rule.partition(":")[2].startswith("*")], rules
    # verify.log has no test stage when the type-check escalated first, which
    # is this section's case; the audit log records every refused command.
    assert "permissions.jsonl" in _section()


def test_with_a_venv_the_documented_rules_allow_the_tests_and_the_type_check(tmp_path):
    project = _project(tmp_path, venv=True)
    rules = [
        rule.replace(PROJECT_PLACEHOLDER, str(project)).replace(RUDRA_PLACEHOLDER, sys.executable)
        for rule in _documented_allow_rules()
    ]

    for command in (_test_command(project), _typecheck_command(project)):
        assert _allows(project, rules, command), (command, rules)


def test_without_a_venv_the_tests_run_under_another_interpreter(tmp_path):
    """Rudra's own `.venv` build is a command, denied under `--auto` without
    shell (run E1), so the section must say so: its test rule names `.venv`."""
    project = _project(tmp_path, venv=False)
    rules = [rule.replace(PROJECT_PLACEHOLDER, str(project)) for rule in _documented_allow_rules()]
    command = _test_command(project)

    assert not command.startswith(str(project / ".venv")), command
    assert not _allows(project, rules, command), (command, rules)
    assert "-m venv .venv" in _section()


def test_the_recipe_allows_each_refused_command_and_nothing_chained_after_it(tmp_path):
    """`execute:` + the `arg` permissions.jsonl recorded + `*`, as the section says."""
    with_venv = _project(tmp_path / "a", venv=True)
    without = _project(tmp_path / "b", venv=False)

    for project, command in (
        (with_venv, _test_command(with_venv)),
        (without, _test_command(without)),
        (without, _typecheck_command(without)),
    ):
        rule = (f"execute:{command}*",)
        assert _allows(project, rule, command), command
        assert not _allows(project, rule, f"{command} && rm -rf ~"), command


@pytest.mark.parametrize("venv", [True, False], ids=["venv", "no-venv"])
def test_the_old_advice_really_did_match_nothing(tmp_path, venv):
    """Why the section changed, pinned: `execute:pytest*` is refused for the
    gate's test command either way, so a doc that brings it back as the test
    rule fails the tests above."""
    project = _project(tmp_path, venv=venv)

    assert not _allows(project, ("execute:pytest*",), _test_command(project))
