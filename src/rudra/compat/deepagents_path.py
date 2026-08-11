"""Cross-platform path normalization for deepagents.

WHY THIS EXISTS
--------------
Some LLMs ignore system-prompt instructions and emit absolute paths regardless
of the OS — Windows-style (``C:\\project\\models.py``), POSIX-style
(``/home/user/project/models.py``), or hallucinated sandbox prefixes from
training data (``/testbed/app/main.py``).  Left alone these resolve to the
wrong place in the backend's virtual filesystem.

Measured against the pinned deepagents 0.7.4, upstream ``validate_path`` does
NOT reject absolute paths: ``validate_path("/abs/x.py")`` returns
``"/abs/x.py"`` unchanged and ``validate_path("main.py")`` returns
``"/main.py"`` — it prepends ``/``, virtual-root semantics.  An earlier
revision of this docstring claimed it "rejects absolute paths with a hard
ValueError"; that was false for 0.7.4 (TODO.md A4.11).  This shim's real job
is to strip real-machine and sandbox prefixes so the resulting virtual path
points at the intended file.

``validate_path`` is only called inside ``deepagents.middleware.filesystem``
(confirmed by grep across the full deepagents package).  We patch the
module-level name there because ``FilesystemMiddleware`` uses
``from deepagents.backends.utils import validate_path``, so tool closures
look up the name in that module's globals at call time.

NORMALIZATION LAYERS
--------------------
1. Windows absolute path (C:\\...) → strip drive + project root → relative
2. POSIX absolute path:
   a. Strip actual project root prefix (matches real machine path)
   b. Strip known sandbox prefix (/testbed/, /workspace/, etc.)
   c. Plan-aware suffix matching against PLAN.md filenames
   d. Last resort: bogus-dir-aware basename stripping

   (a) precedes (b) deliberately — see the ordering note in ``_normalize``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional, Sequence

from rudra.compat.path_constants import BOGUS_DIRS, SANDBOX_PREFIXES

_ORIGINAL_KEY = "_rudra_original_validate_path"


def _load_planned_files(plan_path: Optional[Path]) -> set[str]:
    """Read PLAN.md and return all planned filenames (both full paths and basenames)."""
    if plan_path is None or not plan_path.exists():
        return set()
    files: set[str] = set()
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if "- [ ]" in line or "- [x]" in line:
            name = line.strip().replace("- [ ]", "").replace("- [x]", "").strip()
            if name:
                files.add(name)
                basename = PurePosixPath(name).name
                if basename != name:
                    files.add(basename)
    return files


def install_path_normalizer(
    project_root: Path,
    plan_path: Optional[Path] = None,
) -> None:
    """Patch deepagents to accept absolute paths from LLMs on any OS.

    Handles:
    - Windows absolute paths  e.g. ``C:\\project\\src\\models.py``
    - POSIX absolute paths     e.g. ``/home/user/project/src/models.py``
    - Sandbox prefixes         e.g. ``/testbed/app/main.py``
    - Unknown absolute paths   e.g. ``/some/random/prefix/requirements.txt``

    Args:
        project_root: Absolute path to the project root directory.
        plan_path: Optional path to .rudra/run/PLAN.md for plan-aware suffix matching.
                   When provided, planned filenames are used to resolve unknown paths.

    Safe to call multiple times (always wraps the original, never re-wraps).
    """
    from rudra.compat.version_guard import (
        require_deepagents_attr,
        require_deepagents_version,
    )

    # This function rewrites a deepagents internal in two places. Fail here,
    # naming the ledger item, rather than deep inside a tool call later.
    require_deepagents_version("U.4")
    require_deepagents_attr("deepagents.backends.utils", "validate_path", "U.4")
    require_deepagents_attr("deepagents.middleware.filesystem", "validate_path", "U.4")

    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw

    original = getattr(_utils, _ORIGINAL_KEY, None)
    if original is None:
        original = _utils.validate_path
        setattr(_utils, _ORIGINAL_KEY, original)

    _win_root = PureWindowsPath(project_root)

    # Both spellings of the root, de-duplicated and ordered so the one the
    # caller actually passed is tried first. They differ whenever the root
    # sits behind a symlink -- /tmp and /home on macOS being the cases that
    # matter, since people really do work there (A1.58).
    _posix_roots: tuple[str, ...] = tuple(
        dict.fromkeys((project_root.as_posix(), project_root.resolve().as_posix()))
    )

    def _root_candidates(pp: PurePosixPath) -> tuple[PurePosixPath, ...]:
        """The incoming path as written, and as it resolves on disk.

        `resolve()` touches the filesystem, so it is attempted second and
        only for the root test -- never to rewrite what gets returned. A
        path that cannot be resolved (a permission error, a dangling
        symlink) simply contributes no second candidate.
        """
        try:
            resolved = PurePosixPath(Path(pp).resolve().as_posix())
        except (OSError, RuntimeError):
            return (pp,)
        return (pp,) if resolved == pp else (pp, resolved)

    def _plan_suffix_match(rel_path: str, planned: set[str]) -> str | None:
        """Try progressively shorter suffixes of rel_path against planned files."""
        parts = rel_path.split("/")
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if candidate in planned:
                return candidate
        return None

    def _normalize(path: str, *, allowed_prefixes: Sequence[str] | None = None) -> str:
        # --- Step 1: Windows absolute paths ---
        wp = PureWindowsPath(path)
        if wp.drive:
            try:
                path = wp.relative_to(_win_root).as_posix()
            except ValueError:
                path = wp.relative_to(wp.anchor).as_posix()
            return original(path, allowed_prefixes=allowed_prefixes)

        # --- Step 2: POSIX absolute paths only ---
        if not PurePosixPath(path).is_absolute():
            return original(path, allowed_prefixes=allowed_prefixes)

        pp = PurePosixPath(path)

        # Step 2a: Strip the actual project root prefix.
        #
        # This runs BEFORE the sandbox list, and the order is load-bearing: the
        # root is a verified-true match for this machine, while SANDBOX_PREFIXES
        # is a guess. Several of those guesses ("/tmp/", "/app/", "/code/",
        # "/src/", "/root/", "/home/user/") are also ordinary directories people
        # really work in, so stripping them first truncates a correct absolute
        # path at the wrong component and the write lands where nothing reads
        # (TODO.md A1.51).
        # Both spellings of the root and of the incoming path are tried, and
        # a match on any pair counts. The root arrives resolved, but the
        # model writes the path it was shown -- and on macOS /tmp is a
        # symlink to /private/tmp while /home is autofs, so a project the
        # user opened at /tmp/work is recorded as /private/tmp/work and the
        # obvious spelling misses. It then falls through to the sandbox
        # list, which is A1.51's failure on the one platform A1.51's own
        # test could not see (TODO.md A1.58).
        #
        # Strictly additive: it can only add matches, never remove one, so a
        # path genuinely outside the project still falls through untouched.
        # Same reasoning, and the same /etc -> /private/etc case, as
        # PermissionEngine.rule_matches offering both spellings.
        for candidate in _root_candidates(pp):
            for root in _posix_roots:
                try:
                    stripped = candidate.relative_to(root).as_posix()
                except ValueError:
                    continue
                return original(stripped, allowed_prefixes=allowed_prefixes)

        # Step 2b: Not under the real root — now try the known sandbox prefixes,
        # then continue to plan matching. Don't return early on the strip alone:
        # the stripped path may still have a project-name component (e.g.
        # "/home/user/repos/myproject/requirements.txt" strips to
        # "myproject/requirements.txt", which still needs suffix resolution).
        sandbox_stripped: str | None = None
        for prefix in SANDBOX_PREFIXES:
            if path.startswith(prefix):
                sandbox_stripped = path[len(prefix) :]
                break

        if sandbox_stripped is not None:
            planned = _load_planned_files(plan_path)
            if planned:
                match = _plan_suffix_match(sandbox_stripped, planned)
                if match is not None:
                    return original(match, allowed_prefixes=allowed_prefixes)
            return original(sandbox_stripped, allowed_prefixes=allowed_prefixes)

        # Step 2c: Plan-aware suffix matching on the full absolute path.
        # e.g., "/unknown/prefix/app/database.py" → tries "app/database.py" → match.
        planned = _load_planned_files(plan_path)
        if planned:
            parts = pp.parts[1:]  # Strip leading "/"
            match = _plan_suffix_match("/".join(parts), planned)
            if match is not None:
                return original(match, allowed_prefixes=allowed_prefixes)

        # Step 2d: Last resort — strip "/" then apply bogus-dir trimming.
        # Avoids creating deep bogus directories from hallucinated paths.
        path = pp.relative_to("/").as_posix()
        path_parts = path.split("/")
        if len(path_parts) > 3:
            second_to_last = path_parts[-2]
            if second_to_last in BOGUS_DIRS:
                path = path_parts[-1]
            else:
                path = "/".join(path_parts[-2:])

        return original(path, allowed_prefixes=allowed_prefixes)

    _utils.validate_path = _normalize
    _fs_mw.validate_path = _normalize
