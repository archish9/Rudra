from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from rudra.skills.bundle import Bundle
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import DuplicateSkillError, render


def _fake_bundle(root: Path, name: str, skills: dict[str, str]) -> Bundle:
    """A minimal on-disk bundle, so layout tests do not depend on 472K of corpus."""
    skills_root = root / "src" / "skills"
    for skill_name, body in skills.items():
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: test skill\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return Bundle(
        name=name,
        root=root,
        upstream="https://example.invalid/x",
        version="1.0.0",
        copied_on="2026-08-17",
        source="fixture",
        license="MIT",
        license_file="LICENSE",
        copyright="Copyright (c) 2026 Nobody",
        frozen=True,
        skills_root="src/skills",
    )


def test_library_is_namespaced_by_bundle(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "demo" / "beta" / "SKILL.md").is_file()


def test_active_is_flat_and_holds_only_enabled_skills(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "alpha" / "SKILL.md").is_file()
    assert not (tmp_path / "out" / "active" / "beta").exists()
    assert report.active_skills == ("alpha",)
    assert report.library_skills == ("demo/alpha", "demo/beta")


def test_supporting_files_travel_with_their_skill(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    refs = bundle.skills_path / "alpha" / "references"
    refs.mkdir()
    (refs / "deep.md").write_text("reference body", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "references" / "deep.md").is_file()
    assert (tmp_path / "out" / "active" / "alpha" / "references" / "deep.md").is_file()


def test_duplicate_active_skill_name_raises_naming_both_bundles(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2"})

    with pytest.raises(DuplicateSkillError) as excinfo:
        render([one, two], frozenset({"alpha"}), tmp_path / "out")

    message = str(excinfo.value)
    assert "alpha" in message
    assert "first" in message
    assert "second" in message


def test_duplicate_names_are_fine_when_only_one_is_enabled(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2", "beta": "B"})

    render([one, two], frozenset({"beta"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "beta").is_dir()


def test_render_into_an_existing_dest_replaces_it(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    dest = tmp_path / "out"
    (dest / "library" / "demo" / "stale").mkdir(parents=True)

    render([bundle], frozenset({"alpha"}), dest)

    assert not (dest / "library" / "demo" / "stale").exists()


def test_render_is_deterministic(tmp_path: Path) -> None:
    """Byte-identical output twice over, or 11b's cache key thrashes."""
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "one")
    render([bundle], frozenset({"alpha"}), tmp_path / "two")

    first = sorted(p.relative_to(tmp_path / "one") for p in (tmp_path / "one").rglob("*"))
    second = sorted(p.relative_to(tmp_path / "two") for p in (tmp_path / "two").rglob("*"))
    assert first == second
    for rel in first:
        left, right = tmp_path / "one" / rel, tmp_path / "two" / rel
        if left.is_file():
            assert left.read_bytes() == right.read_bytes()


def test_renders_the_real_corpus(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert len(report.library_skills) == 14
    assert len(report.active_skills) == 9
    assert (tmp_path / "out" / "active" / "brainstorming" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "superpowers" / "writing-skills" / "SKILL.md").is_file()


def test_cross_refs_become_library_paths(tmp_path: Path) -> None:
    bundle = _fake_bundle(
        tmp_path / "b",
        "demo",
        {"alpha": "See demo:beta for details.", "beta": "B"},
    )
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body
    assert "demo:beta" not in body
    assert report.cross_refs_rewritten == 1


def test_active_copies_inherit_the_rewrite(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"})
    bundle = replace(bundle, cross_ref_prefix="demo:")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "active" / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_unknown_names_after_the_prefix_are_left_alone(tmp_path: Path) -> None:
    """Only real skill names are rewritten.

    Prose like "demo:whatever" is not a cross-reference, and turning it into
    a path to a file that does not exist would be worse than leaving it.
    """
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:nosuchskill."})
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "demo:nosuchskill" in body
    assert report.cross_refs_rewritten == 0


def test_a_bundle_without_a_prefix_is_not_rewritten(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert report.cross_refs_rewritten == 0


def test_supporting_markdown_is_rewritten_too(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})
    bundle = replace(bundle, cross_ref_prefix="demo:")
    (bundle.skills_path / "alpha" / "notes.md").write_text("see demo:beta", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_real_corpus_has_no_surviving_namespace_refs(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert report.cross_refs_rewritten == 26

    survivors = [
        path
        for path in (tmp_path / "out").rglob("*.md")
        if "superpowers:" in path.read_text(encoding="utf-8")
    ]
    assert survivors == []


def test_every_rewritten_target_exists_on_disk(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    targets = set()
    pattern = re.compile(r"/skills/library/([a-z0-9-]+)/([a-z0-9-]+)/SKILL\.md")
    for path in dest.rglob("*.md"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            targets.add((match.group(1), match.group(2)))

    assert targets
    for bundle_name, skill_name in sorted(targets):
        assert (dest / "library" / bundle_name / skill_name / "SKILL.md").is_file()
