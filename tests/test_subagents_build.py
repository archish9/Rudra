"""One assembly, two consumers -- and the gate on every path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from rich.console import Console

from rudra.subagents.build import (
    _middleware_for,
    _model_for,
    _tools_for,
    to_subagent_spec,
)
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
class FakeModelConfig:
    context_tokens: int | None = 131072


@dataclass
class FakeCfg:
    compat: FakeCompat
    tools: FakeTools
    models: dict

    def model_for(self, role: str):
        """Mirrors Config.model_for (config/loader.py:293), fallback and all."""
        return self.models.get(role, self.models.get("default", FakeModelConfig()))


@dataclass
class FakeContext:
    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = "test"
    facts: Any = None
    # Mirrors SubagentContext (Step 11b). A fake that drifts from the real
    # dataclass is how tests stay green against code that would break.
    skills_sources: tuple[str, ...] | None = None
    usage: Any = None
    trace: Any = None


class FakeModel(BaseChatModel):
    """Stands in for a BaseChatModel -- and actually is one.

    These tests are about assembly -- which tools and which middleware a
    spec produces. Resolving a real model is llm/factory.py's job and is
    covered by tests/test_llm_factory.py; building one here would only
    require a full Config to test something else.

    It subclasses BaseChatModel rather than merely resembling one because
    Step 12b's compaction middleware type-checks its model
    (deepagents/middleware/summarization.py:1663). A duck-typed stand-in
    passed every assertion here right up until something looked.
    """

    role: str = ""

    @property
    def _llm_type(self) -> str:
        return "fake-assembly-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    import rudra.subagents.build as build

    monkeypatch.setattr(build, "build_model", lambda role, cfg=None: FakeModel(role=role))


@pytest.fixture
def context(tmp_path):
    return FakeContext(
        project_path=tmp_path,
        backend=object(),
        gate=FakeGate(middleware=FakeMiddleware(), interrupt_on={"write_file": True}),
        console=Console(quiet=True),
        cfg=FakeCfg(
            compat=FakeCompat(),
            tools=FakeTools(),
            models={"default": FakeModelConfig(context_tokens=131072)},
        ),
    )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_gate(name, context):
    # The invariant. deepagents does not propagate the parent's middleware=
    # to subagents (graph.py:666-703), so a spec without this is gated for
    # approvals and not for denials.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    assert any(m is context.gate.middleware for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_gate_precedes_the_argument_rewriter(name, context):
    # A denied call must stop before anything rewrites its arguments
    # (planner_agent.py:126-128).
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    names = [type(m).__name__ for m in middleware]
    assert names.index("FakeMiddleware") < names.index("FixWriteParamsMiddleware")


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_execute_guard(name, context):
    # OPEN-25. Unconditional rather than restricted to specs granting
    # `execute`: the middleware is a no-op without one, and a spec that gains
    # shell later must not have to remember this.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    assert any(type(m).__name__ == "ExecuteGuardMiddleware" for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_delegation_guard(name, context):
    # OPEN-26. `task` is registered for every agent Rudra builds, because
    # _nested_subagents always supplies the gated general-purpose spec.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    assert any(type(m).__name__ == "DelegationGuardMiddleware" for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_the_model_retry(name, context):
    # OPEN-41. `_stream_with_retry` protects one model call per invocation
    # -- the first -- and a subagent makes dozens. Every spec, because a
    # provider degrades for all of them at once.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    assert any(type(m).__name__ == "ModelRetryMiddleware" for m in middleware)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_model_retry_sits_outside_the_usage_accounting(name, context):
    # Each ATTEMPT is one recorded call and the backoff sleep is nobody's.
    # Inside the accounting, a twice-retried call reads as one 40-second
    # call that was mostly asyncio.sleep -- the number OPEN-40 is about.
    import dataclasses

    counted = dataclasses.replace(context, usage=object())
    middleware = _middleware_for(REGISTRY[name], counted, _model_for(REGISTRY[name], counted.cfg))
    names = [type(m).__name__ for m in middleware]
    assert names.index("ModelRetryMiddleware") < names.index("UsageMiddleware")


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_retry_can_report_what_it_did(name, context):
    # OPEN-45. Registered is not the same as wired: this middleware shipped
    # on every spec holding neither a sink nor an accumulator, so a run
    # absorbing a third of its requests was indistinguishable from a slow
    # model -- and those have opposite fixes.
    import dataclasses

    sink = object()
    usage = object()
    wired = dataclasses.replace(context, trace=sink, usage=usage)
    middleware = _middleware_for(REGISTRY[name], wired, _model_for(REGISTRY[name], wired.cfg))
    retry = next(m for m in middleware if type(m).__name__ == "ModelRetryMiddleware")

    assert retry.trace is sink
    assert retry.usage is usage


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_subagent_retry_still_builds_with_no_run_around_it(name, context):
    # The context fixture carries neither, as every 9b-era stand-in does.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    retry = next(m for m in middleware if type(m).__name__ == "ModelRetryMiddleware")

    assert retry.trace is None
    assert retry.usage is None


def test_no_shipped_subagent_may_delegate(context):
    """The coder writes one file for one task; the tester writes and runs
    tests; the reviewer reads a diff; and general-purpose IS the delegate --
    granting it `task` would permit recursion. Nothing needs to hand work off,
    and `to_subagent_spec`'s delegating parent has never been built
    (build.py:353-355)."""
    for name, spec in REGISTRY.items():
        assert spec.can_delegate is False, name


def test_the_gated_general_purpose_spec_is_still_passed(context):
    """OPEN-14 regression guard, and the reason OPEN-26 is solved with a
    middleware rather than `subagents=None`. Dropping the spec would remove
    `task` AND restore deepagents' ungated general-purpose, whose auto-add
    skips only on a spec literally named this (graph.py:750-751)."""
    from rudra.subagents.build import _nested_subagents

    assert [spec["name"] for spec in _nested_subagents(context)] == ["general-purpose"]


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_filesystem_middleware_is_scoped_to_the_spec(name, context):
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1


def test_a_gateless_context_still_builds(context):
    # Only a caller constructing this by hand can produce gate=None; it must
    # not crash, matching how RudraAgent tolerates it (main_agent.py:91-93).
    context.gate = None
    middleware = _middleware_for(
        REGISTRY["coder"], context, _model_for(REGISTRY["coder"], context.cfg)
    )
    assert middleware


def test_rudra_tools_resolve_by_name(context):
    tools = _tools_for(REGISTRY["reviewer"], context)
    assert [t.name for t in tools] == ["git_diff"]

    # Declaration order, not alphabetical -- the tester declares run_tests
    # first and gained the two memory tools in Step 14b.
    tools = _tools_for(REGISTRY["tester"], context)
    assert [t.name for t in tools] == ["run_tests", "remember", "search_memory"]


def test_a_spec_with_no_rudra_tools_gets_none(context):
    """Built rather than borrowed from the registry.

    This used to point at the coder, which happened to declare none until
    Step 14b gave it the memory tools -- so the test broke on a change that
    had nothing to do with the property it checks. A spec constructed here
    cannot drift out from under it.
    """
    bare = replace(REGISTRY["coder"], rudra_tools=())
    assert _tools_for(bare, context) == []


def test_an_unknown_rudra_tool_is_a_construction_error(context):
    broken = replace(REGISTRY["coder"], rudra_tools=("nonexistent_tool",))
    with pytest.raises(ValueError, match="nonexistent_tool"):
        _tools_for(broken, context)


def test_an_unknown_fs_tool_is_a_construction_error(context):
    broken = replace(REGISTRY["coder"], fs_tools=("read_file", "teleport"))
    with pytest.raises(ValueError, match="teleport"):
        _middleware_for(broken, context, _model_for(broken, context.cfg))


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_has_every_required_key(name, context):
    spec = to_subagent_spec(REGISTRY[name], context)
    # create_sub_agent raises without model and tools (subagents.py:358-363).
    for key in ("name", "description", "system_prompt", "model", "tools", "middleware"):
        assert key in spec


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_delegation_spec_inherits_the_gate_interrupts(name, context):
    """Inherits, narrowed to what the spec can actually call (OPEN-15).

    It used to be the gate's whole map on every spec, which is how the
    coder -- with no `execute` -- raised an approval panel for
    `execute pwd` and then failed with "execute is not a valid tool".
    """
    spec = to_subagent_spec(REGISTRY[name], context)
    assert set(spec["interrupt_on"]) <= set(context.gate.interrupt_on)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_subagent_is_gated_on_a_tool_it_does_not_hold(name, context):
    """OPEN-15: an entry for an unregistered tool is a prompt for a phantom."""
    spec = REGISTRY[name]
    built = to_subagent_spec(spec, context)
    granted = set(spec.fs_tools) | {tool.name for tool in built["tools"]}

    assert set(built["interrupt_on"]) <= granted


def test_a_writing_subagent_keeps_its_interrupts(context):
    """The filter must not become "no subagent is gated"."""
    assert to_subagent_spec(REGISTRY["coder"], context)["interrupt_on"] == {"write_file": True}


def test_a_read_only_subagent_has_nothing_to_interrupt_on(context):
    assert to_subagent_spec(REGISTRY["reviewer"], context)["interrupt_on"] == {}


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_middleware(name, context):
    # The parity guard: if the two consumers ever disagree about what a
    # subagent is, the delegating path silently loses the gate.
    direct = [
        type(m).__name__
        for m in _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    ]
    delegated = [type(m).__name__ for m in to_subagent_spec(REGISTRY[name], context)["middleware"]]
    assert direct == delegated


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_tools(name, context):
    direct = [t.name for t in _tools_for(REGISTRY[name], context)]
    delegated = [t.name for t in to_subagent_spec(REGISTRY[name], context)["tools"]]
    assert direct == delegated


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_supplies_its_own_general_purpose(name, context, monkeypatch):
    """OPEN-14: upstream auto-adds an ungated one when we supply none.

    `create_deep_agent` adds its own `general-purpose` subagent unless a
    spec of that name is passed (graph.py:745-751), built with a fresh
    `FilesystemMiddleware` carrying the unrestricted tool set and inheriting
    only middleware whose `.name` matches a default slot -- which Rudra's
    gate does not (graph.py:752-778). Every Rudra subagent holds `task`, so
    that nested agent was reachable from the coder with write and shell and
    no deny middleware.

    Asserted at the `create_deep_agent` boundary because that is where the
    decision is made; the nested graph is upstream's to compile.
    """
    import rudra.subagents.build as build
    from rudra.subagents.build import build_agent

    captured: dict[str, Any] = {}

    def record(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(build, "create_deep_agent", record)
    build_agent(REGISTRY[name], context)

    specs = captured["subagents"]
    assert [s["name"] for s in specs] == ["general-purpose"], (
        "a spec named general-purpose is what suppresses upstream's auto-add"
    )
    assert context.gate.middleware in specs[0]["middleware"], (
        "the nested agent must carry the same gate as its parent"
    )
    # Narrowed by OPEN-15 to what the nested spec holds, which for the
    # read-only general-purpose is nothing.
    assert set(specs[0]["interrupt_on"]) <= set(context.gate.interrupt_on)


def test_the_nested_general_purpose_cannot_write(context, monkeypatch):
    """It is the read-only registry spec, not a fresh permissive one."""
    import rudra.subagents.build as build
    from rudra.subagents.build import build_agent

    captured: dict[str, Any] = {}
    monkeypatch.setattr(build, "create_deep_agent", lambda **kw: captured.update(kw) or object())
    build_agent(REGISTRY["coder"], context)

    nested = captured["subagents"][0]
    assert nested["system_prompt"].startswith(REGISTRY["general-purpose"].system_prompt[:40])
    granted = {tool.name for tool in nested["tools"]}
    assert not ({"write_file", "edit_file", "delete", "execute"} & granted)


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


def test_build_agent_always_supplies_a_checkpointer(tmp_path, monkeypatch):
    # A1.62: interrupt_on becomes a HumanInTheLoopMiddleware, which needs a
    # checkpointer (subagents.py:70). Without one every invocation ended in
    # "No checkpointer set" AFTER the subagent had done its work. The unit
    # tests missed it because they patch build_agent away; only the live
    # acceptance run caught it.
    from deepagents.backends.filesystem import FilesystemBackend

    import rudra.subagents.build as build
    from rudra.config import build_config
    from rudra.llm import build_model
    from rudra.permissions import build_gate
    from rudra.subagents.build import build_agent

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

    context = FakeContext(
        project_path=tmp_path,
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        gate=build_gate(cfg, tmp_path),
        console=Console(quiet=True),
        cfg=cfg,
        checkpointer=None,
    )

    agent = build_agent(REGISTRY["reviewer"], context)
    assert agent.checkpointer is not None, (
        "interrupt_on without a checkpointer fails at the end of every run (A1.62)"
    )


def test_an_explicit_checkpointer_wins(tmp_path, monkeypatch):
    from deepagents.backends.filesystem import FilesystemBackend
    from langgraph.checkpoint.memory import InMemorySaver

    import rudra.subagents.build as build
    from rudra.config import build_config
    from rudra.llm import build_model
    from rudra.permissions import build_gate
    from rudra.subagents.build import build_agent

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
    mine = InMemorySaver()

    context = FakeContext(
        project_path=tmp_path,
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        gate=build_gate(cfg, tmp_path),
        console=Console(quiet=True),
        cfg=cfg,
        checkpointer=mine,
    )

    assert build_agent(REGISTRY["reviewer"], context).checkpointer is mine


# --- Step 10a: the facts block reaches every subagent ---


def _stocked_store():
    from rudra.facts import FactStore

    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    return store


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_the_facts_block_is_appended_to_every_subagent_prompt(name, context):
    """The coder has never seen the project facts before Step 10a."""
    from rudra.subagents.build import _prompt_for

    context.facts = _stocked_store()
    prompt = _prompt_for(REGISTRY[name], context)

    assert REGISTRY[name].system_prompt in prompt
    assert "## PROJECT FACTS" in prompt
    assert "Rust" in prompt


def test_a_prompt_is_unchanged_when_there_are_no_facts(context):
    # Asserted as "no facts block", not as equality with the spec prompt:
    # OPEN-39 gives the coder a PROJECT FILES block unconditionally, so
    # equality would now be a claim about the listing rather than about
    # the facts. The reviewer holds the equality case below.
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for

    context.facts = FactStore()
    prompt = _prompt_for(REGISTRY["coder"], context)
    assert "## PROJECT FACTS" not in prompt
    assert REGISTRY["coder"].system_prompt in prompt
    # A spec that gets no other block still comes back untouched.
    assert _prompt_for(REGISTRY["reviewer"], context) == REGISTRY["reviewer"].system_prompt


def test_a_context_without_facts_still_builds(context):
    """SubagentContext.facts defaults to None; nothing may require it."""
    from rudra.subagents.build import _prompt_for

    assert "## PROJECT FACTS" not in _prompt_for(REGISTRY["coder"], context)
    assert _prompt_for(REGISTRY["reviewer"], context) == REGISTRY["reviewer"].system_prompt


def test_a_fact_recorded_after_construction_reaches_the_next_build(context):
    """build_agent runs per invocation (runner.py:124) -- that is the point."""
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for

    store = FactStore()
    context.facts = store
    assert "clap" not in _prompt_for(REGISTRY["coder"], context)

    store.record("cli_framework", "clap", "the user answered", "asked")
    assert "clap" in _prompt_for(REGISTRY["coder"], context)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_both_paths_agree_on_the_prompt(name, context):
    """The delegating path must not drift from the direct one.

    Same rule _tools_for and _middleware_for already follow: a subagent
    reached through `task` and one invoked directly are one thing.
    """
    from rudra.subagents.build import _prompt_for, to_subagent_spec

    context.facts = _stocked_store()
    spec = REGISTRY[name]
    assert to_subagent_spec(spec, context)["system_prompt"] == _prompt_for(spec, context)


def test_build_agent_passes_the_facts_prompt_to_create_deep_agent(context, monkeypatch):
    """Asserted against the call, not the helper: a helper nobody uses is
    the A1.8 shape -- a fix that exists and is not wired."""
    import rudra.subagents.build as build

    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(build, "create_deep_agent", _fake_create_deep_agent)
    context.facts = _stocked_store()
    build.build_agent(REGISTRY["coder"], context)

    assert "## PROJECT FACTS" in captured["system_prompt"]
    assert "Rust" in captured["system_prompt"]


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_carries_a_derived_evict_limit(name, context):
    """No agent may quietly take deepagents' 20000 default (A1.47).

    13107 == int(131072 * 0.10), written out so this test fails if the
    fraction changes rather than following it.
    """
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1, f"{name} has {len(filesystem)} FilesystemMiddleware"
    assert filesystem[0]._tool_token_limit_before_evict == 13107, name


def test_an_undeclared_window_leaves_the_upstream_default(context):
    """S12.9: None must not become 0 or a guess on the way through."""
    cfg = replace(context.cfg, models={"default": FakeModelConfig(context_tokens=None)})
    spec = REGISTRY[sorted(REGISTRY)[0]]
    ctx = replace(context, cfg=cfg)
    middleware = _middleware_for(spec, ctx, _model_for(spec, ctx.cfg))
    filesystem = [m for m in middleware if type(m).__name__ == "FilesystemMiddleware"][0]
    assert filesystem._tool_token_limit_before_evict == 20000


def test_every_subagent_reports_its_usage(context):
    """One accumulator, fed by every agent (C7.5)."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    ctx = replace(context, usage=usage)

    for name in sorted(REGISTRY):
        middleware = _middleware_for(REGISTRY[name], ctx, _model_for(REGISTRY[name], ctx.cfg))
        recorders = [m for m in middleware if type(m).__name__ == "UsageMiddleware"]
        assert len(recorders) == 1, name
        assert recorders[0].usage is usage, name
        assert recorders[0].role == REGISTRY[name].role, name


def test_no_usage_object_means_no_usage_middleware(context):
    """Every 9b-era test builds a context without one and must still work."""
    middleware = _middleware_for(
        REGISTRY["coder"], context, _model_for(REGISTRY["coder"], context.cfg)
    )
    assert not [m for m in middleware if type(m).__name__ == "UsageMiddleware"]


# --- OPEN-21: the two path universes ------------------------------------


def _specs_that_can_run_commands() -> list[str]:
    """The specs whose prompts must carry the command contract.

    Derived, never listed. The hand-written pair these tests used to loop
    over named the coder, which has never held `execute` -- OPEN-36, where
    twenty lines of shell instructions produced 15 dead `execute` calls in
    one run.
    """
    from rudra.subagents.registry import REGISTRY

    names = [name for name, spec in REGISTRY.items() if "execute" in spec.fs_tools]
    assert names, "vacuous if no shipped spec can run anything"
    return names


def test_every_subagent_that_can_run_commands_is_told_execute_does_not_share_the_root():
    """OPEN-21. The file tools are project-rooted; `execute` is a real shell
    whose "/" is the machine. Nothing said so, and the tester found out the
    hard way: `write_file('/tests')` landed in the project, `rm /tests`
    looked at the host, `mkdir -p /tests` hit the host root, and
    `mkdir -p /tmp/tests` then created a directory OUTSIDE the project.

    Not a containment control -- `permissions/floor.py` explains at length
    why a regex cannot contain a shell, and that decision stands. This is
    the *cause*: the model was not evading anything, it was reasoning
    correctly from a path contract Rudra had only told it half of.
    """
    from rudra.subagents.registry import REGISTRY

    for name in _specs_that_can_run_commands():
        prompt = REGISTRY[name].system_prompt
        assert "do NOT agree about what" in prompt, name
        assert "MACHINE" in prompt, name
        assert "rm /tests" in prompt, name


def test_every_writing_subagent_is_told_directories_are_implicit():
    """OPEN-22's teaching half. The guard in fix_write_params refuses the
    placeholder write; this is what stops the model reaching for it."""
    from rudra.subagents.registry import REGISTRY

    for name in ("coder", "tester"):
        prompt = REGISTRY[name].system_prompt
        assert "Directories are created implicitly" in prompt, name
        assert "no mkdir tool" in prompt, name


def test_every_subagent_that_can_run_commands_is_told_where_they_run():
    """OPEN-25's positive half. Nothing in Rudra said where `execute` runs --
    only what "/" meant to it -- so a model carrying a container-shaped prior
    from training had nothing to correct it, and emitted `cd /app && ...`.
    The upstream tool description asserting the opposite is removed by
    middleware/execute_guard.py; this is the fact that replaces it."""
    from rudra.subagents.registry import REGISTRY

    for name in _specs_that_can_run_commands():
        prompt = REGISTRY[name].system_prompt
        assert "WHERE COMMANDS RUN" in prompt, name
        assert "ALREADY runs in this project's root directory" in prompt, name
        assert "NEVER `cd` before a command" in prompt, name
        assert "not in a container" in prompt.lower(), name
        assert "cd /app &&" in prompt, name


def test_the_tester_is_told_not_to_test_what_does_not_exist_yet():
    """OPEN-23's complement. Measured 2026-08-26 (run `ee29dd3ebf51`): the
    tester for `t1` ("Define the Todo class") wrote `tests/test_cli.py`
    against a CLI that task `t8` had not built. Those tests failed for the
    rest of the run and belonged to no task, blocking six in a row.

    Prompt text alone would be a weak fix -- OPEN-17 is the standing evidence
    -- which is why loop/regressions.py carries the structural half. This
    reduces how often the situation arises; it is not what stops it hurting.
    """
    prompt = REGISTRY["tester"].system_prompt
    assert "does not exist yet" in prompt
    assert "another task" in prompt
    assert "absent" in prompt or "empty" in prompt


def test_the_coder_is_told_the_gate_runs_the_tests(context):
    """OPEN-26's complement. The coder has no `execute` (registry.py:179) and
    went looking for a way to run tests anyway; what it found was `task`.
    delegation_guard.py removes that route -- this removes the motive."""
    prompt = REGISTRY["coder"].system_prompt
    assert "Do NOT try to run the tests" in prompt
    assert "task" in prompt, "the tool it reached for is named, so it is not a mystery"


def test_the_path_contract_has_exactly_one_owner():
    """It lived only in the coder prompt, and the TESTER is the agent that
    actually walked out of the project -- it had no path rules at all
    (OPEN-21). Two copies would drift; one constant cannot."""
    from rudra.subagents.registry import _PATH_RULES, REGISTRY

    for name in ("coder", "tester"):
        assert _PATH_RULES in REGISTRY[name].system_prompt, name
    # The reviewer cannot write, so the contract would be noise for it.
    assert _PATH_RULES not in REGISTRY["reviewer"].system_prompt


# --- OPEN-39: the project listing ------------------------------------------


@pytest.mark.parametrize("name", ["coder", "tester"])
def test_the_writing_subagents_are_told_what_files_exist(name, context):
    """OPEN-39. 158 of run6's 243 tool calls were orientation -- 13 of 20
    invocations opened with `ls`, listing a directory an earlier invocation
    in the same run had already listed. The coder had no other route: the
    system prompt carried the spec, the facts and the memories, and no file
    listing, so `ls` -> `glob` -> `read_file` from scratch was the only way
    to learn what existed."""
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    prompt = _prompt_for(REGISTRY[name], context)

    assert "## PROJECT FILES" in prompt
    assert "app.py" in prompt


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_the_reading_subagents_are_not(name, context):
    """S11b.1 again: the reviewer is handed the changed-file list in its own
    prompt (loop/engine.py), so a whole-project listing is a payload it
    cannot act on -- paid for on every review."""
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    assert "## PROJECT FILES" not in _prompt_for(REGISTRY[name], context)


def test_the_project_listing_is_capped_below_project_trees_own_default(context):
    """300 paths is ~1,800 tokens on all 133 of run8's coder calls. The cap
    is the only thing keeping this off a large repository's every call."""
    from rudra.subagents.build import TREE_MAX_ENTRIES, _prompt_for

    assert TREE_MAX_ENTRIES < 300
    for i in range(TREE_MAX_ENTRIES + 50):
        (context.project_path / f"mod_{i:04d}.py").write_text("x = 1\n")

    prompt = _prompt_for(REGISTRY["coder"], context)
    listing = prompt.split("## PROJECT FILES", 1)[1]
    assert listing.count("mod_") == TREE_MAX_ENTRIES
    assert f"(cap: {TREE_MAX_ENTRIES})" in listing


def test_an_empty_project_still_builds_a_prompt(context):
    """`project_tree` answers "(empty project)"; nothing may crash on it."""
    from rudra.filesystem import EMPTY_PROJECT
    from rudra.subagents.build import _prompt_for

    prompt = _prompt_for(REGISTRY["coder"], context)
    assert "## PROJECT FILES" in prompt
    assert EMPTY_PROJECT in prompt


def test_the_listing_lands_between_the_facts_and_the_recall(context):
    """What was decided, then what is on disk, then what was remembered."""
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    context.facts = _stocked_store()
    prompt = _prompt_for(REGISTRY["coder"], context, task="add a route")

    assert prompt.index("## PROJECT FACTS") < prompt.index("## PROJECT FILES")


def test_the_listing_is_rebuilt_per_invocation_never_cached(context):
    """The coder writes the files it is being shown, so a stale listing is a
    correctness bug rather than a performance trade. build_agent runs once
    per invocation for exactly this reason (build.py:109-113)."""
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    assert "widget.py" not in _prompt_for(REGISTRY["coder"], context)

    (context.project_path / "widget.py").write_text("y = 2\n")
    assert "widget.py" in _prompt_for(REGISTRY["coder"], context)


def test_the_listings_cost_is_recorded(context):
    """This change ADDS to the fixed per-call prompt, which CLAUDE.md 5a
    says nothing currently measures. Without the number the trade -- calls
    bought with tokens -- cannot be settled, and the next session repeats
    the investigation."""
    import dataclasses

    from rudra.context.usage import RunUsage
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    usage = RunUsage()
    measured = dataclasses.replace(context, usage=usage)
    _prompt_for(REGISTRY["coder"], measured)

    assert usage.as_dict()["coder"]["tree_chars"] > 0
    assert usage.as_dict()["coder"]["tree_injections"] == 1


def test_a_context_without_usage_still_builds_the_listing(context):
    """SubagentContext.usage defaults to None; nothing may require it."""
    from rudra.subagents.build import _prompt_for

    (context.project_path / "app.py").write_text("x = 1\n")
    context.usage = None
    assert "app.py" in _prompt_for(REGISTRY["coder"], context)


def test_build_agent_passes_the_listing_to_create_deep_agent(context, monkeypatch):
    """A1.8's shape: a fix that exists and is not wired."""
    import rudra.subagents.build as build

    captured = {}
    monkeypatch.setattr(build, "create_deep_agent", lambda **kw: captured.update(kw) or object())
    (context.project_path / "app.py").write_text("x = 1\n")
    build.build_agent(REGISTRY["coder"], context)

    assert "## PROJECT FILES" in captured["system_prompt"]
    assert "app.py" in captured["system_prompt"]


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_repeat_guard_can_report_what_it_saved(name, context):
    # OPEN-39 Phase 2, and it is OPEN-45's lesson applied before the fact:
    # registered is not wired. A re-read the guard answers is a model call
    # that never happens, so it leaves no mark on calls, tokens or seconds
    # -- if the guard cannot reach the accounting, the saving is provable
    # only by re-running a script over a debug log, which this item's
    # document exists because of.
    import dataclasses

    usage = object()
    wired = dataclasses.replace(context, usage=usage)
    middleware = _middleware_for(REGISTRY[name], wired, _model_for(REGISTRY[name], wired.cfg))
    guard = next(m for m in middleware if type(m).__name__ == "RepeatGuardMiddleware")

    assert guard.usage is usage
    assert guard.role == REGISTRY[name].role


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_subagent_repeat_guard_still_builds_with_no_run_around_it(name, context):
    # The context fixture carries no usage, as every 9b-era stand-in does,
    # and the guard must go on guarding without one -- counting is the
    # optional half.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    guard = next(m for m in middleware if type(m).__name__ == "RepeatGuardMiddleware")

    assert guard.usage is None


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_subagent_repeat_guard_can_report_what_it_refused(name, context):
    # OPEN-57. Registered is not wired: the guard held a counter and no
    # sink, so its refusal reached the trace as a HumanMessage -- Rudra's
    # words wearing the user's name -- and nothing said a refusal had
    # happened at all. ModelRetryMiddleware above is the precedent.
    import dataclasses

    sink = object()
    wired = dataclasses.replace(context, trace=sink)
    middleware = _middleware_for(REGISTRY[name], wired, _model_for(REGISTRY[name], wired.cfg))
    guard = next(m for m in middleware if type(m).__name__ == "RepeatGuardMiddleware")

    assert guard.trace is sink
    assert guard.role == REGISTRY[name].role


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_subagent_repeat_guard_still_builds_with_no_trace(name, context):
    # The context fixture carries none, as every 9b-era stand-in does.
    middleware = _middleware_for(REGISTRY[name], context, _model_for(REGISTRY[name], context.cfg))
    guard = next(m for m in middleware if type(m).__name__ == "RepeatGuardMiddleware")

    assert guard.trace is None
