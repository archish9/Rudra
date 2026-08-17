"""Would deepagents load this skill, and if not, why?

Delegates to deepagents' own parser rather than reimplementing its rules.
`SkillsMiddleware` *silently skips* a skill it cannot parse -- it logs a
warning and carries on -- so at runtime a broken skill looks exactly like
one the model chose not to use. This is the only place a user finds out,
which is why S11b.3 made it a precondition for shipping writable skill
directories.

Importing deepagents internals is deliberate. If an upgrade moves them,
this failing is the signal to re-verify the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deepagents.middleware.skills import _parse_skill_metadata, _validate_skill_name


@dataclass(frozen=True)
class Finding:
    """One skill directory, and whether deepagents would load it."""

    path: Path
    name: str
    ok: bool
    reason: str


def validate_skill_dir(directory: Path) -> Finding:
    """Check one `<name>/SKILL.md`."""
    name = directory.name
    skill_md = directory / "SKILL.md"

    if not skill_md.is_file():
        return Finding(directory, name, False, "no SKILL.md in this directory")

    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Finding(directory, name, False, f"unreadable: {exc}")

    metadata = _parse_skill_metadata(content, str(skill_md), name)
    if metadata is None:
        return Finding(
            directory,
            name,
            False,
            "frontmatter is missing or unparseable, or the description is empty "
            "or too long -- deepagents would skip this skill without an error",
        )

    valid, error = _validate_skill_name(metadata["name"], name)
    if not valid:
        return Finding(directory, name, False, error)

    # Checked separately from the parser: a skill with no description is
    # indexed and unselectable, which is worse than one that fails to load,
    # because nothing anywhere reports it.
    #
    # "None" is not a typo. `description:` with no value is YAML null, and
    # skills.py:416 does `str(frontmatter_data.get("description", ""))`, so
    # the description becomes the four-character string "None". The skill
    # then loads, appears in the index, and tells the model nothing.
    description = metadata["description"].strip()
    if not description or description == "None":
        return Finding(
            directory,
            name,
            False,
            "description is empty, so the model has nothing to select this skill on",
        )

    return Finding(directory, name, True, "")


def validate_tree(root: Path) -> list[Finding]:
    """Every skill directory under `root`, sorted by name."""
    if not root.is_dir():
        return []
    return [validate_skill_dir(child) for child in sorted(root.iterdir()) if child.is_dir()]


__all__ = ["Finding", "validate_skill_dir", "validate_tree"]
