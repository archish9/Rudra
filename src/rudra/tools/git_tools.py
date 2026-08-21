"""The one git tool the model sees.

The model already has gated `execute`, so it can run `git status` today. A
new tool only earns its place by parsing output or bounding it -- otherwise
it is a second spelling of an existing capability, paid for in schema tokens
inside a 32B window (D6). An uncapped diff is exactly what raw `execute`
does badly, so `git_diff` is the one that ships (Step 8 spec §5.1).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from langchain_core.tools import tool
from rich.console import Console

from rudra.git import core
from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated

MAX_DIFF_LINES = 400

# How many untracked files to name before summarising the rest. Bounded for
# the reason the diff is: this tool exists to fit a 32B window (D6).
MAX_UNTRACKED_LISTED = 40


def _within_root(project_path: Path, candidate: str) -> bool:
    """Is this path inside the project?

    Resolved before comparison, so `../../elsewhere` and a symlink pointing
    out both land on their real location -- the same reason
    `PermissionEngine._resolve` resolves before matching.
    """
    root = Path(project_path).resolve()
    given = Path(candidate)
    target = given.resolve() if given.is_absolute() else (root / given).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _untracked_note(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> str:
    """The new files `git diff` cannot show, named (A1.68).

    `git diff` reports changes to files git already tracks, so on a fresh
    repository -- where every file the run wrote is untracked -- it is
    empty and the honest answer is not "nothing changed". Measured: a Step
    9c acceptance run wrote parser.py and test_parser.py into a new repo
    and the reviewer reported no changes, while an edit to a committed
    file produced a real finding.

    Naming the files rather than diffing them is deliberate. `git diff
    --no-index /dev/null <file>` would print each new file in full, which
    is the uncapped output this tool exists to avoid, and `git add -N`
    would make the reviewer -- which cannot write -- mutate the index. The
    model has read_file for anything it wants to see.
    """
    entries = core.status(project_path, gate=gate, console=console, cfg=cfg, all_untracked=True)
    if entries is None:
        return "git status failed, so untracked files could not be listed."
    paths = [
        entry.path
        for entry in entries
        if entry.path and "?" in entry.index + entry.worktree and not _is_noise(entry.path)
    ]
    if not paths:
        return ""

    shown = sorted(paths)[:MAX_UNTRACKED_LISTED]
    listed = "\n".join(f"  {path}" for path in shown)
    note = f"Untracked files (new, so no diff exists yet):\n{listed}"
    if len(paths) > len(shown):
        note += f"\n  ... and {len(paths) - len(shown)} more"
    return note


def _is_noise(path: str) -> bool:
    """Build output and Rudra's own state, which are nobody's work.

    Shares `SKIP_DIRS` with the gate and the loop, so all three agree
    about what a build directory is (A1.66).
    """
    from rudra.verify.stubs import SKIP_DIRS

    return any(part in SKIP_DIRS for part in PurePosixPath(path).parts)


def create_git_tools(project_path: Path, *, gate: Any, console: Console, cfg: Any) -> list:
    """The git tools for one run. Currently exactly one."""

    @tool
    def git_diff(path: str = "", staged: bool = False) -> str:
        """Show what has changed in the working tree, as a unified diff.

        Use this to review your own edits before deciding what to do next.
        Output is capped, so a very large diff is truncated with a note.

        Args:
            path: Limit the diff to one file, relative to the project root.
                  Leave empty for every change.
            staged: Show staged changes instead of unstaged ones.

        Returns:
            The diff, or a sentence explaining why there is none.
        """
        common = {"gate": gate, "console": console, "cfg": cfg}

        if not core.is_repo(project_path, **common):
            return "This project is not a git repository, so there is no diff to show."

        target = path.strip() or None

        if target is not None and not _within_root(project_path, target):
            # The one model-supplied value that could otherwise ride the
            # read-only bypass into an arbitrary repository. Route the whole
            # call through the gate instead of deciding it here: refusing
            # outright would make this tool a second policy authority, and
            # the engine is the only one (Step 8 spec §4.4).
            argv = ["git", "diff"]
            if staged:
                argv.append("--staged")
            argv.extend(["--", target])

            result = run_gated(
                argv,
                cwd=project_path,
                gate=gate,
                console=console,
                timeout=core.GIT_TIMEOUT_SECONDS,
                env=scrubbed_env(cfg),
                read_only=False,
            )
            if result.denied:
                return (
                    f"Reading a diff outside the project was not permitted: "
                    f"{result.denial_reason}. Do not retry this call."
                )
            # Capped like the in-root branch. This is the whole reason the
            # tool exists rather than the model calling `git diff` through
            # execute (CR-B8).
            return core.cap_diff(result.stdout, MAX_DIFF_LINES) or "No changes."

        text = core.diff(
            project_path, path=target, staged=staged, max_lines=MAX_DIFF_LINES, **common
        )

        # A path-limited or staged request asked a narrow question; answer
        # that one. The untracked note belongs to the broad "what changed?"
        # call, which is the one the reviewer makes.
        if target is not None or staged:
            return text if text.strip() else "No changes."

        untracked = _untracked_note(project_path, **common)
        if text.strip():
            return f"{text}\n\n{untracked}" if untracked else text
        if untracked:
            return untracked
        return "No changes in the working tree."

    return [git_diff]


__all__ = ["MAX_DIFF_LINES", "create_git_tools"]
