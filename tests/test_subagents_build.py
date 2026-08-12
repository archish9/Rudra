"""One assembly, two consumers -- and the gate on every path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from rudra.subagents.build import _middleware_for, _tools_for, to_subagent_spec
from rudra.subagents.registry import REGISTRY


class FakeMiddleware:
    name = "RudraPermissionMiddleware"


@dataclass
class FakeGate:
    middleware: Any
    interrupt_on: dict


@dataclass
class FakeCompat:
    task_anchor: bool = False
    sandbox_paths: bool = False


@dataclass
class FakeTools:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict


@dataclass
class FakeContext:
    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = "test"


class FakeModel:
    """Stands in for a BaseChatModel.

    These tests are about assembly -- which tools and which middleware a
    spec produces. Resolving a real model is llm/factory.py's job and is
    covered by tests/test_llm_factory.py; building one here would only
    require a full Config to test something else.
    """

    def __init__(self, role: str) -> None:
        self.role = role


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    import rudra.subagents.build as build

    monkeypatch.setattr(build, "build_model", lambda role, cfg=None: FakeModel(role))


@pytest.fixture
def context(tmp_path):
    return FakeContext(
        project_path=tmp_path,
        backend=object(),
        gate=FakeGate(middleware=FakeMiddleware(), interrupt_on={"write_file": True}),
        console=Console(quiet=True),
        cfg=FakeCfg(compat=FakeCompat(), tools=FakeTools(), models={}),
    )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_gate(name, context):
    # The invariant. deepagents does not propagate the parent's middleware=
    # to subagents (graph.py:666-703), so a spec without this is gated for
    # approvals and not for denials.
    middleware = _middleware_for(REGISTRY[name], context)
    assert any(m is context.gate.middleware for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_gate_precedes_the_argument_rewriter(name, context):
    # A denied call must stop before anything rewrites its arguments
    # (planner_agent.py:126-128).
    middleware = _middleware_for(REGISTRY[name], context)
    names = [type(m).__name__ for m in middleware]
    assert names.index("FakeMiddleware") < names.index("FixWriteParamsMiddleware")


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_filesystem_middleware_is_scoped_to_the_spec(name, context):
    middleware = _middleware_for(REGISTRY[name], context)
    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1


def test_a_gateless_context_still_builds(context):
    # Only a caller constructing this by hand can produce gate=None; it must
    # not crash, matching how RudraAgent tolerates it (main_agent.py:91-93).
    context.gate = None
    middleware = _middleware_for(REGISTRY["coder"], context)
    assert middleware


def test_rudra_tools_resolve_by_name(context):
    tools = _tools_for(REGISTRY["reviewer"], context)
    assert [t.name for t in tools] == ["git_diff"]

    tools = _tools_for(REGISTRY["tester"], context)
    assert [t.name for t in tools] == ["run_tests"]


def test_a_spec_with_no_rudra_tools_gets_none(context):
    assert _tools_for(REGISTRY["coder"], context) == []


def test_an_unknown_rudra_tool_is_a_construction_error(context):
    broken = replace(REGISTRY["coder"], rudra_tools=("nonexistent_tool",))
    with pytest.raises(ValueError, match="nonexistent_tool"):
        _tools_for(broken, context)


def test_an_unknown_fs_tool_is_a_construction_error(context):
    broken = replace(REGISTRY["coder"], fs_tools=("read_file", "teleport"))
    with pytest.raises(ValueError, match="teleport"):
        _middleware_for(broken, context)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_has_every_required_key(name, context):
    spec = to_subagent_spec(REGISTRY[name], context)
    # create_sub_agent raises without model and tools (subagents.py:358-363).
    for key in ("name", "description", "system_prompt", "model", "tools", "middleware"):
        assert key in spec


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_inherits_the_gate_interrupts(name, context):
    spec = to_subagent_spec(REGISTRY[name], context)
    assert spec["interrupt_on"] == context.gate.interrupt_on


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_middleware(name, context):
    # The parity guard: if the two consumers ever disagree about what a
    # subagent is, the delegating path silently loses the gate.
    direct = [type(m).__name__ for m in _middleware_for(REGISTRY[name], context)]
    delegated = [type(m).__name__ for m in to_subagent_spec(REGISTRY[name], context)["middleware"]]
    assert direct == delegated


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_tools(name, context):
    direct = [t.name for t in _tools_for(REGISTRY[name], context)]
    delegated = [t.name for t in to_subagent_spec(REGISTRY[name], context)["tools"]]
    assert direct == delegated


def test_the_reviewers_compiled_graph_has_no_write_tools(tmp_path, monkeypatch):
    # The spec is the input; the compiled graph is the claim. Assert the
    # claim (spec S9b.3).
    from deepagents.backends.filesystem import FilesystemBackend

    import rudra.subagents.build as build
    from rudra.config import build_config
    from rudra.llm import build_model
    from rudra.permissions import build_gate
    from rudra.subagents.build import build_agent

    # Undo the autouse fake: compiling a real graph needs a real chat model.
    # Constructing one makes no network call (llm/factory.py:67).
    monkeypatch.setattr(build, "build_model", build_model)
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
        encoding="utf-8",
    )
    cfg = build_config(tmp_path)

    real_context = FakeContext(
        project_path=tmp_path,
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        gate=build_gate(cfg, tmp_path),
        console=Console(quiet=True),
        cfg=cfg,
    )

    agent = build_agent(REGISTRY["reviewer"], real_context)
    registered = set(agent.nodes["tools"].bound.tools_by_name)

    assert registered, "sanity: the compiled graph must register some tools"
    forbidden = {"write_file", "edit_file", "delete", "execute"}
    assert not (forbidden & registered), f"reviewer must not have {forbidden & registered}"
    assert "read_file" in registered
    assert "git_diff" in registered
