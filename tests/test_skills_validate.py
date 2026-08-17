from __future__ import annotations

from pathlib import Path

from rudra.skills.validate import validate_tree

GOOD = """---
name: deploy-checklist
description: Use when shipping to production
---

# Deploy checklist
"""


def _skill(root: Path, name: str, body: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


def test_a_well_formed_skill_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "deploy-checklist", GOOD)

    findings = validate_tree(tmp_path)

    assert [f.ok for f in findings] == [True]


def test_a_name_that_does_not_match_its_directory_fails(tmp_path: Path) -> None:
    """deepagents requires name == parent directory (skills.py:347)."""
    _skill(tmp_path, "deploy-checklist", GOOD.replace("name: deploy-checklist", "name: deploy", 1))

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert "directory" in findings[0].reason.lower()


def test_missing_frontmatter_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "broken", "# No frontmatter here\n")

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert findings[0].reason


def test_an_empty_description_fails(tmp_path: Path) -> None:
    """An indexed skill with no description is unselectable."""
    _skill(tmp_path, "quiet", "---\nname: quiet\ndescription:\n---\n\n# Quiet\n")

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False


def test_a_directory_without_a_skill_file_fails(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert "SKILL.md" in findings[0].reason


def test_findings_are_sorted_and_cover_every_directory(tmp_path: Path) -> None:
    _skill(tmp_path, "beta", GOOD.replace("deploy-checklist", "beta"))
    _skill(tmp_path, "alpha", GOOD.replace("deploy-checklist", "alpha"))

    findings = validate_tree(tmp_path)

    assert [f.name for f in findings] == ["alpha", "beta"]


def test_the_vendored_corpus_validates_clean() -> None:
    """The strongest possible check: the real 14 pass their own parser."""
    from rudra.skills.registry import BUNDLES

    findings = validate_tree(BUNDLES[0].skills_path)

    assert [f for f in findings if not f.ok] == []
