"""What file a model-written path will actually be written to.

ONE ANSWER, TWO CONSUMERS
-------------------------
Rudra builds every backend with ``virtual_mode=True``
(``agent/main_agent.py``), so deepagents treats a leading ``/`` as the
project root, not the machine root: ``write("/etc/passwd", ...)`` on a
backend rooted at ``<project>`` creates ``<project>/etc/passwd`` and leaves
the host's ``/etc/passwd`` untouched.  Measured directly against the pinned
0.7.4 backend, not inferred.

The permission gate did not know that.  It read an absolute path as a host
path, so a model that *obeyed* deepagents' own tool description --
"Absolute path where the file should be written. Must be absolute, not
relative." -- and wrote ``/src/app.py`` was denied ``<floor:outside-root>``,
the one floor rule config refuses to let you disable, for a write that would
have landed safely at ``<project>/src/app.py``.  ``permissions/diff.py``
made the same reading, so the approval panel previewed a *different file*
from the one the write would touch (TODO.md CR-B4).

This module is the shared answer.  ``virtual_to_relative`` is what the
backend will do, expressed once, so the gate and the backend cannot drift --
the same reason ``context/budget.py`` derives two eviction thresholds from
one window rather than letting them be configured apart.

WHY IT IS NOT THE WHOLE NORMALIZER
----------------------------------
``deepagents_path.install_path_normalizer`` does more: it strips
hallucinated sandbox prefixes and matches paths against PLAN.md.  Those
steps touch the filesystem, depend on run state, and are opt-in behind
``[compat] sandbox_paths``.  They are deliberately NOT mirrored here,
because every one of them strips *more* leading components -- so the real
write lands at or below what this function predicts, always inside the
root.  The gate's prediction is therefore conservative in the safe
direction: it can never predict "inside the project" for a write that
actually escapes it.

CROSS-PLATFORM
--------------
Absolute means three different things, and the model may emit any of them
on any host because it is guessing from training data, not from the OS it
is running on:

  ``C:\\project\\src\\app.py``   Windows drive-absolute
  ``\\\\server\\share\\app.py``      Windows UNC
  ``/src/app.py``              POSIX absolute
  ``\\src\\app.py``              Windows root-relative (no drive)

All four are handled here on every platform.  ``PureWindowsPath`` parses
Windows spellings on Linux and macOS, and ``PurePosixPath`` parses POSIX
spellings on Windows, so this module never asks what OS it is running on --
it asks what shape the path is.  That matters because a mac user's model
and a Windows user's model make the same mistakes.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

# A Windows path is recognised by shape, not by host OS: the model emits
# what its training data suggests, which is frequently the wrong platform
# for the machine it is running on.
_WINDOWS_SEPARATOR = "\\"


def looks_windows_absolute(path: str) -> bool:
    """Is this a Windows drive-absolute or UNC path, on any host?

    ``C:\\x``, ``C:/x`` and ``\\\\server\\share\\x`` all qualify.  A bare
    ``\\x`` does not -- it is root-relative with no drive, handled as a
    POSIX-style absolute path instead.
    """
    return bool(PureWindowsPath(path).drive)


def virtual_to_relative(path: str, project_root: Path) -> str | None:
    """The project-relative path this spelling refers to, or None.

    None means "this is not a path inside the project" -- the only caller
    that sees it today is a backend route (``/artifacts/``, ``/skills/``),
    which is mounted outside the root on purpose and must not be rewritten
    as if it were project content.

    Returns a POSIX-style relative path, because that is what deepagents
    stores and what a permission rule is written against.  A rule says
    ``write_file:src/app.py`` on every platform.
    """
    if not path:
        return ""

    # Windows drive-absolute or UNC. Strip the project root if the model
    # spelled it out, otherwise strip the anchor (`C:\` / `\\server\share\`)
    # and keep the rest -- exactly what the backend's normalizer does.
    if looks_windows_absolute(path):
        candidate = PureWindowsPath(path)
        for root in _windows_root_spellings(project_root):
            try:
                return candidate.relative_to(root).as_posix()
            except ValueError:
                continue
        try:
            return candidate.relative_to(candidate.anchor).as_posix()
        except ValueError:  # pragma: no cover - anchor is always a prefix
            return candidate.as_posix()

    # A backslash-separated relative path (`src\app.py`) is a Windows
    # spelling too, and PurePosixPath would read the whole thing as one
    # filename. Normalise the separator before anything else looks at it.
    if _WINDOWS_SEPARATOR in path:
        path = PureWindowsPath(path).as_posix()

    candidate = PurePosixPath(path)
    if not candidate.is_absolute():
        return candidate.as_posix()

    # Absolute POSIX. If the model spelled out the real project root, strip
    # it; both spellings of the root are tried because on macOS /tmp is a
    # symlink to /private/tmp, so the obvious spelling and the resolved one
    # differ for a project people really do work in (A1.58).
    for root in _posix_root_spellings(project_root):
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            continue

    # Otherwise it is a VIRTUAL absolute path: deepagents reads the leading
    # `/` as the project root, so `/src/app.py` is `<project>/src/app.py`.
    return candidate.relative_to("/").as_posix()


def virtual_to_host(path: str, project_root: Path) -> Path | None:
    """The real file this spelling names, or None for a backend route.

    The host-side counterpart of `virtual_to_relative`: what the gate needs
    in order to ask the floor a question about a real location.
    """
    relative = virtual_to_relative(path, project_root)
    if relative is None:
        return None
    return Path(project_root) / relative


def _posix_root_spellings(project_root: Path) -> tuple[str, ...]:
    """The project root as given and as it resolves, de-duplicated."""
    root = Path(project_root)
    spellings = [root.as_posix()]
    try:
        resolved = root.resolve().as_posix()
    except (OSError, RuntimeError):  # pragma: no cover - unresolvable root
        resolved = None
    if resolved is not None and resolved not in spellings:
        spellings.append(resolved)
    return tuple(spellings)


def _windows_root_spellings(project_root: Path) -> tuple[PureWindowsPath, ...]:
    """The project root in Windows form, as given and as it resolves."""
    root = Path(project_root)
    spellings = [PureWindowsPath(str(root))]
    try:
        resolved = PureWindowsPath(str(root.resolve()))
    except (OSError, RuntimeError):  # pragma: no cover - unresolvable root
        return tuple(spellings)
    if resolved != spellings[0]:
        spellings.append(resolved)
    return tuple(spellings)


__all__ = ["looks_windows_absolute", "virtual_to_host", "virtual_to_relative"]
