"""Session ID management for DeepAgents checkpointing.

The full agent state (messages, tool calls, results) is persisted automatically
by DeepAgents' SqliteSaver (LangGraph) into .rudra/run/checkpoints.db.

This module's sole job is to provide a stable `thread_id` so every command in
the same project directory continues from the same conversation thread.

Still unused: nothing calls this yet, which is TODO.md A1.3, and the reason
checkpoints are written but never resumed is A1.2. Step 6 only moves the file
into the D15 run/ subtree; it does not wire the function up.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from rudra.state.paths import ensure_layout


def get_or_create_session_id(project_root: Path) -> str:
    """Return the persistent session ID for this project.

    On first call, generates a 12-char hex ID derived from the project path,
    persists it to `.rudra/run/session_id.txt`, and returns it.
    On subsequent calls (any command in the same project), reads and returns
    the existing ID so DeepAgents loads the correct checkpoint thread.

    Args:
        project_root: Path to the project root (the `.rudra/` layout is
            created beneath it if absent).

    Returns:
        12-character hex session ID string.
    """
    session_file = ensure_layout(project_root).session_id_txt

    if session_file.exists():
        return session_file.read_text().strip()

    # Derive a stable ID from the project path so it's reproducible
    session_id = hashlib.sha256(str(Path(project_root).resolve()).encode()).hexdigest()[:12]
    session_file.write_text(session_id)
    return session_id


__all__ = ["get_or_create_session_id"]
