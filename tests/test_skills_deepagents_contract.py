"""The rendered corpus, checked against deepagents' own skill parser.

SkillsMiddleware silently *skips* a skill whose frontmatter will not parse
or whose description is over length (middleware/skills.py). A skipped skill
is invisible at runtime: no error, it simply never appears in the index and
the model never reaches for it. These tests make that failure loud.

They import deepagents internals on purpose. If an upgrade moves or renames
them, this file failing is the signal to re-verify the contract, not a
reason to delete the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.middleware.skills import _parse_skill_metadata, _validate_skill_name

from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("skills")
    render(BUNDLES, DEFAULT_ENABLED, dest / "out")
    return dest / "out"


def test_every_active_skill_parses(rendered: Path) -> None:
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None, f"deepagents would silently skip {skill_md}"


def test_every_active_skill_name_matches_its_directory(rendered: Path) -> None:
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None
        valid, error = _validate_skill_name(metadata["name"], skill_md.parent.name)
        assert valid, error


def test_every_library_skill_parses(rendered: Path) -> None:
    """library/ is not indexed, but a skill that cannot parse cannot be enabled later."""
    for skill_md in sorted((rendered / "library").glob("*/*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None, f"deepagents would silently skip {skill_md}"


def test_active_holds_exactly_the_enabled_set(rendered: Path) -> None:
    on_disk = {p.name for p in (rendered / "active").iterdir() if p.is_dir()}
    assert on_disk == set(DEFAULT_ENABLED)


def test_descriptions_are_non_empty(rendered: Path) -> None:
    """An empty description makes a skill unselectable even when indexed."""
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None
        assert metadata["description"].strip()
