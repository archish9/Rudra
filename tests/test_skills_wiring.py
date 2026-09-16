"""Who is constructed with skills, and who is not."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from rudra.config.loader import get_config, reset_config
from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.sources import SkillSource


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


@pytest.fixture
def cache_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    home = tmp_path_factory.mktemp("cache-home")
    return ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=home).root


def _console() -> Console:
    return Console(quiet=True)


def _project(tmp_path: Path, body: str = ""):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    # get_config, not build_config: it installs the process-wide Config, so
    # the get_config() inside create_planner_agent resolves to this project
    # rather than rebuilding from Path.cwd() and importing the repo's .env.
    return get_config(tmp_path)


def test_build_backend_mounts_the_skills_route(tmp_path: Path, cache_root: Path) -> None:
    from rudra.agent.main_agent import build_backend

    cfg = _project(tmp_path)
    bundled = SkillSource("bundled", "/skills/", cache_root)

    backend = build_backend(cfg, tmp_path, (bundled,))

    assert "/skills/" in backend.routes


def test_build_backend_without_a_cache_has_no_skills_route(tmp_path: Path) -> None:
    """Every existing caller passes no cache and must keep working."""
    from rudra.agent.main_agent import build_backend

    cfg = _project(tmp_path)

    backend = build_backend(cfg, tmp_path)

    assert "/skills/" not in backend.routes
    assert "/artifacts/" in backend.routes


def test_the_normalizer_is_told_about_every_route_the_backend_mounts(
    tmp_path: Path, cache_root: Path
) -> None:
    """A1.79: a route the normalizer does not know about gets mangled.

    The prefixes are passed to install_path_normalizer literally, because
    it runs before the backend is built. This asserts the two lists agree,
    so adding a third route without telling the normalizer fails here
    rather than in a live run.
    """
    from rudra.agent import main_agent

    cfg = _project(tmp_path)
    bundled = SkillSource("bundled", "/skills/", cache_root)
    mounted = set(main_agent.build_backend(cfg, tmp_path, (bundled,)).routes)

    assert mounted == set(main_agent.route_prefixes((bundled,)))


def test_only_the_writing_subagents_want_skills() -> None:
    """S11b.1. The reviewer reads a diff; it is not choosing a method."""
    from rudra.subagents.registry import REGISTRY

    wanting = {name for name, spec in REGISTRY.items() if spec.wants_skills}

    assert wanting == {"coder", "tester"}


@pytest.mark.parametrize("name", ["coder", "tester"])
def test_wanting_subagents_are_built_with_skills(name: str, tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY[name], context)

    assert spy.call_args.kwargs["skills"] == ["/skills/active/"]


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_other_subagents_are_built_without_skills(name: str, tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY[name], context)

    assert spy.call_args.kwargs.get("skills") is None


def test_no_subagent_gets_skills_when_the_run_has_none(tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=None)

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY["coder"], context)

    assert spy.call_args.kwargs.get("skills") is None


def _planner_call(stage: str, tmp_path: Path):
    """create_deep_agent's kwargs for one planner stage."""
    from rudra.agent import planner_agent

    _project(tmp_path)
    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="write a todo application in python",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage=stage,
        )
    return spy.call_args.kwargs


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_planner_stage_indexes_any_skill(stage: str, tmp_path: Path) -> None:
    """OPEN-17, and absence is the enforcement.

    The corpus is written for one agent that clarifies, designs and
    implements in a single conversation; Rudra plans with three agents
    holding disjoint tools. Measured four times on
    nvidia/nemotron-3-ultra-550b-a55b with the same prompt: indexed, the
    planner emitted the skill's own checklist as the task list ("Write
    design doc to docs/superpowers/specs/", "Invoke writing-plans skill")
    or declared nothing at all; absent, seven units of real work.

    Three attempts to fix it in prompt text lost to the injected bootstrap's
    "YOU DO NOT HAVE A CHOICE. YOU MUST USE IT." A prompt cannot outrank a
    prompt. A missing index can.
    """
    assert _planner_call(stage, tmp_path)["skills"] is None


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_planner_stage_carries_the_bootstrap(stage: str, tmp_path: Path) -> None:
    """It instructs the model to invoke skills the planner does not index.

    Injecting it now would be strictly worse than the bug it was added for
    (C5.3): it would send a stage after a corpus that is not there.
    """
    prompt = _planner_call(stage, tmp_path)["system_prompt"]

    assert "Platform Adaptation" not in prompt
    assert "YOU DO NOT HAVE A CHOICE" not in prompt


def test_clarify_carries_the_merged_methodology(tmp_path: Path) -> None:
    """OPEN-17: the methodology is kept, the runtime skill read is not.

    brainstorming's "Understanding the idea" is now stage text rather than a
    file the model may or may not open -- so it applies to every planning
    run, on every model, instead of when a 550B model chooses to read it.
    """
    # Something to read: a greenfield clarify is not told to look first,
    # because it has nothing to look at and no tool to look with (OPEN-116).
    (tmp_path / "todo.py").write_text("", encoding="utf-8")
    prompt = _planner_call("clarify", tmp_path)["system_prompt"]

    # Look before asking, and size the request before refining it.
    assert "Look before you ask." in prompt
    assert "several independent pieces" in prompt
    # Purpose, constraints, success criteria -- the three that carry a run.
    assert "purpose" in prompt
    assert "constraints" in prompt
    assert "how you would know this succeeded" in prompt


def test_architect_carries_the_merged_methodology(tmp_path: Path) -> None:
    """ "Exploring approaches" and "Design for isolation and clarity"."""
    prompt = _planner_call("architect", tmp_path)["system_prompt"]

    assert "Weigh 2-3 approaches before you record one." in prompt
    assert "why the others lost" in prompt
    assert "Cut ruthlessly." in prompt
    assert "what it does, how it is used, and what it depends" in prompt
    assert "Follow the patterns that are" in prompt


def test_breakdown_carries_the_merged_self_review(tmp_path: Path) -> None:
    """The spec self-review, retargeted from a document to the ledger."""
    prompt = _planner_call("breakdown", tmp_path)["system_prompt"]

    assert "Re-read your list before you send it." in prompt
    assert "contradict" in prompt
    assert "placeholder" in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_is_told_to_produce_a_document(stage: str, tmp_path: Path) -> None:
    """What deliberately did NOT transfer, because it is what leaked.

    The spec-document step and the writing-plans handoff are the two the
    failing runs emitted as tasks. Rudra's plan is the ledger.
    """
    prompt = _planner_call(stage, tmp_path)["system_prompt"].lower()

    assert "design doc" not in prompt
    assert "spec file" not in prompt
    assert "writing-plans" not in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_each_stage_is_constructed_with_its_own_prompt(stage: str, tmp_path: Path) -> None:
    """A1.78: `stage` was never forwarded, so all three got `breakdown`.

    The tools were scoped correctly the whole time, which is what hid it:
    the clarify stage was told to call add_tasks while holding only
    record_fact and ask_user. This asserts the seam that 10b's tests
    straddled -- they covered _tools_for_stage and build_planner_prompt
    separately, and never the call between them.
    """
    from rudra.agent import planner_agent

    cfg = _project(tmp_path)
    # A project with content, so `can_read` is the default this expectation
    # is built with. The greenfield side of the same seam is pinned in
    # tests/test_planner_stages.py (OPEN-116).
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    expected = planner_agent.build_planner_prompt(
        "x",
        tmp_path,
        None,
        stage=stage,
        can_ask=stage == "clarify" and cfg.agent.max_questions > 0,
        max_questions=cfg.agent.max_questions,
    )

    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage=stage,
        )

    assert spy.call_args.kwargs["system_prompt"] == expected


def test_the_planner_prompt_has_no_bootstrap_without_a_cache(tmp_path: Path) -> None:
    from rudra.agent import planner_agent

    _project(tmp_path)
    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage="clarify",
        )

    assert "Platform Adaptation" not in spy.call_args.kwargs["system_prompt"]


def test_subagent_prompts_never_carry_the_bootstrap(tmp_path: Path) -> None:
    """SUBAGENT-STOP: upstream tells dispatched subagents to ignore it."""
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY["coder"], context)

    assert "Platform Adaptation" not in spy.call_args.kwargs["system_prompt"]


def _subagent_context(tmp_path: Path, *, skills_sources):
    from rudra.subagents.runner import SubagentContext

    return SubagentContext(
        project_path=tmp_path,
        backend=None,
        gate=None,
        console=_console(),
        cfg=_project(tmp_path),
        skills_sources=skills_sources,
    )


def _project_with_skill(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    skill = project / ".rudra" / "skills" / "deploy-checklist"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy-checklist\ndescription: Use when shipping\n---\n\n# Deploy\n",
        encoding="utf-8",
    )
    return project


def test_user_and_project_skills_are_mounted_and_indexed(tmp_path: Path, cache_root: Path) -> None:
    """C5.1's remaining half: a user's own skills reach the agent."""
    from rudra.agent.main_agent import build_backend
    from rudra.skills.sources import resolve_sources

    project = _project_with_skill(tmp_path)
    cfg = _project(project)

    class _Cache:
        root = cache_root

    sources = resolve_sources(project, _Cache(), home=tmp_path / "home")
    backend = build_backend(cfg, project, sources)

    assert "/skills/project/" in backend.routes
    assert "/skills/" in backend.routes


def test_the_normalizer_still_knows_every_mounted_route(tmp_path: Path, cache_root: Path) -> None:
    """A1.79's guard, extended to the routes this step adds."""
    from rudra.agent import main_agent
    from rudra.skills.sources import resolve_sources

    project = _project_with_skill(tmp_path)
    cfg = _project(project)

    class _Cache:
        root = cache_root

    sources = resolve_sources(project, _Cache(), home=tmp_path / "home")
    mounted = set(main_agent.build_backend(cfg, project, sources).routes)

    assert mounted == set(main_agent.route_prefixes(sources))


def test_index_paths_are_ordered_lowest_precedence_first(tmp_path: Path, cache_root: Path) -> None:
    """The order handed to create_deep_agent decides who shadows whom."""
    from rudra.skills.sources import resolve_sources

    project = _project_with_skill(tmp_path)

    class _Cache:
        root = cache_root

    paths = [s.index_path for s in resolve_sources(project, _Cache(), home=tmp_path / "home")]

    assert paths == ["/skills/active/", "/skills/project/"]
