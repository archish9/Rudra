"""Cross-platform path normalization for deepagents.

WHY THIS EXISTS
--------------
deepagents' ``validate_path`` rejects absolute paths with a hard ValueError.
Some LLMs ignore system-prompt instructions and emit absolute paths regardless
of the OS — Windows-style (``C:\\project\\models.py``) or POSIX-style
(``/home/user/project/models.py``).  This shim intercepts both forms before
deepagents sees them and converts them to relative POSIX paths.

``validate_path`` is only called inside ``deepagents.middleware.filesystem``
(confirmed by grep across the full deepagents package).  We patch the
module-level name there because ``FilesystemMiddleware`` uses
``from deepagents.backends.utils import validate_path``, so tool closures
look up the name in that module's globals at call time.

NORMALIZATION LAYERS
--------------------
1. Windows absolute path (C:\\...) → strip drive + project root → relative
2. POSIX absolute path:
   a. Strip known sandbox prefix (/testbed/, /workspace/, etc.)
   b. Strip actual project root prefix (matches real machine path)
   c. Plan-aware suffix matching against PLAN.md filenames
   d. Last resort: bogus-dir-aware basename stripping
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
        plan_path: Optional path to .rudra/PLAN.md for plan-aware suffix matching.
                   When provided, planned filenames are used to resolve unknown paths.

    Safe to call multiple times (always wraps the original, never re-wraps).
    """
    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw

    original = getattr(_utils, _ORIGINAL_KEY, None)
    if original is None:
        original = _utils.validate_path
        setattr(_utils, _ORIGINAL_KEY, original)

    _win_root = PureWindowsPath(project_root)
    _posix_root = project_root.as_posix()

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

        # Step 2a: Strip known sandbox prefixes, then continue to plan matching.
        # Don't return early — the stripped path may still have a project-name
        # component (e.g. "/home/user/repos/myproject/requirements.txt" strips to
        # "myproject/requirements.txt" which still needs suffix resolution).
        sandbox_stripped: str | None = None
        for prefix in SANDBOX_PREFIXES:
            if path.startswith(prefix):
                sandbox_stripped = path[len(prefix):]
                break

        if sandbox_stripped is not None:
            planned = _load_planned_files(plan_path)
            if planned:
                match = _plan_suffix_match(sandbox_stripped, planned)
                if match is not None:
                    return original(match, allowed_prefixes=allowed_prefixes)
            return original(sandbox_stripped, allowed_prefixes=allowed_prefixes)

        # Step 2b: Strip actual project root prefix
        try:
            path = pp.relative_to(_posix_root).as_posix()
            return original(path, allowed_prefixes=allowed_prefixes)
        except ValueError:
            pass

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
