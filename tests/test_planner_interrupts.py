"""The planner's approval map, narrowed to tools the planner holds (OPEN-101).

`build_interrupt_on` emits one entry per MUTATING_TOOLS name and knows
nothing about any agent (permissions/interrupts.py). deepagents'
HumanInTheLoopMiddleware matches on the tool-call NAME in the assistant
message, which happens BEFORE the tool node discovers the name is not
registered -- so run d8f742805b9b's planner, which holds no `write_file`,
raised a full approval panel for one, took the user's answer, recorded a
session-wide `approve_all` grant, and only then failed with `write_file is
not a valid tool`. That grant then authorised a `task` call ten minutes
later.

This is OPEN-15 on the one stack OPEN-15's fix did not cover: the
diagnosis, the argument and a working implementation were all already in
`subagents/build.py::_interrupt_on_for`. What was missing was anything
making the second `create_deep_agent` call site use it -- which is what
`test_no_create_deep_agent_call_site_passes_an_unnarrowed_map` is for.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from rich.console import Console

from rudra.agent.planner_agent import PLANNER_FS_TOOLS, STAGES, create_planner_agent
from rudra.config.loader import build_config, reset_config
from rudra.permissions import build_gate
from rudra.permissions.interrupts import narrow_interrupt_on
from rudra.permissions.rules import MUTATING_TOOLS

SRC = Path(__file__).resolve().parents[1] / "src" / "rudra"


def _capture_planner(monkeypatch, tmp_path: Path, stage: str) -> dict:
    captured: dict = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr("rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent)
    reset_config()
    try:
        gate = build_gate(build_config(tmp_path), tmp_path)
        create_planner_agent(
            task="write a single HTML file for iphone 15",
            project_path=tmp_path,
            filesystem_backend=object(),
            checkpointer=None,
            console=Console(quiet=True),
            gate=gate,
            stage=stage,
        )
    finally:
        reset_config()
    captured["_gate"] = gate
    return captured


@pytest.mark.parametrize("stage", STAGES)
def test_planner_interrupt_map_holds_no_tool_the_planner_lacks(
    monkeypatch, tmp_path: Path, stage: str
):
    """The regression pin. A panel for an unregistered name is OPEN-101."""
    captured = _capture_planner(monkeypatch, tmp_path, stage)
    granted = set(PLANNER_FS_TOOLS) | {tool.name for tool in captured["tools"]}
    assert set(captured["interrupt_on"]) <= granted


@pytest.mark.parametrize("stage", STAGES)
def test_no_planner_stage_can_raise_a_panel_for_a_mutating_tool(
    monkeypatch, tmp_path: Path, stage: str
):
    """Today's planner holds none of MUTATING_TOOLS, so the map is empty.

    Stated separately from the rule above because it is a fact about the
    planner rather than about the narrowing: if a stage is ever granted a
    mutating tool, this test is the one that must change, and the rule
    above is the one that must not.
    """
    captured = _capture_planner(monkeypatch, tmp_path, stage)
    assert set(captured["interrupt_on"]) & set(MUTATING_TOOLS) == set()
    assert captured["interrupt_on"] == {}


@pytest.mark.parametrize("stage", STAGES)
def test_a_planner_stage_without_a_gate_still_passes_none(monkeypatch, tmp_path: Path, stage: str):
    """`gate=None` must stay None, not become an empty map (A1.62).

    An empty dict is a HumanInTheLoopMiddleware with no entries; None is no
    middleware at all, and the checkpointer requirement rides on it.
    """
    captured: dict = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr("rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent)
    create_planner_agent(
        task="t",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
        stage=stage,
    )
    assert captured["interrupt_on"] is None


def test_narrowing_keeps_the_entries_for_tools_that_are_held():
    """The inverse of the pin: a narrowing that returns {} is a security bug.

    Asserted on the shared function directly, and again through the coder
    below, because on today's planner the correct map is empty for all
    three stages -- so no planner assertion can catch over-filtering.
    """
    full = {name: {"allowed_decisions": ["approve", "reject"]} for name in sorted(MUTATING_TOOLS)}
    narrowed = narrow_interrupt_on(full, {"ls", "read_file", "write_file", "execute"})
    assert set(narrowed) == {"write_file", "execute"}
    assert narrowed["write_file"] is full["write_file"]


def test_narrow_interrupt_on_is_the_one_implementation():
    """`_interrupt_on_for` must CALL it, not restate it (CLAUDE.md §3).

    Two copies of a filtering rule is OPEN-64's shape: three copies of the
    build-output predicate, one of which had drifted, each carrying a
    docstring asserting it could not.
    """
    tree = ast.parse((SRC / "subagents" / "build.py").read_text())
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_interrupt_on_for"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "narrow_interrupt_on" in called


def _interrupt_on_arguments() -> list[tuple[str, ast.expr]]:
    """Every `interrupt_on=` handed to `create_deep_agent` under src/."""
    found: list[tuple[str, ast.expr]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "create_deep_agent":
                continue
            for kw in node.keywords:
                if kw.arg == "interrupt_on":
                    found.append((str(path.relative_to(SRC)), kw.value))
    return found


def test_no_create_deep_agent_call_site_passes_an_unnarrowed_map():
    """The pin against the THIRD stack, whenever it appears (OPEN-101 §10).

    Every ingredient of this fix was in the repository on the day the run
    happened; what was missing was anything making a second call site use
    it. An `interrupt_on=` whose expression is a bare `<something>.interrupt_on`
    is the gate's whole map, which is the defect.
    """
    sites = _interrupt_on_arguments()
    assert len(sites) == 2, f"a new create_deep_agent call site appeared: {sites}"
    for where, value in sites:
        for node in ast.walk(value):
            assert not (
                isinstance(node, ast.Attribute) and node.attr == "interrupt_on"
            ) or _is_narrowed(value), f"{where} passes the gate's whole interrupt map"


def _is_narrowed(value: ast.expr) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"narrow_interrupt_on", "_interrupt_on_for"}
        for node in ast.walk(value)
    )
