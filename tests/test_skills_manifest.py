from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rudra.skills.manifest import compute_manifest, verify_manifest, write_manifest

BUNDLE_ROOT = Path(__file__).parent.parent / "src" / "rudra" / "skills" / "bundles" / "superpowers"


def test_compute_manifest_is_sorted_and_hashes_content(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")

    entries = compute_manifest(tmp_path)

    assert [rel for rel, _ in entries] == ["a.txt", "b.txt"]
    assert entries[0][1] == hashlib.sha256(b"alpha").hexdigest()


def test_verify_manifest_reports_edited_file(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    manifest = tmp_path / "MANIFEST.sha256"
    write_manifest(root, manifest)

    (root / "a.txt").write_text("tampered", encoding="utf-8")

    problems = verify_manifest(root, manifest)

    assert len(problems) == 1
    assert "a.txt" in problems[0]


def test_verify_manifest_reports_added_and_removed_files(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    manifest = tmp_path / "MANIFEST.sha256"
    write_manifest(root, manifest)

    (root / "a.txt").unlink()
    (root / "b.txt").write_text("beta", encoding="utf-8")

    problems = verify_manifest(root, manifest)

    assert any("a.txt" in p for p in problems)
    assert any("b.txt" in p for p in problems)


def test_vendored_superpowers_tree_is_unmodified() -> None:
    """The freeze rule (spec S11a.3), enforced.

    The vendored copy is byte-identical to superpowers 6.3.0 and is never
    edited -- the transform rewrites the rendered output, never the source.
    A failure here means someone edited the frozen corpus.
    """
    problems = verify_manifest(BUNDLE_ROOT / "src", BUNDLE_ROOT / "MANIFEST.sha256")
    assert problems == []


def test_vendored_corpus_has_the_expected_shape() -> None:
    skills_root = BUNDLE_ROOT / "src" / "skills"
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    support_files = [p for p in skills_root.rglob("*") if p.is_file() and p.name != "SKILL.md"]

    assert len(skill_files) == 14
    assert len(support_files) == 37
    assert list(skills_root.rglob("*.py")) == []


def test_vendored_license_is_present_and_mit() -> None:
    text = (BUNDLE_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Jesse Vincent" in text


def test_the_vendored_corpus_is_pinned_to_lf_line_endings():
    """CR-X2: the manifest hashes RAW BYTES, so line endings are content.

    Git's default on Windows is core.autocrlf=true, which rewrites LF to
    CRLF on checkout. Without a .gitattributes saying otherwise, every file
    under skills/bundles/ hashed differently on a Windows clone, so
    verify_manifest reported all 55 as `content changed:` and the cache key
    moved -- a checkout artefact read as tampering.

    This asserts the protection rather than the symptom, because the
    symptom only appears on a platform this suite has never run on (CR-X3).
    """
    repo_root = Path(__file__).resolve().parent.parent
    attributes = repo_root / ".gitattributes"

    assert attributes.is_file(), ".gitattributes is what pins this; it is missing"
    text = attributes.read_text(encoding="utf-8")
    assert "src/rudra/skills/bundles/** text eol=lf" in text


def test_no_vendored_file_currently_holds_a_crlf_line_ending():
    """The other half: the bytes on disk must match what the rule promises.

    A file committed with CRLF before the rule existed would still hash
    wrong everywhere, and .gitattributes alone would not fix it.
    """
    bundles = Path(__file__).resolve().parent.parent / "src" / "rudra" / "skills" / "bundles"
    if not bundles.is_dir():  # pragma: no cover - corpus always ships
        pytest.skip("no vendored corpus")

    offenders = [
        str(path.relative_to(bundles))
        for path in bundles.rglob("*")
        if path.is_file() and b"\r\n" in path.read_bytes()
    ]

    assert offenders == []
