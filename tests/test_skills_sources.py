from __future__ import annotations

from pathlib import Path

from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.sources import resolve_sources


def _cache(tmp_path: Path):
    return ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path / "cache")


def test_bundled_only_when_no_user_directories_exist(tmp_path: Path) -> None:
    sources = resolve_sources(tmp_path / "proj", _cache(tmp_path), home=tmp_path / "home")

    assert [s.label for s in sources] == ["bundled"]


def test_user_and_project_directories_are_added_in_precedence_order(tmp_path: Path) -> None:
    """Lowest precedence first: SkillsMiddleware lets later sources win."""
    home = tmp_path / "home"
    (home / ".rudra" / "skills" / "mine").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    sources = resolve_sources(project, _cache(tmp_path), home=home)

    assert [s.label for s in sources] == ["bundled", "user", "project"]


def test_an_empty_directory_is_not_offered_as_a_source(tmp_path: Path) -> None:
    """An empty dir would add a route and an index entry for nothing."""
    home = tmp_path / "home"
    (home / ".rudra" / "skills").mkdir(parents=True)

    sources = resolve_sources(tmp_path / "proj", _cache(tmp_path), home=home)

    assert [s.label for s in sources] == ["bundled"]


def test_no_cache_means_no_bundled_source(tmp_path: Path) -> None:
    """`[skills] enabled = []` builds no cache; user skills still work."""
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    sources = resolve_sources(project, None, home=tmp_path / "home")

    assert [s.label for s in sources] == ["project"]


def test_routes_are_distinct(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".rudra" / "skills" / "mine").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    routes = [s.route for s in resolve_sources(project, _cache(tmp_path), home=home)]

    assert len(set(routes)) == len(routes)


def test_the_bundled_source_is_indexed_from_active(tmp_path: Path) -> None:
    """Bundled skills split enabled/available; user skills do not."""
    home = tmp_path / "home"
    (home / ".rudra" / "skills" / "mine").mkdir(parents=True)

    sources = resolve_sources(tmp_path / "proj", _cache(tmp_path), home=home)
    by_label = {s.label: s.index_path for s in sources}

    assert by_label["bundled"] == "/skills/active/"
    assert by_label["user"] == "/skills/user/"
