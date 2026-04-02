"""Cross-platform path normalization for deepagents.

WHY THIS EXISTS
--------------
deepagents' ``validate_path`` rejects Windows absolute paths (anything
matching ``^[a-zA-Z]:``) with a hard ValueError.  Some LLMs ignore system
prompt instructions and emit paths like
``C:\\laragon\\www\\project\\models.py`` regardless.  This shim intercepts
those paths before deepagents sees them and converts them to relative POSIX
paths that deepagents accepts.

``validate_path`` is only called inside ``deepagents.middleware.filesystem``
(confirmed by grep across the full deepagents package).  We patch the
module-level name there because ``FilesystemMiddleware`` uses
``from deepagents.backends.utils import validate_path``, so tool closures
look up the name in that module's globals at call time.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Sequence

_ORIGINAL_KEY = "_rudraanvil_original_validate_path"


def install_path_normalizer(project_root: Path) -> None:
    """Patch deepagents to accept Windows absolute paths from LLMs.

    Safe to call multiple times (always wraps the original, never re-wraps).
    """
    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw

    original = getattr(_utils, _ORIGINAL_KEY, None)
    if original is None:
        original = _utils.validate_path
        setattr(_utils, _ORIGINAL_KEY, original)

    _win_root = PureWindowsPath(project_root)

    def _normalize(path: str, *, allowed_prefixes: Sequence[str] | None = None) -> str:
        wp = PureWindowsPath(path)
        if wp.drive:  # C:, D:, … — Windows absolute path on any host OS
            try:
                path = wp.relative_to(_win_root).as_posix()
            except ValueError:
                path = wp.relative_to(wp.anchor).as_posix()
        return original(path, allowed_prefixes=allowed_prefixes)

    _utils.validate_path = _normalize
    _fs_mw.validate_path = _normalize
