"""The Python git API (Step 8, C3.5).

The orchestrator calls these directly -- `auto_branch` before a run, `diff`
for Step 9's reviewer -- with no model involved. The one model-facing tool
built on top lives in `rudra.tools.git_tools`, with every other tool.
"""

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
