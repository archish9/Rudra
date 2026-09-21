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

import ast
import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

import pytest

import rudra.permissions as permissions_package
from rudra.permissions.rules import WRAPPED_EXECUTE_TOOLS, PermissionEngine, gated_arg
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


# --- OPEN-145: the audit-log examples show what permissions.jsonl holds ---

TOOLS = REPO / "Documentation" / "11-tools.md"
PERMISSIONS = REPO / "Documentation" / "09-permissions.md"
AUDIT_EXAMPLES = {
    "11-tools": (TOOLS, "## Reading the audit log"),
    "09-permissions": (PERMISSIONS, "## The audit log"),
}
# The argument key of each tool an example shows; `gated_arg` reads the same one.
ARG_KEYS = {
    "execute": "command",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
}


def _audit_section(page: Path, heading: str) -> str:
    return page.read_text(encoding="utf-8").split(heading, 1)[1].split("\n## ", 1)[0]


def _audit_example(page: Path, heading: str) -> list[dict]:
    """The section's first ```json block, one object at a time -- an object may
    wrap across lines, as `09-permissions.md`'s do."""
    match = re.search(r"```json\n(.*?)```", _audit_section(page, heading), flags=re.S)
    assert match is not None, f"{page.name} lost its audit example"
    text, decoder, entries, index = match.group(1), json.JSONDecoder(), [], 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return entries
        entry, index = decoder.raw_decode(text, index)
        entries.append(entry)


def test_the_audit_example_records_run_tests_as_the_log_does():
    """OPEN-145: a wrapped tool's own line carries no arg -- `gated_arg` has no
    key for it -- and `run_tests`'s command is judged, and recorded, on the
    `execute` line after it, at the absolute path the gate resolved.
    `git_diff`'s is not: in-root it runs read-only git, ungated (OPEN-146)."""
    lines = _audit_example(*AUDIT_EXAMPLES["11-tools"])
    wrapped = [i for i, entry in enumerate(lines) if entry["tool"] in WRAPPED_EXECUTE_TOOLS]
    assert wrapped, "the example no longer shows a wrapped tool"
    for i in wrapped:
        assert lines[i]["arg"] == gated_arg(lines[i]["tool"], {}), lines[i]
        if lines[i]["tool"] == "run_tests":
            after = lines[i + 1] if i + 1 < len(lines) else {}
            assert after.get("tool") == "execute", after
            assert after["arg"].startswith("/"), after


@pytest.mark.parametrize("name", sorted(AUDIT_EXAMPLES))
def test_every_audit_example_line_is_a_decision_the_engine_makes(tmp_path, name):
    """OPEN-145: `09-permissions.md` showed `/etc/hosts` refused by the floor,
    which since CR-B4 is the project's own `etc/hosts`, allowed. Each line is
    put to the real engine, with shell opted in and not -- the log is appended
    across runs, so one file holds both -- and a human's answer is an `ask`
    that `AuditLog` records as `prompt`."""
    for entry in _audit_example(*AUDIT_EXAMPLES[name]):
        tool, arg = entry["tool"], entry["arg"]
        args = {ARG_KEYS[tool]: arg} if tool in ARG_KEYS and arg is not None else {}
        decided = set()
        for shell in (False, True):
            engine = PermissionEngine(
                mode=entry["mode"],
                allow=(),
                deny=(),
                floor_disable=(),
                project_root=tmp_path,
                shell_in_auto=shell,
            )
            decision = engine.decide(tool, args)
            decided.add((decision.effect, decision.rule, decision.source))
        if entry["decision"] in ("approve", "reject"):
            assert entry["source"] == "prompt", entry
            assert ("ask", entry["rule"], "mode-default") in decided, (entry, decided)
        else:
            assert (entry["decision"], entry["rule"], entry["source"]) in decided, (entry, decided)


def _recordable_sources() -> set[str]:
    """Every `source` a `Decision` is built with in `permissions/`, read from the
    code -- less `control-plane`, which is always an allow of a silent tool, so
    never recorded -- plus `prompt`, which `AuditLog` writes for a human's answer."""
    found: set[str] = set()
    for module in Path(permissions_package.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) in {"Decision", "_final"}
                and len(node.args) == 3
                and isinstance(node.args[2], ast.Constant)
                and isinstance(node.args[2].value, str)
            ):
                found.add(node.args[2].value)
    assert {"wrapped-execute", "floor", "mode-default"} <= found, found  # the scan sees the engine
    return (found - {"control-plane"}) | {"prompt"}


def test_the_permissions_page_names_every_source_the_log_records():
    """OPEN-145: the list omitted `wrapped-execute`, `auto-mcp` and `fail-closed`."""
    section = _audit_section(PERMISSIONS, "## The audit log")
    paragraph = section.split("The `source` field says", 1)[1].split("\n\n", 1)[0]
    listed = set(re.findall(r"`([a-z-]+)`", paragraph))
    assert _recordable_sources() <= listed, sorted(_recordable_sources() - listed)
