"""The `.rudra/` directory layout (TODO.md D15, §0.7, C0.9).

Two subtrees with different lifetimes:

* **durable** — `config.toml`, `AGENTS.md`, `project.json`, `memory/export/`.
  Text, worth committing, safe to commit.
* **volatile** — everything under `run/`, plus `memory/palace/`. Binary or
  regenerated on every run. Never worth committing.

Rudra writes `.rudra/.gitignore` covering only the volatile subtree, and
never touches the project's own `.gitignore`: whether `.rudra/` is committed
is the user's decision (D15).

`rudra_paths()` is pure and `ensure_layout()` is the only function here that
creates anything. That split exists because a getter that mkdir'd as a side
effect made read-only commands mutate the filesystem — see TODO.md A1.43.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RUDRA_DIR_NAME = ".rudra"

GITIGNORE_BODY = """\
# Written by Rudra. Scopes only this directory — your project's own
# .gitignore is never modified.
#
# Everything below is regenerated on every run or is a binary store.
# The durable files beside it (config.toml, AGENTS.md, project.json,
# memory/export/) are deliberately NOT ignored: committing them is safe,
# and whether you do is your call. See TODO.md D15.
run/
memory/palace/
"""


@dataclass(frozen=True)
class RudraPaths:
    """Every path Rudra owns inside one project, resolved but not created."""

    root: Path
    # durable
    config_toml: Path
    agents_md: Path
    project_json: Path
    memory_export: Path
    # volatile
    memory_palace: Path
    run: Path
    checkpoints_db: Path
    plan_md: Path
    current_task_md: Path
    tech_stack_md: Path
    session_id_txt: Path
    logs: Path
    artifacts: Path


def rudra_paths(project_root: Path) -> RudraPaths:
    """Resolve the layout for a project. Creates nothing (A1.43)."""
    root = Path(project_root) / RUDRA_DIR_NAME
    memory = root / "memory"
    run = root / "run"
    return RudraPaths(
        root=root,
        config_toml=root / "config.toml",
        agents_md=root / "AGENTS.md",
        project_json=root / "project.json",
        memory_export=memory / "export",
        memory_palace=memory / "palace",
        run=run,
        checkpoints_db=run / "checkpoints.db",
        plan_md=run / "PLAN.md",
        current_task_md=run / "current_task.md",
        tech_stack_md=run / "tech_stack.md",
        session_id_txt=run / "session_id.txt",
        logs=run / "logs",
        # deepagents evicts oversized tool results and offloads conversation
        # history to <artifacts_root>. That root defaults to the backend
        # root -- the user's project -- so Step 7 routes it here instead.
        # See TODO.md A1.45.
        artifacts=run / "artifacts",
    )


def ensure_layout(project_root: Path) -> RudraPaths:
    """Create the directory tree and `.gitignore`. Idempotent.

    The `.gitignore` is written only when absent, so a user who adds their
    own patterns keeps them across runs.
    """
    paths = rudra_paths(project_root)
    for directory in (
        paths.root,
        paths.run,
        paths.logs,
        paths.artifacts,
        paths.memory_export,
        paths.memory_palace,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    gitignore = paths.root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_BODY, encoding="utf-8")
    return paths


__all__ = ["GITIGNORE_BODY", "RUDRA_DIR_NAME", "RudraPaths", "ensure_layout", "rudra_paths"]
