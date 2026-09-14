"""Rudra's state directory is not project content (OPEN-117).

`.rudra/config.toml` may hold a literal `api_key` -- OPEN-6's decision, and
`committed_api_key_notice` warns when it does. `.rudra/` is an ordinary
subdirectory of the project the backend is rooted at, so before this module
every agent's `read_file`, `ls`, `glob` and `grep` reached it, and
`grep("api_key", "/")` returned the key line without the model ever naming
`.rudra`. Read by a model, a key goes to that role's provider -- which need
not be the provider it belongs to -- and from there into `facts.json`,
`AGENTS.md` or the palace, files `.rudra/.gitignore` calls safe to commit.

WHY THE BACKEND, AND NOT A TOOL MIDDLEWARE
------------------------------------------
A middleware can refuse `read_file /.rudra/config.toml`. It cannot refuse
`grep "api_key" /`, which names no state path: it would have to parse and
rewrite formatted tool output. The backend returns structured results
(`LsResult.entries`, `GrepResult.matches`), and it is the one object every
agent, every current file tool and any future one shares. Reads are not
gated and must not be (`permissions/floor.py`): that would prompt on every
`read_file` a run makes. Writes are the floor's job -- `rudra-state` there.

WHY A SUBCLASS, AND NOT A WRAPPER
---------------------------------
deepagents asks what the default backend IS, not only what it can do:
`isinstance(backend.default, SandboxBackendProtocol)` decides whether
`execute` exists (`middleware/filesystem.py:1435`), and
`isinstance(backend.default, LocalShellBackend)` decides whether the model
is told the host path behind each route (`_route_host_path_prompt`,
`middleware/filesystem.py:1360`). A delegating wrapper passes the first and
fails the second, which silently rewrites the execute tool's prompt -- the
first version of this fix did exactly that, and
`tests/test_backend_wiring.py` caught it. Subclassing keeps every such
check, every attribute (`cwd`, `virtual_mode`) and every method this module
does not override, byte for byte.

WHAT IS HIDDEN, AND WHAT IS NOT
-------------------------------
`read` of a state path answers exactly what a missing file answers, and
`ls` of one what a missing directory answers, so the model is told nothing
it could go looking for. `ls`, `glob` and `grep` drop every entry under a
state directory, including when called on `/`.

`download_files` is deliberately NOT filtered. deepagents' MemoryMiddleware
loads `.rudra/AGENTS.md` through it (`middleware/memory.py:295`) and no file
tool calls it, so filtering it would take the planner's memory away and
hide nothing from a model. `write`, `edit`, `delete` and `upload_files` are
not touched either: the floor refuses the tool calls, and `floor_disable =
["rudra-state"]` must be able to lift that -- a refusal here would make the
config key inert, which is worse than no key. `execute` is not filtered and
cannot be: a shell reads any file the user can (OPEN-117 §5.4).

WHICH PATH IS STATE
-------------------
The argument a model typed is resolved the way the backend will resolve it
(`virtual_to_relative`, CR-B4) and then the way the disk will: `/./.rudra`,
`src/../.rudra`, `\\.rudra\\x`, the host path spelled out, `/.RUDRA/x` on a
case-insensitive filesystem and a symlink into `.rudra` all name the same
directory, and a string-prefix check misses every one of them (OPEN-52's
shape). Result entries need less: measured against the pinned backend, they
come back as virtual paths of the RESOLVED file -- `ls` of a symlink to
`.rudra` lists `/.rudra/config.toml` -- so their segments are enough, and
that avoids a `realpath` per entry on a glob that returns twenty thousand.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.backends.protocol import GlobResult, GrepResult, LsResult, ReadResult

from rudra.compat.virtual_paths import virtual_to_relative
from rudra.state.paths import is_rudra_state


def names_rudra_state(path: str | None, project_root: Path) -> bool:
    """Does this model-written path land inside a Rudra state directory?

    None and the empty string are "the whole project", which is not state.
    """
    if not path:
        return False
    relative = virtual_to_relative(path, project_root)
    if relative is None:
        return False
    normalised = posixpath.normpath(relative)
    if is_rudra_state(PurePosixPath(normalised)):
        return True
    # A symlink inside the project can point at `.rudra` under any name.
    root = Path(project_root)
    try:
        real = Path(os.path.realpath(root / normalised))
        return is_rudra_state(real.relative_to(os.path.realpath(root)))
    except (ValueError, OSError):
        return False


def _listed_as_state(path: str) -> bool:
    """A result entry's virtual path, already resolved by the backend."""
    return is_rudra_state(PurePosixPath(posixpath.normpath(path.lstrip("/") or ".")))


def _without_state_entries(result: LsResult) -> LsResult:
    if not result.entries:
        return result
    return replace(
        result, entries=[e for e in result.entries if not _listed_as_state(e.get("path", ""))]
    )


def _without_state_matches(result: Any) -> Any:
    """For a GrepResult or a GlobResult, which share `matches`."""
    if not result.matches:
        return result
    return replace(
        result, matches=[m for m in result.matches if not _listed_as_state(m.get("path", ""))]
    )


def _cap_may_have_hidden_matches(
    raw: GrepResult, visible: GrepResult, max_count: int | None
) -> bool:
    if max_count is None or not raw.matches:
        return False
    removed = len(raw.matches) != len(visible.matches or [])
    return removed and (bool(raw.truncated) or len(raw.matches) >= max_count)


def _capped(result: GrepResult, max_count: int | None) -> GrepResult:
    if max_count is None or result.matches is None or len(result.matches) <= max_count:
        return result
    return replace(result, matches=result.matches[:max_count], truncated=True)


class _HidesRudraState:
    """The read side of a filesystem backend, with `.rudra/` taken out.

    A mixin placed BEFORE the deepagents class, so `super()` is that class.
    The async overrides check and filter too, rather than trusting the
    protocol's default to route through the sync method: an upstream release
    that gives `aread` its own body would otherwise reopen this silently.
    Filtering twice is idempotent.
    """

    cwd: Path

    def _hidden(self, path: str | None) -> bool:
        return names_rudra_state(path, self.cwd)

    # -- ls --------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        if self._hidden(path):
            return LsResult(error=f"Path '{path}': path_not_found")
        return _without_state_entries(super().ls(path))  # type: ignore[misc]

    async def als(self, path: str) -> LsResult:
        if self._hidden(path):
            return LsResult(error=f"Path '{path}': path_not_found")
        return _without_state_entries(await super().als(path))  # type: ignore[misc]

    # -- read ------------------------------------------------------------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if self._hidden(file_path):
            return ReadResult(error=f"File '{file_path}' not found")
        return super().read(file_path, offset=offset, limit=limit)  # type: ignore[misc]

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if self._hidden(file_path):
            return ReadResult(error=f"File '{file_path}' not found")
        return await super().aread(file_path, offset=offset, limit=limit)  # type: ignore[misc]

    # -- grep ------------------------------------------------------------
    #
    # The cap is spent on what the agent can see. `.rudra/run/logs/` holds
    # every line a run printed, so a capped search whose matches were mostly
    # state would answer "nothing" to a real question. When filtering removed
    # something from a result the cap may have cut short, search again
    # uncapped -- the rare case pays, the common one does not.

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        **kwargs: Any,
    ) -> GrepResult:
        if self._hidden(path):
            return GrepResult(matches=[])
        search = super().grep  # type: ignore[misc]
        raw = search(pattern, path, glob, max_count=max_count, **kwargs)
        visible = _without_state_matches(raw)
        if _cap_may_have_hidden_matches(raw, visible, max_count):
            visible = _without_state_matches(search(pattern, path, glob, **kwargs))
        return _capped(visible, max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        **kwargs: Any,
    ) -> GrepResult:
        if self._hidden(path):
            return GrepResult(matches=[])
        search = super().agrep  # type: ignore[misc]
        raw = await search(pattern, path, glob, max_count=max_count, **kwargs)
        visible = _without_state_matches(raw)
        if _cap_may_have_hidden_matches(raw, visible, max_count):
            visible = _without_state_matches(await search(pattern, path, glob, **kwargs))
        return _capped(visible, max_count)

    # -- glob ------------------------------------------------------------

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if self._hidden(path):
            return GlobResult(matches=[])
        return _without_state_matches(super().glob(pattern, path))  # type: ignore[misc]

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        if self._hidden(path):
            return GlobResult(matches=[])
        return _without_state_matches(await super().aglob(pattern, path))  # type: ignore[misc]


class HiddenStateFilesystemBackend(_HidesRudraState, FilesystemBackend):
    """`FilesystemBackend` for the project root, `[tools] shell = false`."""


class HiddenStateLocalShellBackend(_HidesRudraState, LocalShellBackend):
    """`LocalShellBackend` for the project root -- the default."""


__all__ = [
    "HiddenStateFilesystemBackend",
    "HiddenStateLocalShellBackend",
    "names_rudra_state",
]
