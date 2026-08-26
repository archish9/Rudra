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

``validate_path`` is imported by name in THREE modules of deepagents 0.7.4:
``backends.utils`` (where it is defined), ``middleware.filesystem``, and
``middleware._fs_interrupt``.  Each does
``from deepagents.backends.utils import validate_path``, so tool closures
look up the name in their own module's globals at call time and patching
one is not enough.  All three are rewritten below.

This docstring used to say the function was called in one module only,
"confirmed by grep across the full deepagents package" -- false for the
pinned version, and the kind of claim a future maintainer trusts rather
than re-checks.  ``_fs_interrupt`` is reached only when ``permissions=`` is
passed, which Rudra deliberately never does (U.7/A1.46), so patching it
changes nothing today; it is patched anyway so that the day U.7 reopens,
this file is not quietly half-applied (CR-D8).

NORMALIZATION LAYERS
--------------------
1. Windows absolute path (C:\\...) → strip drive + project root → relative
2. POSIX absolute path:
   a. Strip actual project root prefix (matches real machine path)
   b. Strip known sandbox prefix (/testbed/, /workspace/, etc.)
   c. Plan-aware suffix matching against PLAN.md filenames
   d. Last resort: bogus-dir-aware basename stripping, and ONLY for a path
      whose leading component is itself a bogus dir (``/home/...``,
      ``/var/...``).  A path that reaches (d) without that shape is left
      alone: it is far likelier to be a real project layout than a
      hallucination, and trimming it wrote files to the wrong place in
      silence (OPEN-12).

   (a) precedes (b) deliberately — see the ordering note in ``_normalize``.

SANDBOX PREFIXES ARE OPT-IN
---------------------------
Layer (b) runs only under ``[compat] sandbox_paths``, off by default, which
is the same flag gating the identical stripping in
``middleware/fix_write_params.py`` and for the same reason: the list holds
``/src/``, ``/app/``, ``/code/``, ``/tmp/`` and ``/root/``, every one of
which is an ordinary directory a project really has.

It was unconditional until 2026-08-26, and TODO.md OPEN-31 is what that
cost. Because ``virtual_mode=True`` makes a leading ``/`` mean the project
root, ``/src/models.py`` is the *correct* spelling of ``<project>/src/models.py``
-- and it was being rewritten to ``/models.py``. Worse, the prefixes carry a
trailing slash, so ``ls("/src")`` never matched while
``read_file("/src/models.py")`` always did: the listing tool kept returning
paths the reading tool then denied, by a different name, and every agent in
run ``d104fd13d9cc`` looped until its failure guard fired.

Under virtual-root semantics the layer rescues nothing by default.
``/testbed/app/main.py`` simply means ``<project>/testbed/app/main.py``,
which does not exist -- so the model gets an error naming the path it asked
for and can correct itself, instead of a silent rewrite naming one it did
not.
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
    route_prefixes: Sequence[str] = (),
    strip_sandbox_prefixes: bool = False,
) -> None:
    """Patch deepagents to accept absolute paths from LLMs on any OS.

    Handles:
    - Windows absolute paths  e.g. ``C:\\project\\src\\models.py``
    - POSIX absolute paths     e.g. ``/home/user/project/src/models.py``
    - Sandbox prefixes         e.g. ``/testbed/app/main.py`` (opt-in)
    - Unknown absolute paths   e.g. ``/some/random/prefix/requirements.txt``

    Args:
        project_root: Absolute path to the project root directory.
        plan_path: Optional path to .rudra/run/PLAN.md for plan-aware suffix matching.
                   When provided, planned filenames are used to resolve unknown paths.
        strip_sandbox_prefixes: ``[compat] sandbox_paths``. Off by default, and
                   the default is the correct setting -- see the SANDBOX
                   PREFIXES ARE OPT-IN section of the module docstring.

    Safe to call multiple times (always wraps the original, never re-wraps).
    """
    from rudra.compat.version_guard import (
        require_deepagents_attr,
        require_deepagents_version,
    )

    # This function rewrites a deepagents internal in three places. Fail
    # here, naming the ledger item, rather than deep inside a tool call
    # later -- and fail if a site MOVES, which is what pins the count.
    require_deepagents_version("U.4")
    require_deepagents_attr("deepagents.backends.utils", "validate_path", "U.4")
    require_deepagents_attr("deepagents.middleware.filesystem", "validate_path", "U.4")
    require_deepagents_attr("deepagents.middleware._fs_interrupt", "validate_path", "U.4")

    import deepagents.backends.utils as _utils
    import deepagents.middleware._fs_interrupt as _fs_int
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
        # --- Step 0: real backend routes are not hallucinations (A1.79) ---
        #
        # Everything below exists to rescue absolute paths the model
        # invented. A CompositeBackend route is the opposite: a real mount
        # point the model was *told* about, and one it reads back out of an
        # `ls` listing. Step 2d would keep only the last two segments of
        # "/skills/active/brainstorming/SKILL.md" and hand back
        # "/brainstorming/SKILL.md", so a live run could list the skills and
        # never read one. `/artifacts/` escaped that only by being three
        # segments deep -- luck, not design.
        for prefix in route_prefixes:
            if path.startswith(prefix):
                return path

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
        # Gated on `[compat] sandbox_paths` since OPEN-31: `/src/`, `/app/`,
        # `/code/`, `/tmp/` and `/root/` are ordinary project directories,
        # and a VIRTUAL absolute path naming one is the common case rather
        # than the hallucination this list was built for.
        sandbox_stripped: str | None = None
        if strip_sandbox_prefixes:
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

        # Step 2d: Last resort — strip "/", and trim only a path that is
        # SHAPED like a container's own directory tree.
        #
        # The trimming below used to run on every absolute path deeper than
        # three components, which is not a hallucination signal -- it is an
        # ordinary project layout. `/todoapp/src/models/todo.py` came back as
        # `/models/todo.py` and `/.rudra/run/transcripts/x.jsonl` as
        # `/transcripts/x.jsonl`: the write succeeded, at the wrong path, with
        # nothing reporting a discrepancy, and the approval panel had already
        # previewed the untrimmed one (OPEN-12).
        #
        # Every earlier step matches against something knowable -- the real
        # root (2a), a listed sandbox prefix (2b), PLAN.md (2c). This one
        # matched against nothing, so the leading component now has to be a
        # directory a container owns and a project root is not: `/home/...`,
        # `/var/...`, `/workspace/...`. Anything else is returned as written,
        # which deepagents reads as project-relative -- the correct answer,
        # and the one `virtual_mode=True` is built on.
        path = pp.relative_to("/").as_posix()
        path_parts = path.split("/")
        if len(path_parts) > 3 and path_parts[0] in BOGUS_DIRS:
            second_to_last = path_parts[-2]
            if second_to_last in BOGUS_DIRS:
                path = path_parts[-1]
            else:
                path = "/".join(path_parts[-2:])

        return original(path, allowed_prefixes=allowed_prefixes)

    _utils.validate_path = _normalize
    _fs_mw.validate_path = _normalize
    _fs_int.validate_path = _normalize
