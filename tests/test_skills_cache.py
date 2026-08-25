from __future__ import annotations

from pathlib import Path

from rudra.skills.cache import SENTINEL, cache_key, ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


def test_key_is_stable_for_the_same_inputs() -> None:
    assert cache_key(BUNDLES, DEFAULT_ENABLED) == cache_key(BUNDLES, DEFAULT_ENABLED)


def test_key_changes_with_the_enabled_set() -> None:
    fewer = frozenset(set(DEFAULT_ENABLED) - {"brainstorming"})

    assert cache_key(BUNDLES, DEFAULT_ENABLED) != cache_key(BUNDLES, fewer)


def test_key_changes_with_the_transform_version(monkeypatch) -> None:
    """The only lever that can invalidate a cache after a transform edit.

    cache_key hashes the transform's *inputs* -- bundle manifests and the
    enabled set. RUDRA_TOOLS_MD and the Platform Adaptation bullet live in
    rudra_tools.py, which is the transform, not an input, so editing either
    moves no hashed byte. Without the bump every existing cache keeps
    serving the old rendering and the edit reaches no model (OPEN-17/18).
    """
    before = cache_key(BUNDLES, DEFAULT_ENABLED)
    monkeypatch.setattr("rudra.skills.cache.TRANSFORM_VERSION", 999)

    assert cache_key(BUNDLES, DEFAULT_ENABLED) != before


def test_key_ignores_enabled_ordering() -> None:
    """A frozenset has no order; the key must not acquire one."""
    a = frozenset({"brainstorming", "writing-plans"})
    b = frozenset({"writing-plans", "brainstorming"})

    assert cache_key(BUNDLES, a) == cache_key(BUNDLES, b)


def test_builds_a_usable_cache(tmp_path: Path) -> None:
    cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    assert cache.active_path == "/skills/active/"
    assert cache.fell_back is False
    assert (cache.root / "active" / "brainstorming" / "SKILL.md").is_file()
    assert (cache.root / "library" / "superpowers" / "writing-skills" / "SKILL.md").is_file()
    assert (cache.root / SENTINEL).is_file()


def test_a_complete_cache_is_reused_not_rebuilt(tmp_path: Path) -> None:
    first = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)
    marker = first.root / "active" / "brainstorming" / "SKILL.md"
    marker.write_text("TOUCHED", encoding="utf-8")

    second = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    assert second.root == first.root
    assert marker.read_text(encoding="utf-8") == "TOUCHED"


def test_an_incomplete_cache_is_rebuilt(tmp_path: Path) -> None:
    """Existence is not completeness.

    A run killed mid-render leaves a half-populated directory. The key is a
    pure function of its inputs, so nothing would ever invalidate it --
    every later run would reuse the wreckage.
    """
    first = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)
    (first.root / SENTINEL).unlink()
    (first.root / "active" / "brainstorming" / "SKILL.md").write_text("HALF", encoding="utf-8")

    second = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    body = (second.root / "active" / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    assert body != "HALF"
    assert (second.root / SENTINEL).is_file()


def test_no_temp_directories_are_left_behind(tmp_path: Path) -> None:
    cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    siblings = [p.name for p in cache.root.parent.iterdir()]
    assert [n for n in siblings if ".tmp-" in n] == []


def test_falls_back_to_a_temp_dir_when_the_root_is_unwritable(tmp_path: Path) -> None:
    """S11b.2: an unwritable cache must not change how the agent reasons."""
    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=blocked)

        assert cache.fell_back is True
        assert (cache.root / "active" / "brainstorming" / "SKILL.md").is_file()
        assert (cache.root / SENTINEL).is_file()
    finally:
        blocked.chmod(0o700)


def test_the_fallback_cache_is_still_readable_by_deepagents(tmp_path: Path) -> None:
    """Asserted through the middleware, not by trusting the flag."""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=blocked)
        backend = FilesystemBackend(root_dir=str(cache.root), virtual_mode=True)

        skills = _list_skills(backend, "/active/")

        assert sorted(s["name"] for s in skills) == sorted(DEFAULT_ENABLED)
    finally:
        blocked.chmod(0o700)


def test_an_empty_enabled_set_still_renders_the_library(tmp_path: Path) -> None:
    """`enabled = []` means "index nothing", not "build nothing".

    Callers skip the cache entirely in that case (see main_agent), but the
    cache module itself must not special-case it into a broken tree.
    """
    cache = ensure_cache(BUNDLES, frozenset(), cache_home=tmp_path)

    assert list((cache.root / "active").iterdir()) == []
    assert (cache.root / "library" / "superpowers" / "brainstorming" / "SKILL.md").is_file()
