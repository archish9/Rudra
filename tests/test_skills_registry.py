from __future__ import annotations

from pathlib import Path

import pytest

from rudra.skills.bundle import load_bundle
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


def test_registry_ships_superpowers() -> None:
    assert [b.name for b in BUNDLES] == ["superpowers"]


def test_superpowers_metadata_matches_upstream_release() -> None:
    bundle = BUNDLES[0]

    assert bundle.upstream == "https://github.com/obra/superpowers"
    assert bundle.version == "6.3.0"
    assert bundle.license == "MIT"
    assert "Jesse Vincent" in bundle.copyright
    assert bundle.frozen is True


def test_superpowers_declares_its_transform_hooks() -> None:
    bundle = BUNDLES[0]

    assert bundle.cross_ref_prefix == "superpowers:"
    assert bundle.bootstrap_skill == "using-superpowers"
    assert bundle.platform_ref_section == "## Platform Adaptation"


def test_bundle_paths_resolve_to_real_files() -> None:
    bundle = BUNDLES[0]

    assert bundle.skills_path.is_dir()
    assert bundle.manifest_path.is_file()
    assert bundle.license_path.is_file()
    assert (bundle.skills_path / bundle.bootstrap_skill / "SKILL.md").is_file()


def test_skill_names_are_the_fourteen_upstream_skills() -> None:
    names = BUNDLES[0].skill_names()

    assert len(names) == 14
    assert names == tuple(sorted(names))
    assert "brainstorming" in names
    assert "using-superpowers" in names


def test_default_enabled_is_the_eight_plus_the_bootstrap() -> None:
    assert DEFAULT_ENABLED == frozenset(
        {
            "brainstorming",
            "writing-plans",
            "executing-plans",
            "systematic-debugging",
            "test-driven-development",
            "verification-before-completion",
            "requesting-code-review",
            "receiving-code-review",
            "using-superpowers",
        }
    )


def test_every_enabled_name_exists_in_a_bundle() -> None:
    available = {name for bundle in BUNDLES for name in bundle.skill_names()}
    assert DEFAULT_ENABLED <= available


def test_load_bundle_rejects_a_missing_required_key(tmp_path: Path) -> None:
    (tmp_path / "BUNDLE.toml").write_text('name = "x"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="upstream"):
        load_bundle(tmp_path)
