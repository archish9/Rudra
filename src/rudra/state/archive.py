"""The run archive: a run's evidence, kept where the project's deletion cannot reach it (OPEN-68).

Rudra's instruments -- `usage.json`, `debug-<id>.jsonl`, the ledger and the
transcript -- are all written under `<project>/.rudra/run/`, which D15 calls
volatile and which lives in whatever directory the run was pointed at. On
2026-09-01 the entire pool of run evidence this ledger had been sized
against (`run6` ... `run14`) was found gone: the test projects were
throwaway sibling directories and had been deleted. Six items -- OPEN-39
Phase 2, OPEN-46, OPEN-52, OPEN-55, OPEN-56 and OPEN-60 -- had been argued
from numbers nothing could recompute, and OPEN-46 §6 was blocked outright
because its whole content is a number.

**Why the archive is at the user level and not a durable subtree of
`.rudra/`.** The obvious fix -- promote these files from `run/` (volatile)
to a durable sibling -- fails the thing that actually happened. What was
deleted was the *project*, and `.rudra/` went with it whether its subtree
was durable or not. A copy only survives if it leaves the project, so this
writes to `$XDG_STATE_HOME/rudra/runs/`, which is the move
`config/layers.py::user_toml_path` makes for `$XDG_CONFIG_HOME` and
`skills/cache.py::_cache_home` makes for `$XDG_CACHE_HOME`.

**Automatic, because a manual step is the step that gets skipped.** That is
not a guess: the lost pool was lost exactly that way, and OPEN-68 says in
so many words not to fix it by adding a sentence to a checklist.

Layout, one directory per run:

    $XDG_STATE_HOME/rudra/runs/<project-slug>/<run-id>/
        meta.json                  project path, run id, version, file list
        usage.json                 the run's tally (loop/engine.py)
        ledger.json                what the run was asked to do and did
        debug-<run-id>.jsonl       the complete record (trace/debug.py)
        transcript.jsonl           the readable record (trace/transcript.py)

`<project-slug>` is the directory's name plus a digest of its resolved
path, because two projects called `app` are one name and two projects.

**The archive is a PREFIX of what the project holds, not a copy of it**
(OPEN-69). `usage.json`, `ledger.json` and the transcript are complete and
closed before `close()` runs, so those are byte-identical. The debug log is
still OPEN -- the checkpointer and the MCP client shut down after this and
either may log -- so identity is not a contract this code can keep. What it
keeps instead is that every archived line is in the project's file, in
order, and that archiving happens LAST so the prefix is as long as it can
be.

**Nothing here may raise.** `write_usage_log`'s rule (loop/engine.py, C7.5):
a run that finished its work must not be reported failed because its own
bookkeeping could not be written. Every entry point returns `None` or an
empty list on failure.

**Retention deletes whole runs, never part of one.** The debug log is
uncapped by design, so this is bounded twice -- newest `KEEP_RUNS` runs, and
`MAX_PROJECT_BYTES` per project, whichever binds first. Truncating a copied
file would leave an instrument that reads short rather than one that is
plainly absent, and a wrong number is worse than a missing one -- which is
the lesson OPEN-46's `exhaustions` was filed on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("rudra.state.archive")

META_NAME = "meta.json"
"""What a session holding only this directory reads first."""

META_VERSION = 1
"""Bumped when the layout changes shape, so a reader can say so rather
than mis-parse. The scripts in `docs/superpowers/plans/` read it."""

KEEP_RUNS = 20
"""Archived runs retained per project, newest first. Matches
`trace/debug.py::KEEP_RUNS` and `trace/transcript.py`: this mirrors the
project's own retention one level up, where nothing deletes it by accident."""

MAX_PROJECT_BYTES = 2 * 1024**3
"""Per project, whichever binds first with `KEEP_RUNS`. The debug log has
uncapped payloads, so a count alone does not bound a home directory: run14
was 233 model calls of them. Whole runs are dropped to get under it."""

_SLUG_MAX = 40
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def archive_home(explicit: Path | None = None) -> Path:
    """Where archives live: `$XDG_STATE_HOME/rudra/runs`, else `~/.local/state/...`.

    Spelled the way `config/layers.py::user_toml_path` and
    `skills/cache.py::_cache_home` spell theirs -- three XDG roots, one
    house style. `explicit` wins outright, which is what makes every test
    here run against a tmp_path instead of the developer's home.
    """
    if explicit is not None:
        return Path(explicit)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "rudra" / "runs"


def project_slug(project_path: Path) -> str:
    """A directory name for one project: readable name, then path digest.

    The digest is not decoration. Every measured run this ledger has ever
    made lived in a sibling directory named `test-rudra-runN`, and the next
    one will too; two projects sharing a name must not share an archive.
    """
    resolved = Path(project_path).resolve()
    name = _UNSAFE.sub("-", resolved.name).strip("-.") or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{name[:_SLUG_MAX]}-{digest}"


def run_dir(project_path: Path, session_id: str, *, home: Path | None = None) -> Path:
    """Where one run's archive goes. Creates nothing (`rudra_paths`'s rule)."""
    return archive_home(home) / project_slug(project_path) / session_id


def _directory_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def prune_runs(
    project_dir: Path,
    keep: int = KEEP_RUNS,
    *,
    max_bytes: int = MAX_PROJECT_BYTES,
) -> list[Path]:
    """Keep the newest runs that fit both bounds; delete the rest. Returns the kept.

    Sorted by mtime and then by name, so two runs archived inside one
    filesystem timestamp still order deterministically rather than by
    whatever `iterdir` happens to yield.

    Only directories are considered and only children of `project_dir` are
    ever removed: this is the one function here that deletes, and it is
    scoped by construction rather than by a pattern that could widen --
    `prune_debug_logs`' `debug-*` glob exists for the same reason.
    """
    project_dir = Path(project_dir)
    try:
        runs = sorted(
            (path for path in project_dir.iterdir() if path.is_dir()),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
    except OSError:
        return []

    kept: list[Path] = []
    used = 0
    for index, run in enumerate(runs):
        size = _directory_bytes(run)
        if index < keep and (used + size <= max_bytes or not kept):
            kept.append(run)
            used += size
            continue
        try:
            shutil.rmtree(run)
        except OSError:
            # A run we could not delete is still a run that is there, so it
            # is reported as kept rather than silently dropped from the tally.
            kept.append(run)
            used += size
    return kept


def model_facts(cfg: Any) -> dict[str, dict[str, Any]]:
    """Which model each role ran, for the archive's `meta.json`.

    The archive is read by a session holding only the archive, and every
    pooled rate this project has computed carries the caveat *one endpoint,
    one model throughout* -- a claim nothing in `usage.json` can support.
    `config.toml` is deliberately NOT copied instead: it may hold a literal
    `api_key` (OPEN-6), and an archive is a place a user copies around.

    Three fields, and the omission is the point: `api_key` and
    `api_key_env` are never recorded. `base_url` is, because it is the
    endpoint half of that caveat and `rudra config list` already prints it.
    """
    facts: dict[str, dict[str, Any]] = {}
    for role, model in getattr(cfg, "models", {}).items():
        facts[str(role)] = {
            "provider": getattr(model, "provider", None),
            "model": getattr(model, "model", None),
            "base_url": getattr(model, "base_url", None),
        }
    return facts


def env_facts(cfg: Any) -> dict[str, Any]:
    """What machine and what settings produced this run (OPEN-78).

    `meta.json` recorded the models and nothing else, so a log folder could
    not answer "which mode was this, was there a terminal, which versions"
    without a conversation -- or without reading a `config.toml` that lives
    outside the folder and that the user may have edited since. That is not
    hypothetical: the runs this was filed on were read against a config
    that had changed between them, and the only thing that caught it was an
    incidental `built model role=planner spec=openai:qwen3:32b` line that
    `llm/factory.py` happens to log.

    `interactive` is here because it decides more than it looks: it is
    whether `ask_user` is registered at all and whether the plan gate
    prompts or auto-approves (`main_agent`, `interactive`). A run that
    behaved unlike the reader's expectation usually differed here first.

    Recording the platform is not a `sys.platform` branch and does not
    conflict with CLAUDE.md 1.8 -- that rule is about behaviour branching
    on the host. A bug report saying which host it came from is the
    opposite: it is what makes a portability claim checkable at all.

    `model_facts`'s rule holds here too: **never `config.toml` itself**,
    which may carry a literal `api_key` (OPEN-6). Every value below is a
    setting, a version or a capability -- no secret has a path into it.
    """
    import platform
    import sys

    agent = getattr(cfg, "agent", None)
    permissions = getattr(cfg, "permissions", None)
    tools = getattr(cfg, "tools", None)

    try:
        from rudra.permissions import stdin_is_interactive

        interactive = bool(stdin_is_interactive())
    except Exception:  # noqa: BLE001 -- bookkeeping never ends a run
        interactive = None

    return {
        "mode": getattr(permissions, "mode", None),
        "interactive": interactive,
        "shell": getattr(tools, "shell", None),
        "shell_in_auto": getattr(tools, "shell_in_auto", None),
        "verbose": getattr(agent, "verbose", None),
        "debug_log": getattr(agent, "debug_log", None),
        "max_fix_attempts": getattr(agent, "max_fix_attempts", None),
        "max_questions": getattr(agent, "max_questions", None),
        # A `halts` line saying "over the 1200s limit" is only readable
        # against the limit that was in force, and it is configurable
        # (OPEN-91).
        "max_invocation_seconds": getattr(agent, "max_invocation_seconds", None),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "deepagents": _package_version("deepagents"),
        "executable": sys.executable,
    }


def _package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001 -- an absent package is a fact, not an error
        return None


def _sources(paths: Any, session_id: str) -> list[tuple[Path, str]]:
    """(file on disk, name in the archive) for every instrument, in read order.

    The transcript is renamed: in the project it is `<run-id>.jsonl`, which
    is a name that only means something beside the other runs. Inside a
    directory already named for the run, `transcript.jsonl` is the name that
    tells a reader what it is holding.
    """
    logs = Path(paths.logs)
    return [
        (logs / "usage.json", "usage.json"),
        (Path(paths.ledger_json), "ledger.json"),
        # Durable, unlike everything else here, and copied anyway (OPEN-79):
        # it is what the planner SETTLED, it is in every agent's prompt, and
        # it goes with the project when the project is deleted. A reader
        # holding only the archive otherwise cannot tell what the run
        # believed about the work.
        (Path(paths.facts_json), "facts.json"),
        (logs / f"debug-{session_id}.jsonl", f"debug-{session_id}.jsonl"),
        (Path(paths.transcripts) / f"{session_id}.jsonl", "transcript.jsonl"),
    ]


def archive_run(
    *,
    project_path: Path,
    session_id: str,
    paths: Any = None,
    models: dict[str, Any] | None = None,
    env: dict[str, Any] | None = None,
    home: Path | None = None,
    keep: int = KEEP_RUNS,
    max_bytes: int = MAX_PROJECT_BYTES,
) -> Path | None:
    """Copy this run's evidence out of the project. Returns where, or None.

    `paths` is the `RudraPaths` the run used; it is derived from
    `project_path` when absent, because `state/paths.py` is the only thing
    allowed to know that layout.

    `None` means nothing was archived, and it is a normal answer twice over:
    a run that left no instrument (`--plan`, or a REPL turn that wrote
    nothing) has nothing to copy, and a home that cannot be written to must
    not end a run that is otherwise fine.
    """
    from rudra.state.paths import rudra_paths

    try:
        project_path = Path(project_path)
        if paths is None:
            paths = rudra_paths(project_path)

        present = [
            (source, name) for source, name in _sources(paths, session_id) if source.is_file()
        ]
        if not present:
            # Before any mkdir: an empty run must leave no directory behind,
            # or the REPL -- a fresh agent and a fresh run id per turn --
            # fills the archive with folders holding one meta file.
            return None

        destination = run_dir(project_path, session_id, home=home)
        destination.mkdir(parents=True, exist_ok=True)
        # BEFORE the copy, and that is the whole of OPEN-69. This record goes
        # to the `rudra` logger tree, whose handler IS the debug log two lines
        # below is about to copy -- so logging the OUTCOME afterwards appends a
        # line to the file just copied, and the archive is short by exactly one
        # record on every run, forever. RUN #9's row 21 measured it.
        #
        # It is phrased as intent rather than outcome for the same reason:
        # "archiving" is true when written, "archived" would not be if the copy
        # then failed. The copy naming its own location is what a file read
        # alone wants anyway.
        LOGGER.debug("archiving run %s to %s", session_id, destination)

        copied: list[str] = []
        for source, name in present:
            try:
                shutil.copy2(source, destination / name)
            except OSError:
                # One unreadable instrument is a gap in this archive, not a
                # reason to abandon the three that are readable.
                continue
            copied.append(name)

        meta = {
            "meta_version": META_VERSION,
            "run_id": session_id,
            "project_path": str(project_path.resolve()),
            "project_slug": project_slug(project_path),
            "rudra_version": _version(),
            "archived_at": time.time(),
            "models": models or {},
            "env": env or {},
            "files": copied,
        }
        (destination / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")

        prune_runs(destination.parent, keep=keep, max_bytes=max_bytes)
        return destination
    except Exception:  # noqa: BLE001 -- bookkeeping never ends a run (C7.5)
        return None


def _version() -> str:
    try:
        import rudra

        return str(rudra.__version__)
    except Exception:  # noqa: BLE001 -- the archive is worth more than the field
        return "unknown"


__all__ = [
    "KEEP_RUNS",
    "MAX_PROJECT_BYTES",
    "META_NAME",
    "META_VERSION",
    "archive_home",
    "archive_run",
    "env_facts",
    "model_facts",
    "project_slug",
    "prune_runs",
    "run_dir",
]
