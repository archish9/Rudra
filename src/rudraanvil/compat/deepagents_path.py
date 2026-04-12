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
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Sequence

_ORIGINAL_KEY = "_rudraanvil_original_validate_path"


def install_path_normalizer(project_root: Path) -> None:
    """Patch deepagents to accept absolute paths from LLMs on any OS.

    Handles:
    - Windows absolute paths  e.g. ``C:\\project\\src\\models.py``
    - POSIX absolute paths     e.g. ``/home/user/project/src/models.py``
    - Bare POSIX-rooted paths  e.g. ``/src/models.py`` (no project prefix)

    Safe to call multiple times (always wraps the original, never re-wraps).
    """
    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw

    original = getattr(_utils, _ORIGINAL_KEY, None)
    if original is None:
        original = _utils.validate_path
        setattr(_utils, _ORIGINAL_KEY, original)

    _win_root = PureWindowsPath(project_root)
    # as_posix() gives a clean forward-slash string on every OS ("C:/..." on
    # Windows, "/home/..." on Linux/macOS) that PurePosixPath can parse.
    _posix_root = project_root.as_posix()

    def _normalize(path: str, *, allowed_prefixes: Sequence[str] | None = None) -> str:
        wp = PureWindowsPath(path)
        if wp.drive:
            # Windows absolute path: C:\...\file.py  or  C:/...
            try:
                path = wp.relative_to(_win_root).as_posix()
            except ValueError:
                path = wp.relative_to(wp.anchor).as_posix()
        elif PurePosixPath(path).is_absolute():
            # POSIX absolute path: /home/user/project/src/file.py  or  /src/file.py
            # PurePosixPath is a pure string parser — it works identically on
            # every OS (Windows included) because it never touches the filesystem.
            pp = PurePosixPath(path)
            try:
                # Best case: LLM included the full project root prefix — strip it.
                path = pp.relative_to(_posix_root).as_posix()
            except ValueError:
                # Fallback: path starts with / but no project prefix — just
                # strip the leading slash (relative_to("/") always succeeds).
                path = pp.relative_to("/").as_posix()
        return original(path, allowed_prefixes=allowed_prefixes)

    _utils.validate_path = _normalize
    _fs_mw.validate_path = _normalize
