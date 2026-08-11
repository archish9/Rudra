"""Git tools and the Python git API (Step 8, C3.5)."""

from __future__ import annotations

from rudra.git.core import (
    BranchOutcome,
    Commit,
    FileStatus,
    auto_branch,
    branch_exists,
    branch_slug,
    current_branch,
    diff,
    is_clean,
    is_repo,
    log,
    status,
)

__all__ = [
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
