"""Where skills come from, and in what order they win.

The one module that knows user skills live in `.rudra/skills/`. Everything
else takes a resolved tuple.

User skills are deliberately NOT rendered into the keyed cache: their
content changes freely, and the key hashes vendored manifests only (11a),
so a copy would go stale the moment someone edited a file. They mount at
their real directories instead.

Nested route prefixes are safe here -- `/skills/` and `/skills/user/`
resolve independently, with the more specific route winning rather than
being swallowed. Measured before this module existed, in
tests/test_skills_multiroute_contract.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUNDLED_ROUTE = "/skills/"
USER_ROUTE = "/skills/user/"
PROJECT_ROUTE = "/skills/project/"


@dataclass(frozen=True)
class SkillSource:
    """One place skills come from.

    Attributes:
        label: "bundled", "user" or "project" -- what `rudra skills list`
            prints, and how a shadowed skill is explained.
        route: The backend-relative prefix it mounts at.
        local_path: The real directory behind that route.
    """

    label: str
    route: str
    local_path: Path

    @property
    def index_path(self) -> str:
        """The path handed to `create_deep_agent(skills=...)`.

        Bundled skills are indexed from `active/` inside the rendered cache;
        user directories are indexed at their root, because there is no
        enabled/available split for skills a user wrote themselves.
        """
        return f"{self.route}active/" if self.label == "bundled" else self.route


def _has_a_skill(directory: Path) -> bool:
    """Is there anything here worth mounting?

    An empty directory would add a route and a source for nothing, and a
    source that yields no skills is noise in `rudra skills list`.
    """
    return directory.is_dir() and any(child.is_dir() for child in directory.iterdir())


def resolve_sources(
    project_path: Path,
    cache: Any | None,
    *,
    home: Path | None = None,
) -> tuple[SkillSource, ...]:
    """Every skill source for this run, lowest precedence first.

    Order is the contract: `SkillsMiddleware` lets later sources override
    earlier ones by name (skills.py:955), so a project skill shadows a user
    one, which shadows a bundled one.
    """
    root = home if home is not None else Path.home()
    found: list[SkillSource] = []

    if cache is not None:
        found.append(SkillSource("bundled", BUNDLED_ROUTE, cache.root))

    user_dir = root / ".rudra" / "skills"
    if _has_a_skill(user_dir):
        found.append(SkillSource("user", USER_ROUTE, user_dir))

    project_dir = project_path / ".rudra" / "skills"
    if _has_a_skill(project_dir):
        found.append(SkillSource("project", PROJECT_ROUTE, project_dir))

    return tuple(found)


def routes_for(sources: Sequence[SkillSource]) -> tuple[str, ...]:
    """Route prefixes these sources need mounted."""
    return tuple(source.route for source in sources)


__all__ = [
    "BUNDLED_ROUTE",
    "PROJECT_ROUTE",
    "USER_ROUTE",
    "SkillSource",
    "resolve_sources",
    "routes_for",
]
