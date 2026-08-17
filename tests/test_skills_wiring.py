"""Who is constructed with skills, and who is not."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from rudra.config.loader import build_config, reset_config
from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


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
    return build_config(tmp_path)


def test_build_backend_mounts_the_skills_route(tmp_path: Path, cache_root: Path) -> None:
    from rudra.agent.main_agent import build_backend

    cfg = _project(tmp_path)

    backend = build_backend(cfg, tmp_path, skills_root=cache_root)

    assert "/skills/" in backend.routes


def test_build_backend_without_a_cache_has_no_skills_route(tmp_path: Path) -> None:
    """Every existing caller passes no cache and must keep working."""
    from rudra.agent.main_agent import build_backend

    cfg = _project(tmp_path)

    backend = build_backend(cfg, tmp_path)

    assert "/skills/" not in backend.routes
    assert "/artifacts/" in backend.routes


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
