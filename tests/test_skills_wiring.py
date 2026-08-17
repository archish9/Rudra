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


def test_planner_is_constructed_with_skills(tmp_path: Path) -> None:
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
            skills_sources=("/skills/active/",),
        )

    assert spy.call_args.kwargs["skills"] == ["/skills/active/"]


def test_planner_without_sources_passes_no_skills_argument(tmp_path: Path) -> None:
    """None means absent, not empty -- every existing test relies on it."""
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

    assert spy.call_args.kwargs.get("skills") is None


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


def test_bootstrap_is_read_from_the_rendered_cache(cache_root: Path) -> None:
    """It must be the rendered copy, not the packaged one.

    The rendered copy carries both 11a adaptations: cross-references
    rewritten to paths, and the Platform Adaptation list naming
    rudra-tools.md. A copy imported from src/ would send the model looking
    for a plugin namespace that does not exist.
    """
    from rudra.agent.planner_agent import bootstrap_text

    text = bootstrap_text(cache_root)

    assert text is not None
    assert "references/rudra-tools.md" in text
    assert "superpowers:" not in text


def test_bootstrap_drops_its_yaml_frontmatter(cache_root: Path) -> None:
    """Frontmatter is loader metadata, not instruction.

    Injected verbatim it drops a YAML document into the middle of a system
    prompt and tells the model its own instructions are "Use when starting
    any conversation" -- text addressed to whoever reads the skill index,
    not to the agent being prompted.
    """
    from rudra.agent.planner_agent import bootstrap_text

    text = bootstrap_text(cache_root)

    assert text is not None
    assert not text.startswith("---")
    assert "description: Use when starting any conversation" not in text
    # The instructions themselves survive intact.
    assert "Platform Adaptation" in text
    assert "ABSOLUTELY MUST invoke the skill" in text


def test_bootstrap_is_none_when_it_is_not_in_the_cache(tmp_path: Path) -> None:
    from rudra.agent.planner_agent import bootstrap_text

    assert bootstrap_text(tmp_path) is None


def test_bootstrap_is_none_without_a_cache_at_all() -> None:
    from rudra.agent.planner_agent import bootstrap_text

    assert bootstrap_text(None) is None


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_planner_stage_carries_the_bootstrap(
    stage: str, tmp_path: Path, cache_root: Path
) -> None:
    from rudra.agent import planner_agent

    _project(tmp_path)
    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage=stage,
            skills_sources=("/skills/active/",),
            skills_cache_root=cache_root,
        )

    assert "Platform Adaptation" in spy.call_args.kwargs["system_prompt"]


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
