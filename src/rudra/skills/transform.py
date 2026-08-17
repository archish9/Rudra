"""Render vendored bundles into the tree an agent reads.

Pure: takes bundles and an enabled set, writes a destination. It does not
know about caches, XDG paths, or agents -- Step 11b decides where this
output lives and hands it to create_deep_agent(skills=...).

Two output trees, with different rules:

  library/<bundle>/<skill>/   every skill, namespaced. Never indexed, only
                              read_file'd, so no name constraint applies and
                              collisions are impossible by construction.
  active/<skill>/             the enabled skills, flat. deepagents requires
                              the frontmatter `name` to equal the parent
                              directory name (middleware/skills.py:347), so
                              namespacing here would mean rewriting vendored
                              frontmatter.

active/ is copied from the rendered library/, not from the source, so it
inherits every rewrite for free and the two trees cannot drift.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rudra.skills.bundle import Bundle


class DuplicateSkillError(ValueError):
    """Two bundles ship an enabled skill with the same name.

    Skill-name uniqueness across bundles is the owner's contract (spec
    S11a.6), so this is an invariant rather than a condition to resolve:
    there is no precedence rule and no config to disambiguate. It fails
    loudly because silent shadowing -- a second bundle quietly replacing a
    skill body -- would be unfindable at runtime.
    """


@dataclass(frozen=True)
class RenderReport:
    """What a render produced. Tests and 11b's cache builder read this."""

    library_skills: tuple[str, ...]
    active_skills: tuple[str, ...]
    cross_refs_rewritten: int
    platform_refs_injected: tuple[str, ...]


def _check_no_duplicate_active_names(bundles: Sequence[Bundle], enabled: frozenset[str]) -> None:
    owner: dict[str, str] = {}
    for bundle in bundles:
        for name in bundle.skill_names():
            if name not in enabled:
                continue
            if name in owner:
                msg = (
                    f"enabled skill '{name}' is shipped by both bundle "
                    f"'{owner[name]}' and bundle '{bundle.name}'. Skill names "
                    f"must be unique across bundles."
                )
                raise DuplicateSkillError(msg)
            owner[name] = bundle.name


def render(
    bundles: Sequence[Bundle],
    enabled: frozenset[str],
    dest: Path,
) -> RenderReport:
    """Render `bundles` into `dest`, enabling `enabled` in the index."""
    _check_no_duplicate_active_names(bundles, enabled)

    library = dest / "library"
    active = dest / "active"
    if dest.exists():
        shutil.rmtree(dest)
    library.mkdir(parents=True)
    active.mkdir(parents=True)

    library_skills: list[str] = []
    for bundle in bundles:
        for name in bundle.skill_names():
            shutil.copytree(bundle.skills_path / name, library / bundle.name / name)
            library_skills.append(f"{bundle.name}/{name}")

    active_skills: list[str] = []
    for bundle in bundles:
        for name in bundle.skill_names():
            if name in enabled:
                shutil.copytree(library / bundle.name / name, active / name)
                active_skills.append(name)

    return RenderReport(
        library_skills=tuple(sorted(library_skills)),
        active_skills=tuple(sorted(active_skills)),
        cross_refs_rewritten=0,
        platform_refs_injected=(),
    )
