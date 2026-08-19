"""Who may compact their own context, and who is simply not given the tool.

S12.8: the coder and tester retry and read pytest transcripts. Planner
stages are short-lived and the reviewer runs once. On the S9b.3 precedent,
an agent that should not compact does not receive the tool rather than
being told not to use it.
"""

from __future__ import annotations

import pytest

from rudra.subagents.registry import REGISTRY


def test_only_the_coder_and_tester_want_compaction():
    wants = {name for name, spec in REGISTRY.items() if spec.wants_compaction}
    assert wants == {"coder", "tester"}


def test_the_compaction_tool_is_never_gated():
    """Without this the gate DENIES it -- A1.75's shape exactly.

    An unregistered tool decided `ask` is denied, not run
    (tests/test_permissions_unknown_tool.py). So a compaction tool outside
    CONTROL_PLANE_TOOLS would be registered, offered to the model, and
    refused every time.
    """
    from rudra.permissions.rules import CONTROL_PLANE_TOOLS

    assert "compact_conversation" in CONTROL_PLANE_TOOLS


def test_the_gate_allows_it_as_control_plane(tmp_path):
    from rudra.permissions.rules import PermissionEngine

    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    decision = engine.decide("compact_conversation", {})

    assert decision.effect == "allow"
    assert decision.source == "control-plane"


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch):
    """The compaction middleware type-checks its model, so the fake is real.

    FakeModel lives in test_subagents_build and subclasses BaseChatModel
    for exactly this reason (summarization.py:1663).
    """
    import rudra.subagents.build as build
    from tests.test_subagents_build import FakeModel

    monkeypatch.setattr(build, "build_model", lambda role, cfg=None: FakeModel(role=role))


def test_only_the_opted_in_subagents_get_the_middleware(tmp_path):
    """The registry flag is only meaningful if the stack honours it."""
    from rich.console import Console

    from rudra.subagents.build import _middleware_for, _model_for
    from tests.test_subagents_build import (
        FakeCfg,
        FakeCompat,
        FakeContext,
        FakeGate,
        FakeMiddleware,
        FakeModelConfig,
        FakeTools,
    )

    context = FakeContext(
        project_path=tmp_path,
        backend=object(),
        gate=FakeGate(middleware=FakeMiddleware(), interrupt_on={}),
        console=Console(quiet=True),
        cfg=FakeCfg(
            compat=FakeCompat(),
            tools=FakeTools(),
            models={"default": FakeModelConfig(context_tokens=131072)},
        ),
    )

    got = {}
    for name in sorted(REGISTRY):
        spec = REGISTRY[name]
        names = [
            type(m).__name__ for m in _middleware_for(spec, context, _model_for(spec, context.cfg))
        ]
        got[name] = "SummarizationToolMiddleware" in names

    assert got == {
        "coder": True,
        "tester": True,
        "reviewer": False,
        "general-purpose": False,
    }
