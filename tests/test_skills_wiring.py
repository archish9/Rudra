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
