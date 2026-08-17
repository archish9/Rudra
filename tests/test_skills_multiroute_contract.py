"""Can one CompositeBackend serve several skill sources at once?

Step 11c mounts user and project skill directories alongside the bundled
cache. Whether they can be nested under one prefix (/skills/, /skills/user/)
or must be disjoint (/skills/, /user-skills/) is a deepagents behaviour, not
a Rudra decision, and it determines the route layout.

Written before any production code, for the reason 11b's route contract test
was: the cheapest possible check on the assumption everything else rests on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.skills import _list_skills

from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render

SKILL = """---
name: {name}
description: {description}
---

# {name}

Body.
"""


def _write_skill(root: Path, name: str, description: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        SKILL.format(name=name, description=description), encoding="utf-8"
    )


def _backend(project: Path, routes: dict[str, Path]) -> CompositeBackend:
    return CompositeBackend(
        default=LocalShellBackend(root_dir=str(project), virtual_mode=True, env={}),
        routes={
            prefix: FilesystemBackend(root_dir=str(path), virtual_mode=True)
            for prefix, path in routes.items()
        },
        artifacts_root="/artifacts",
    )


@pytest.fixture(scope="module")
def bundled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("bundled")
    render(BUNDLES, DEFAULT_ENABLED, dest)
    return dest


def test_nested_prefixes_resolve_independently(tmp_path: Path, bundled: Path) -> None:
    """The layout the spec prefers: /skills/ and /skills/user/ side by side."""
    user = tmp_path / "user"
    _write_skill(user, "deploy-checklist", "Use when shipping to production")

    backend = _backend(tmp_path / "proj", {"/skills/": bundled, "/skills/user/": user})

    bundled_skills = {s["name"] for s in _list_skills(backend, "/skills/active/")}
    user_skills = {s["name"] for s in _list_skills(backend, "/skills/user/")}

    assert "brainstorming" in bundled_skills
    assert user_skills == {"deploy-checklist"}


def test_disjoint_prefixes_resolve(tmp_path: Path, bundled: Path) -> None:
    """The fallback layout, if nesting turns out to be ambiguous."""
    user = tmp_path / "user"
    _write_skill(user, "deploy-checklist", "Use when shipping to production")

    backend = _backend(tmp_path / "proj", {"/skills/": bundled, "/user-skills/": user})

    assert {s["name"] for s in _list_skills(backend, "/user-skills/")} == {"deploy-checklist"}


def test_later_sources_shadow_earlier_ones(tmp_path: Path) -> None:
    """Upstream's layering rule (skills.py:955), which C5.1's precedence relies on.

    Driven through the middleware's own `before_agent` rather than a
    reimplementation of its loop: the aggregation that makes a project skill
    override a bundled one lives there, keyed by name with the last source
    winning. Its `runtime` and `config` arguments are unused upstream.
    """
    from deepagents.middleware.skills import SkillsMiddleware

    low, high = tmp_path / "low", tmp_path / "high"
    _write_skill(low, "shared", "the bundled description")
    _write_skill(high, "shared", "the project description")

    backend = _backend(tmp_path / "proj", {"/skills/": low, "/skills/project/": high})
    middleware = SkillsMiddleware(backend=backend, sources=["/skills/", "/skills/project/"])

    update = middleware.before_agent({}, None, None)
    loaded = {s["name"]: s["description"] for s in update["skills_metadata"]}

    assert loaded["shared"] == "the project description"
