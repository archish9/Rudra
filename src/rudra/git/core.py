"""Git, as a Python API.

The orchestrator calls these directly (no model in the loop) and Step 9's
reviewer will too. Exactly one of them is exposed to the model, as a tool --
see git/tools.py -- because the model already has gated `execute`, and a new
tool only earns its place by parsing output or bounding it (Step 8 spec §5.1).

Read-only subcommands run with `read_only=True`. `auto_branch` alone fires
three of them before the planner starts, and prompting for
`git rev-parse --is-inside-work-tree` on every run would make the gate an
annoyance rather than a control. This is consistent with policy already in
force: reads are never gated (permissions/rules.py) and are never a floor
violation (permissions/floor.py). The set is fixed here, in Rudra's own
code -- no model-supplied argv ever enters it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import CommandResult, run_gated
from rudra.state.paths import RUDRA_DIR_NAME

# git subcommands that only read. Rudra composes every one of these itself.
READ_ONLY_SUBCOMMANDS = frozenset({"rev-parse", "status", "log", "diff", "branch"})

# Generous, but not unbounded: `git log` over a large history is slow, and a
# hung git is still a hang. Not user-configurable -- [tools] test_timeout is
# for test suites, whose runtime genuinely varies by project.
GIT_TIMEOUT_SECONDS = 60

# NUL, so a commit subject containing a colon does not split into two fields.
_LOG_FORMAT = "%H%x00%an%x00%s"

_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")

BRANCH_PREFIX = "rudra/"

# Enough branches for any real run; needing more is not a naming collision,
# it is a sign auto_branch is being used wrongly.
_MAX_SUFFIX = 50


@dataclass(frozen=True)
class FileStatus:
    """One line of `git status --porcelain`."""

    index: str
    worktree: str
    path: str


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    subject: str


@dataclass(frozen=True)
class BranchOutcome:
    """What auto_branch did, or the reason it did nothing.

    Skipping is a normal outcome, not an error. Exactly one field is set.
    """

    branch: str | None
    skipped_reason: str | None


def _parse_status(text: str) -> list[FileStatus]:
    """Porcelain lines to records. Shared by `status` and `is_clean`.

    The format is two status characters, a space, then the path -- so the
    path is everything from index 3 onward, and splitting on whitespace
    would lose any filename containing a space. A rename prints
    `old -> new`; the new name is what a caller acts on.
    """
    entries: list[FileStatus] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append(FileStatus(index=line[0], worktree=line[1], path=path.strip('"')))
    return entries


def _is_users_change(path: str) -> bool:
    """Is this the user's work, rather than Rudra's own bookkeeping?

    Matched as a path component, not a prefix, so a file genuinely named
    `.rudra-notes.md` still counts as the user's.
    """
    return not (path == RUDRA_DIR_NAME or path.startswith(f"{RUDRA_DIR_NAME}/"))


def _run(
    project_path: Path,
    argv: list[str],
    *,
    gate: Any,
    console: Console,
    cfg: Any,
) -> CommandResult:
    return run_gated(
        ["git", *argv],
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=GIT_TIMEOUT_SECONDS,
        env=scrubbed_env(cfg),
        read_only=bool(argv) and argv[0] in READ_ONLY_SUBCOMMANDS,
    )


def is_repo(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> bool:
    result = _run(
        project_path, ["rev-parse", "--is-inside-work-tree"], gate=gate, console=console, cfg=cfg
    )
    return result.ok and result.stdout.strip() == "true"


def current_branch(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> str | None:
    """The branch name, or None when HEAD is detached.

    `rev-parse --abbrev-ref HEAD` prints the literal string "HEAD" for a
    detached head, which is the case auto_branch must refuse.
    """
    result = _run(
        project_path, ["rev-parse", "--abbrev-ref", "HEAD"], gate=gate, console=console, cfg=cfg
    )
    if not result.ok:
        return None
    name = result.stdout.strip()
    return None if name in ("", "HEAD") else name


def is_clean(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> bool:
    """Does the USER have uncommitted work?

    Rudra's own `.rudra/` is excluded, and that exclusion is load-bearing
    rather than tidy. `ensure_layout` creates the directory before the run
    starts, and D15 deliberately leaves `config.toml`/`AGENTS.md`/
    `project.json` untracked -- Rudra never edits the project's own
    .gitignore. So git reports `?? .rudra/` in a repository the user has not
    touched, and without this filter `auto_branch` would refuse to branch on
    essentially every real repository (TODO.md A1.53).

    Everything else still counts. Untracked build output is the user's
    problem to resolve before branching, exactly as before.
    """
    result = _run(project_path, ["status", "--porcelain"], gate=gate, console=console, cfg=cfg)
    if not result.ok:
        # Not a repository, or git refused. "Clean" would be a lie, and the
        # caller must not read a failure as permission to branch.
        return False
    return not any(_is_users_change(entry.path) for entry in _parse_status(result.stdout))


def status(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list[FileStatus]:
    """Parsed `git status --porcelain`, including Rudra's own `.rudra/`.

    Unfiltered on purpose: this reports what git reports. `is_clean` is the
    one that asks the narrower question, "does the user have uncommitted
    work", and filters accordingly (A1.53).
    """
    result = _run(project_path, ["status", "--porcelain"], gate=gate, console=console, cfg=cfg)
    return _parse_status(result.stdout) if result.ok else []


def diff(
    project_path: Path,
    *,
    gate: Any,
    console: Console,
    cfg: Any,
    path: str | None = None,
    staged: bool = False,
    max_lines: int = 400,
) -> str:
    """The working-tree diff, capped.

    Capping is the whole reason this exists rather than leaving `git diff`
    to raw `execute`: an uncapped diff is the easiest way to fill a 32B
    context (D6) with something the model cannot act on.
    """
    argv = ["diff"]
    if staged:
        argv.append("--staged")
    if path:
        argv.extend(["--", path])

    result = _run(project_path, argv, gate=gate, console=console, cfg=cfg)
    if result.denied:
        return f"git diff was not permitted: {result.denial_reason}"
    if not result.ok:
        return result.stderr.strip() or "git diff failed."

    lines = result.stdout.splitlines()
    if len(lines) <= max_lines:
        return result.stdout
    kept = "\n".join(lines[:max_lines])
    return f"{kept}\n\n[diff truncated: {len(lines) - max_lines} more lines]"


def log(
    project_path: Path, *, gate: Any, console: Console, cfg: Any, count: int = 10
) -> list[Commit]:
    result = _run(
        project_path,
        ["log", f"-n{count}", f"--format={_LOG_FORMAT}"],
        gate=gate,
        console=console,
        cfg=cfg,
    )
    if not result.ok:
        return []

    commits: list[Commit] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00")
        if len(parts) == 3:
            commits.append(Commit(sha=parts[0], author=parts[1], subject=parts[2]))
    return commits


def branch_exists(project_path: Path, name: str, *, gate: Any, console: Console, cfg: Any) -> bool:
    result = _run(
        project_path,
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        gate=gate,
        console=console,
        cfg=cfg,
    )
    return result.ok


def branch_slug(task: str, max_length: int = 40) -> str:
    """A git-ref-safe slug for a task description.

    Trailing separators are stripped AFTER capping as well as before: a cap
    landing mid-separator would otherwise produce `rudra/foo-`, and a ref
    component ending that way is rejected.
    """
    slug = _SLUG_SEPARATORS.sub("-", task.lower()).strip("-")
    return slug[:max_length].strip("-") or "task"


def auto_branch(
    project_path: Path, task: str, *, gate: Any, console: Console, cfg: Any
) -> BranchOutcome:
    """Create `rudra/<slug>` before a run, when it is safe to.

    Opt-in via `[tools] auto_branch` and off by default (Step 8 spec S8.3):
    this mutates the user's repository before the model has done anything.

    Every precondition failure returns a reason and changes nothing. It
    never raises and never fails the run -- a user who cannot get a branch
    still wants their task done, on the branch they are already on.

    The clean-tree precondition is not fussiness: `git checkout -b` carries
    uncommitted changes onto the new branch, and fails outright where it
    would clobber. A dirty tree is the case most likely to surprise someone
    who ran Rudra in a repository they care about.
    """
    common = {"gate": gate, "console": console, "cfg": cfg}

    if not is_repo(project_path, **common):
        return BranchOutcome(None, "this is not a git repository")
    if current_branch(project_path, **common) is None:
        return BranchOutcome(None, "HEAD is detached")
    if not is_clean(project_path, **common):
        return BranchOutcome(None, "the working tree has uncommitted changes")

    base = f"{BRANCH_PREFIX}{branch_slug(task)}"
    name = base
    for suffix in range(2, _MAX_SUFFIX + 1):
        if not branch_exists(project_path, name, **common):
            break
        name = f"{base}-{suffix}"
    else:
        return BranchOutcome(None, f"every name from {base} to {base}-{_MAX_SUFFIX} is taken")

    result = _run(project_path, ["checkout", "-b", name], **common)
    if result.denied:
        return BranchOutcome(None, f"creating the branch was not permitted: {result.denial_reason}")
    if not result.ok:
        return BranchOutcome(None, result.stderr.strip() or "git checkout -b failed")
    return BranchOutcome(name, None)


__all__ = [
    "BRANCH_PREFIX",
    "GIT_TIMEOUT_SECONDS",
    "READ_ONLY_SUBCOMMANDS",
    "BranchOutcome",
    "Commit",
    "FileStatus",
    "auto_branch",
    "branch_exists",
    "branch_slug",
    "current_branch",
    "diff",
    "is_clean",
    "is_repo",
    "log",
    "status",
]
