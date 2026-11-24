"""Where rendered skills live on disk.

The only module that knows about cache locations. `transform.render()`
stays pure and unaware, which is what lets tests render anywhere.

One install serves many project directories (D13 / TODO.md §0.4: `rudra`
is installed once, globally, then run wherever you cd to), so the cache is
user-level rather than per-project. Two projects with the same enabled set
share one directory; a project that enables a different set gets its own
key rather than fighting over one.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import rudra
from rudra.skills.bundle import Bundle
from rudra.skills.manifest import manifest_root_hash
from rudra.skills.transform import TRANSFORM_VERSION, render

# Written last, so a directory that exists but lacks it is known to be
# half-built. Existence is not completeness: the key is a pure function of
# its inputs, so a run killed mid-render would otherwise leave wreckage
# that nothing ever invalidates.
SENTINEL = ".complete"

_ACTIVE_ROUTE = "/skills/active/"


@dataclass(frozen=True)
class SkillCache:
    """One run's rendered corpus.

    Attributes:
        root: The directory holding `library/` and `active/`.
        active_path: The backend-relative source to hand `create_deep_agent`.
        fell_back: True when the cache root was unwritable and this is a
            per-run temp directory. Callers report it; nothing else changes,
            which is the point (S11b.2).
    """

    root: Path
    active_path: str = _ACTIVE_ROUTE
    fell_back: bool = False


def _cache_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "rudra" / "skills"


def cache_key(bundles: Sequence[Bundle], enabled: frozenset[str]) -> str:
    """A digest of everything that can change the rendered output.

    The corpus hash is what makes a hand-update of a vendored bundle
    (S11a.3) invalidate every user's cache with no version negotiation
    anywhere: different bytes, different manifest, different key.
    """
    digest = hashlib.sha256()

    def feed(text: str) -> None:
        """Hash one field, delimited. Without the separator two different
        enabled sets whose sorted names concatenate to the same string share
        a key, and one silently reuses the other's rendered `active/`. Not
        reachable with the shipped corpus (all 2^14 subsets enumerated, no
        collision), but registry.py is built so "shipping a second corpus is
        a directory plus one line". mempalace guards this exact class the
        same way, hashing `f"{wing}|{room}|{content}"` (CR-A3)."""
        digest.update(text.encode("utf-8"))
        digest.update(b"\x00")

    feed(rudra.__version__)
    feed(str(TRANSFORM_VERSION))
    for bundle in bundles:
        feed(bundle.name)
        feed(manifest_root_hash(bundle.manifest_path))
    for name in sorted(enabled):
        feed(name)
    return digest.hexdigest()[:16]


def _rendered_is_valid(root: Path, enabled: frozenset[str]) -> bool:
    """Whether `root` actually holds every enabled skill deepagents needs.

    The sentinel proves a build FINISHED; it does not prove the tree is
    still intact. `_build` publishes atomically, so a clean build cannot
    leave a sentinel beside a partial tree -- but both the cache home and
    the shared scratch (`prune_stale` never cleans scratch, OPEN-172) live
    on disk where an external actor can delete a rendered skill while the
    `.complete` file stays. deepagents' `_list_skills` silently skips any
    `active/<name>/` with no `SKILL.md`, so the coder would run with a
    subset. Re-check the one property that drives that skip -- an enabled
    skill with no `SKILL.md` -- so a corrupted cache self-heals on reuse.
    """
    active = root / "active"
    return all((active / name / "SKILL.md").is_file() for name in enabled)


def _build(bundles: Sequence[Bundle], enabled: frozenset[str], target: Path) -> None:
    """Render beside `target`, then publish with one atomic rename.

    Nothing is ever written into the final path, so a concurrent run either
    sees no cache or a complete one -- never a partial tree.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f"{target.name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        render(bundles, enabled, staging)
        (staging / SENTINEL).write_text("", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def ensure_cache(
    bundles: Sequence[Bundle],
    enabled: frozenset[str],
    *,
    cache_home: Path | None = None,
) -> SkillCache:
    """The rendered corpus for these inputs, building it if needed.

    Falls back to a shared, key-named temp directory when the cache root
    cannot be written -- a read-only home, a locked-down CI box, a sandboxed
    container. Rendering costs about 20ms for 550KB, so the fallback buys
    the property that matters: the same command produces the same agent
    whether or not the cache is writable.
    """
    key = cache_key(bundles, enabled)
    target = _cache_home(cache_home) / key

    # The sentinel says a build finished; `_rendered_is_valid` says the tree
    # is still intact. An externally-corrupted cache (sentinel present, a
    # SKILL.md gone) must re-render, not be served -- OPEN-172.
    if (target / SENTINEL).is_file() and _rendered_is_valid(target, enabled):
        return SkillCache(root=target)

    try:
        _build(bundles, enabled, target)
    except OSError:
        # Deterministic, so repeat runs SHARE one scratch tree. mkdtemp
        # minted a fresh directory per invocation and nothing ever removed
        # it -- prune_stale only cleans the cache home -- so on exactly the
        # boxes this path exists for (read-only home, locked-down CI,
        # sandboxed container) every `rudra` invocation wrote a full render
        # of every skill and abandoned it (CR-A6). The scratch lives in a
        # world-shared temp dir and nothing prunes it, so re-validate it on
        # reuse the same way -- a corrupted scratch is otherwise served
        # forever (OPEN-172).
        scratch = Path(tempfile.gettempdir()) / f"rudra-skills-{key}"
        rendered = scratch / "rendered"
        if (rendered / SENTINEL).is_file() and _rendered_is_valid(rendered, enabled):
            return SkillCache(root=rendered, fell_back=True)
        _build(bundles, enabled, rendered)
        return SkillCache(root=rendered, fell_back=True)

    return SkillCache(root=target)


def prune_stale(cache_home: Path | None, keep: str) -> list[str]:
    """Remove every rendered cache except `keep`. Returns what went.

    Old keys accumulate silently -- a new one appears whenever Rudra's
    version, the corpus or the enabled set changes -- and nothing else ever
    removes them. 11a deferred this deliberately until there was a command
    to hang it on (`rudra skills rebuild`).
    """
    root = _cache_home(cache_home)
    if not root.is_dir():
        return []

    removed = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name != keep:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed


__all__ = ["SENTINEL", "SkillCache", "cache_key", "ensure_cache", "prune_stale"]
