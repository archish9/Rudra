"""Per-file sha256 manifests over a vendored tree.

The vendored corpus is pinned by content, not by a git SHA: the upstream
copy Rudra was built from is a released plugin package, not a git checkout
(spec S11a.2). A manifest is therefore the immutable pin, and verifying it
is what makes the freeze rule (S11a.3) mechanical rather than a promise.

Step 11b reuses `manifest_root_hash` as one input to the cache key, so an
owner's hand-update of the corpus invalidates every user's cache without
any version negotiation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 65536


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest(root: Path) -> list[tuple[str, str]]:
    """Every file under `root` as sorted `(posix relpath, sha256 hex)` pairs.

    Sorted so the output is deterministic across filesystems: a manifest
    that reordered itself would change the 11b cache key for no reason.
    """
    entries = [
        (path.relative_to(root).as_posix(), _hash_file(path))
        for path in root.rglob("*")
        if path.is_file()
    ]
    return sorted(entries)


def format_manifest(entries: list[tuple[str, str]]) -> str:
    """`sha256sum`-compatible text: `<hex>  <relpath>`, newline terminated."""
    return "".join(f"{digest}  {rel}\n" for rel, digest in entries)


def parse_manifest(text: str) -> list[tuple[str, str]]:
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        entries.append((rel, digest))
    return sorted(entries)


def write_manifest(root: Path, out: Path) -> None:
    out.write_text(format_manifest(compute_manifest(root)), encoding="utf-8")


def verify_manifest(root: Path, manifest_path: Path) -> list[str]:
    """Differences between `root` and its manifest, as readable lines.

    Empty means clean. Reports content changes, additions and removals
    separately, because "someone edited a skill" and "someone dropped a
    file in" are different accidents with different fixes.
    """
    recorded = dict(parse_manifest(manifest_path.read_text(encoding="utf-8")))
    actual = dict(compute_manifest(root))

    problems = []
    for rel in sorted(set(recorded) - set(actual)):
        problems.append(f"missing from tree: {rel}")
    for rel in sorted(set(actual) - set(recorded)):
        problems.append(f"not in manifest: {rel}")
    for rel in sorted(set(recorded) & set(actual)):
        if recorded[rel] != actual[rel]:
            problems.append(f"content changed: {rel}")
    return problems


def manifest_root_hash(manifest_path: Path) -> str:
    """One hex digest standing for a whole manifest. Used by 11b's cache key."""
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
