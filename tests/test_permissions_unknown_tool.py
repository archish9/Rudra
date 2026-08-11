"""A1.53 — a tool decided `ask` with no interrupt entry must not run silently.

`decide` falls through to the mode default for any name outside its four
sets, `build_interrupt_on` registers only MUTATING_TOOLS, and the middleware
fell through to the handler for any effect that was not `deny`. So `ask`
with no interrupt entry was neither a prompt nor a denial: the call ran, and
`AuditLog.record` was never reached.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from rudra.permissions.audit import AuditLog
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import (
    READ_ONLY_TOOLS,
    WRAPPED_EXECUTE_TOOLS,
    PermissionEngine,
)


class _Request:
    def __init__(self, name: str, args: dict) -> None:
        self.tool_call = {"name": name, "args": args, "id": "call-1"}


def _engine(tmp_path: Path, mode: str = "ask") -> PermissionEngine:
    return PermissionEngine(mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path)


def _middleware(tmp_path: Path, interrupt_tools: frozenset[str]) -> RudraPermissionMiddleware:
    return RudraPermissionMiddleware(
        _engine(tmp_path),
        AuditLog(tmp_path / "audit.jsonl"),
        "ask",
        interrupt_tools=interrupt_tools,
    )


def test_unregistered_tool_decided_ask_is_denied_not_run(tmp_path: Path):
    middleware = _middleware(tmp_path, frozenset({"write_file"}))
    called = False

    def handler(request):
        nonlocal called
        called = True
        return "ran"

    result = middleware.wrap_tool_call(_Request("mystery_tool", {}), handler)

    assert called is False, "the handler must never run for an unregistered ask"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "mystery_tool" in result.content


def test_unregistered_denial_is_audited(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    middleware = RudraPermissionMiddleware(
        _engine(tmp_path), AuditLog(audit_path), "ask", interrupt_tools=frozenset()
    )
    middleware.wrap_tool_call(_Request("mystery_tool", {}), lambda request: "ran")

    assert audit_path.exists(), "a silent denial is the defect, not the fix"
    assert "mystery_tool" in audit_path.read_text(encoding="utf-8")


def test_registered_ask_tool_still_falls_through_to_the_interrupt(tmp_path: Path):
    middleware = _middleware(tmp_path, frozenset({"write_file"}))
    result = middleware.wrap_tool_call(
        _Request("write_file", {"file_path": "a.py"}), lambda request: "ran"
    )
    assert result == "ran", "interrupt_on owns the ask path for registered tools"


def test_read_plan_is_a_read_and_never_prompts(tmp_path: Path):
    assert "read_plan" in READ_ONLY_TOOLS
    assert _engine(tmp_path).decide("read_plan", {}).effect == "allow"


def test_read_plan_runs_without_an_interrupt_entry(tmp_path: Path):
    """The instance A1.53 was reachable through today."""
    middleware = _middleware(tmp_path, frozenset({"write_file"}))
    result = middleware.wrap_tool_call(_Request("read_plan", {}), lambda request: "the plan")
    assert result == "the plan"


@pytest.mark.parametrize("tool", sorted(WRAPPED_EXECUTE_TOOLS))
def test_wrapped_execute_tools_are_allowed_at_the_middleware(tmp_path: Path, tool: str):
    # Their inner run_gated makes the real execute decision; deciding them
    # again here would prompt twice for one command.
    decision = _engine(tmp_path).decide(tool, {})
    assert decision.effect == "allow"
    assert decision.source == "wrapped-execute"


def test_the_gate_built_for_a_run_knows_its_own_interrupt_names(tmp_path: Path):
    """build_gate must hand the middleware the keys it actually registered."""
    from rudra.config.loader import build_config
    from rudra.permissions import build_gate

    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.middleware.interrupt_tools == frozenset(gate.interrupt_on)
    assert gate.middleware.interrupt_tools, "an empty set would deny every ask"
