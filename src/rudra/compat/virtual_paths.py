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

The one step they share is NOT mirrored but called: stripping the real
root, by any spelling of the root or of the path, is `strip_project_root`
here and step 2a there (OPEN-159).  It was two copies until a symlinked
spelling of the root was the project's file to the backend and a virtual
path to the gate, and a comment claiming they agreed was all that held
them together.  `tests/test_deepagents_path.py::
test_the_gate_and_the_backend_read_every_spelling_alike` holds them now.

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

    # Absolute POSIX. If the model spelled out the real project root, by any
    # spelling, strip it -- the backend's own rule, shared (OPEN-159).
    roots = posix_root_spellings(project_root)
    try:
        stripped = strip_project_root(path, roots)
    except ValueError:
        # An embedded NUL, which `resolve()` refuses. The backend lets that
        # raise and the tool answers `Error: ...`; but the gate asks this
        # BEFORE its own CR-B7 guard, so here the path is read as written.
        stripped = _relative_to_any(candidate, roots)
    if stripped is not None:
        return stripped

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


def strip_project_root(path: str, roots: tuple[str, ...]) -> str | None:
    """The project-relative spelling of POSIX-absolute `path`, or None.

    ONE implementation with two callers (OPEN-159): `virtual_to_relative`,
    which the gate, the approval preview and every middleware ask, and
    `deepagents_path._normalize` step 2a, which is what the file tools hand
    the backend. They were two copies, and they differed: the backend's tried
    the path as it RESOLVES as well as written, so a spelling through a link
    to the root -- on macOS every `/tmp/<project>` and `/var/folders/...`
    path, the root arriving as `/private/...` -- was the project's own file to
    the backend and a virtual path to the gate. A deny rule on `.env` let that
    spelling through, and the backend edited `.env`.

    `roots` is `posix_root_spellings`: the root as given and as resolved,
    which is A1.58. The path as written is tried against them first, so a
    `..` the floor must see survives (`<root>/../x` is `../x`); its resolved
    spelling only after, and never where resolving fails.

    Raises ValueError on an embedded NUL, as `Path.resolve()` does, and it is
    resolved before anything is matched: the backend has always let that
    propagate, and deepagents answers it as `Error: ...` at `validate_path`.
    Matching first would pass a NUL path on to a backend call that raises
    where nothing catches it.
    """
    written = PurePosixPath(path)
    candidates = [written]
    try:
        resolved = PurePosixPath(Path(path).resolve().as_posix())
    except (OSError, RuntimeError):
        resolved = written
    if resolved != written:
        candidates.append(resolved)
    for candidate in candidates:
        relative = _relative_to_any(candidate, roots)
        if relative is not None:
            return relative
    return None


def _relative_to_any(path: PurePosixPath, roots: tuple[str, ...]) -> str | None:
    for root in roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def posix_root_spellings(project_root: Path) -> tuple[str, ...]:
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


__all__ = [
    "looks_windows_absolute",
    "posix_root_spellings",
    "strip_project_root",
    "virtual_to_host",
    "virtual_to_relative",
]
