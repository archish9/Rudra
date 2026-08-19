"""MCP calls go through the one gate, or they do not run.

Before Step 13 an MCP tool name was in no set: `ask` fell through to
mode-default `ask`, hit no interrupt entry, and the middleware refused it
(rules.py:276, middleware.py:97-110); `auto` allowed it ungated
(rules.py:273). This file pins the replacement.
"""

from pathlib import Path

from rudra.permissions.diff import render
from rudra.permissions.interrupts import build_interrupt_on
from rudra.permissions.rules import (
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    PermissionEngine,
    gated_arg,
)


def engine(tmp_path: Path, mode: str = "ask", **kwargs) -> PermissionEngine:
    return PermissionEngine(
        mode=mode,
        allow=kwargs.pop("allow", ()),
        deny=kwargs.pop("deny", ()),
        floor_disable=(),
        project_root=tmp_path,
        **kwargs,
    )


def test_the_three_names_are_registered():
    assert "call_mcp_tool" in MUTATING_TOOLS
    assert {"list_mcp_tools", "describe_mcp_tool"} <= READ_ONLY_TOOLS


def test_the_gated_argument_is_the_tool_id():
    assert gated_arg("call_mcp_tool", {"tool_id": "kala__verify"}) == "kala__verify"


def test_ask_mode_asks_and_the_prompt_exists(tmp_path):
    decision = engine(tmp_path).decide("call_mcp_tool", {"tool_id": "kala__verify"})
    assert decision.effect == "ask"
    assert "call_mcp_tool" in build_interrupt_on(engine(tmp_path))


def test_listing_is_never_gated(tmp_path):
    assert engine(tmp_path).decide("list_mcp_tools", {}).effect == "allow"
    described = engine(tmp_path).decide("describe_mcp_tool", {"tool_id": "kala__verify"})
    assert described.effect == "allow"


def test_auto_denies_mcp_unless_opted_in(tmp_path):
    decision = engine(tmp_path, "auto").decide("call_mcp_tool", {"tool_id": "kala__verify"})
    assert decision.effect == "deny"
    assert decision.rule == "<auto:mcp-not-opted-in>"
    assert decision.source == "auto-mcp"


def test_auto_allows_when_opted_in(tmp_path):
    opted = engine(tmp_path, "auto", mcp_in_auto=True)
    assert opted.decide("call_mcp_tool", {"tool_id": "kala__verify"}).effect == "allow"


def test_naming_one_id_in_allow_is_consent_for_it_under_auto(tmp_path):
    named = engine(tmp_path, "auto", allow=("call_mcp_tool:kala__system_status",))
    assert named.decide("call_mcp_tool", {"tool_id": "kala__system_status"}).effect == "allow"
    assert named.decide("call_mcp_tool", {"tool_id": "kala__verify"}).effect == "deny"


def test_deny_patterns_match_ids(tmp_path):
    guarded = engine(tmp_path, deny=("call_mcp_tool:*__system_bootstrap",))
    assert guarded.decide("call_mcp_tool", {"tool_id": "kala__system_bootstrap"}).effect == "deny"
    assert guarded.decide("call_mcp_tool", {"tool_id": "kala__verify"}).effect == "ask"


def test_plan_mode_denies(tmp_path):
    assert engine(tmp_path, "plan").decide("call_mcp_tool", {"tool_id": "k__t"}).effect == "deny"


def test_an_id_is_never_resolved_as_a_path(tmp_path):
    # kala__verify is not a filename; resolving it would produce a
    # project-relative spelling that rules could accidentally match.
    decision = engine(tmp_path, deny=("call_mcp_tool:kala__verify",)).decide(
        "call_mcp_tool", {"tool_id": "kala__verify"}
    )
    assert decision.effect == "deny"


def test_the_approval_preview_shows_server_tool_and_args(tmp_path):
    preview = render(
        "call_mcp_tool", {"tool_id": "kala__verify", "arguments": {"dir": "/p"}}, tmp_path
    )
    assert "kala" in preview.header and "verify" in preview.header
    assert "dir" in preview.body
