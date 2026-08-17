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
    digest.update(rudra.__version__.encode("utf-8"))
    digest.update(str(TRANSFORM_VERSION).encode("utf-8"))
    for bundle in bundles:
        digest.update(bundle.name.encode("utf-8"))
        digest.update(manifest_root_hash(bundle.manifest_path).encode("utf-8"))
    for name in sorted(enabled):
        digest.update(name.encode("utf-8"))
    return digest.hexdigest()[:16]


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

    Falls back to a per-run temp directory when the cache root cannot be
    written -- a read-only home, a locked-down CI box, a sandboxed
    container. Rendering costs about 20ms for 550KB, so the fallback buys
    the property that matters: the same command produces the same agent
    whether or not the cache is writable.
    """
    target = _cache_home(cache_home) / cache_key(bundles, enabled)

    if (target / SENTINEL).is_file():
        return SkillCache(root=target)

    try:
        _build(bundles, enabled, target)
    except OSError:
        scratch = Path(tempfile.mkdtemp(prefix="rudra-skills-"))
        _build(bundles, enabled, scratch / "rendered")
        return SkillCache(root=scratch / "rendered", fell_back=True)

    return SkillCache(root=target)


__all__ = ["SENTINEL", "SkillCache", "cache_key", "ensure_cache"]
