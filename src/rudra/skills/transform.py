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

import re
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


_REWRITABLE_SUFFIXES = frozenset({".md"})


def _rewrite_cross_refs(skill_root: Path, bundle: Bundle) -> int:
    """Turn `<prefix><skill>` references into read_file-able library paths.

    Upstream skills reference each other through a plugin namespace
    ("superpowers:test-driven-development"). Rudra has no plugin namespace
    and no skill-invoke tool, so a reference has to become a path the model
    can read_file -- which is exactly what deepagents' own skills prompt
    tells it to do (middleware/skills.py:721).

    Only known skill names are rewritten. Prose that happens to follow the
    prefix is not a cross-reference, and pointing it at a file that does not
    exist would be worse than leaving it alone.

    Targets are library paths, never active ones: library/ holds every
    skill regardless of enablement, so a reference stays valid when the
    enabled set changes and C5.9 rewrites nothing (spec S11a.4).
    """
    if bundle.cross_ref_prefix is None:
        return 0

    known = set(bundle.skill_names())
    pattern = re.compile(re.escape(bundle.cross_ref_prefix) + r"([a-z0-9][a-z0-9-]*)")

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in known:
            return match.group(0)
        return f"/skills/library/{bundle.name}/{name}/SKILL.md"

    rewritten = 0
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file() or path.suffix not in _REWRITABLE_SUFFIXES:
            continue
        original = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(_replace, original)
        if count:
            path.write_text(updated, encoding="utf-8")
        rewritten += sum(1 for m in pattern.finditer(original) if m.group(1) in known)
    return rewritten


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
    cross_refs = 0
    for bundle in bundles:
        for name in bundle.skill_names():
            target = library / bundle.name / name
            shutil.copytree(bundle.skills_path / name, target)
            cross_refs += _rewrite_cross_refs(target, bundle)
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
        cross_refs_rewritten=cross_refs,
        platform_refs_injected=(),
    )
