"""Capped, names-only project listing for agent prompts.

Replaces VirtualFileSystem.get_tree(), deleted in Step 2 (TODO.md D7). The
VFS loaded every text file in the project into RAM to answer this one
question; project_tree answers it from filenames alone.

Listing strategy:
  1. `git ls-files --cached --others --exclude-standard` whenever git can
     answer. Git resolves .gitignore semantics itself — negations, leading
     `/`, `**`, trailing `/`, nested .gitignore files, global excludes —
     which is the direct fix for TODO.md A1.10.
  2. Otherwise — including when git succeeds but lists nothing — an
     os.walk + wcmatch pass over the root .gitignore.

Never reads the content of a listed file. The only file this module opens
is the root .gitignore, and only on the fallback path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wcmatch import glob as wcglob

EMPTY_PROJECT = "(empty project)"

_GIT_TIMEOUT_SECONDS = 5

# Applied on BOTH listing paths, so they hold even in a repo that tracks
# them. `.rudra` is Rudra's own state directory and must never appear in
# the model's view of the project.
_ALWAYS_SKIP_DIRS = frozenset(
    {".git", ".rudra", "__pycache__", "node_modules", ".venv", "venv"}
)
_ALWAYS_SKIP_SUFFIXES = (".pyc",)

_WCMATCH_FLAGS = wcglob.GLOBSTAR | wcglob.NEGATE | wcglob.DOTGLOB


def project_tree(
    root: Path,
    *,
    max_depth: int = 5,
    max_entries: int = 300,
) -> str:
    """Return a capped, flat listing of the project's files — names only.

    Args:
        root: Project root directory.
        max_depth: Maximum number of path segments to keep. `pyproject.toml`
            is depth 1; `src/rudra/cli.py` is depth 3.
        max_entries: Maximum paths to list before truncating with a footer.

    Returns:
        One relative POSIX path per line, sorted, plus a truncation footer
        when entries were omitted. `"(empty project)"` when nothing matches.

    Note:
        When git lists nothing, the walk runs and therefore lists files git
        would have excluded. That is deliberate: in a directory where
        everything is ignored, "show the agent what is here" is the right
        answer, and it is what `VirtualFileSystem.get_tree()` did before
        this module replaced it. See TODO.md A1.19.
    """
    root = Path(root)
    if not root.is_dir():
        return EMPTY_PROJECT

    # `not entries`, not `entries is None`. Git exits 0 with no output when
    # the root is itself gitignored — a successful answer of "nothing" —
    # which is indistinguishable from an empty project and must still fall
    # through to the walk. See TODO.md A1.19.
    entries = _git_listing(root)
    if not entries:
        entries = _walk_listing(root, max_depth=max_depth)

    kept = sorted(
        {
            rel
            for rel in entries
            if not _is_always_skipped(rel)
            and rel.count("/") + 1 <= max_depth
            and (root / rel).is_file()
        }
    )
    if not kept:
        return EMPTY_PROJECT

    shown = kept[:max_entries]
    omitted = len(kept) - len(shown)
    lines = list(shown)
    if omitted:
        lines.append(f"… {omitted} more entries omitted (cap: {max_entries})")
    return "\n".join(lines)


def _is_always_skipped(rel_posix: str) -> bool:
    """True when any path segment is in the builtin skip set."""
    if any(part in _ALWAYS_SKIP_DIRS for part in rel_posix.split("/")):
        return True
    return rel_posix.endswith(_ALWAYS_SKIP_SUFFIXES)


def _git_listing(root: Path) -> list[str] | None:
    """Paths from git, or None when git cannot answer.

    None covers every failure mode identically: git missing, root outside a
    work tree (exit 128), or the call timing out.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    raw = completed.stdout.decode("utf-8", errors="replace")
    return [entry for entry in raw.split("\0") if entry]


def _walk_listing(root: Path, *, max_depth: int) -> list[str]:
    """Fallback listing: os.walk pruned by the root .gitignore and max_depth."""
    patterns = _gitignore_patterns(root)
    found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)

        # Bound the walk by construction rather than discarding over-deep
        # paths after traversing them. Nothing below a directory already at
        # max_depth can survive the depth filter. `Path(".").parts` is `()`,
        # so the root itself is depth 0.
        if len(rel_dir.parts) >= max_depth:
            dirnames[:] = []

        # Prune in place. wcmatch's `**/build` matches `build` but NOT
        # `build/artifact.o`, so pruning the directory is what actually
        # keeps an ignored subtree out — filtering files alone would not.
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _ALWAYS_SKIP_DIRS
            and not _matches((rel_dir / name).as_posix(), patterns)
        ]

        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if _is_always_skipped(rel) or _matches(rel, patterns):
                continue
            found.append(rel)

    return found


def _gitignore_patterns(root: Path) -> list[str]:
    """Translate the root .gitignore into wcmatch patterns."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []

    patterns: list[str] = []
    for raw_line in gitignore.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(_to_wcmatch(line))
    return patterns


def _to_wcmatch(line: str) -> str:
    """Convert one .gitignore line into a wcmatch pattern.

    A pattern containing no `/` matches at any depth, so it gets a `**/`
    prefix. A leading `/` anchors to the root. A trailing `/` marks a
    directory, which the caller handles by matching the directory name.
    `!` negation is passed through — wcmatch's NEGATE flag consumes it.
    """
    negated = line.startswith("!")
    if negated:
        line = line[1:]

    line = line.rstrip("/")
    if line.startswith("/"):
        pattern = line.lstrip("/")
    elif "/" in line:
        pattern = line
    else:
        pattern = f"**/{line}"

    return f"!{pattern}" if negated else pattern


def _matches(rel_posix: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    return wcglob.globmatch(rel_posix, patterns, flags=_WCMATCH_FLAGS)
