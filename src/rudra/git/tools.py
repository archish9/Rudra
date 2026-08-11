"""The one git tool the model sees.

The model already has gated `execute`, so it can run `git status` today. A
new tool only earns its place by parsing output or bounding it -- otherwise
it is a second spelling of an existing capability, paid for in schema tokens
inside a 32B window (D6). An uncapped diff is exactly what raw `execute`
does badly, so `git_diff` is the one that ships (Step 8 spec §5.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from rich.console import Console

from rudra.git import core
from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated

MAX_DIFF_LINES = 400


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
            return result.stdout or "No changes."

        text = core.diff(
            project_path, path=target, staged=staged, max_lines=MAX_DIFF_LINES, **common
        )
        return text if text.strip() else "No changes in the working tree."

    return [git_diff]


__all__ = ["MAX_DIFF_LINES", "create_git_tools"]
