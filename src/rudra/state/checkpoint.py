"""Session ID management for DeepAgents checkpointing.

The full agent state (messages, tool calls, results) is persisted automatically
by DeepAgents' SqliteSaver (LangGraph) into .rudra/checkpoints.db.

This module's sole job is to provide a stable `thread_id` so every command in
the same project directory continues from the same conversation thread.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_SESSION_FILE = "session_id.txt"


def get_or_create_session_id(rudra_dir: Path) -> str:
    """Return the persistent session ID for this project.

    On first call, generates a 12-char hex ID derived from the project path,
    persists it to `.rudra/session_id.txt`, and returns it.
    On subsequent calls (any command in the same project), reads and returns
    the existing ID so DeepAgents loads the correct checkpoint thread.

    Args:
        rudra_dir: Path to the .rudra/ directory (created if absent).

    Returns:
        12-character hex session ID string.
    """
    rudra_dir.mkdir(parents=True, exist_ok=True)
    session_file = rudra_dir / _SESSION_FILE

    if session_file.exists():
        return session_file.read_text().strip()

    # Derive a stable ID from the project path so it's reproducible
    session_id = hashlib.sha256(str(rudra_dir.resolve()).encode()).hexdigest()[:12]
    session_file.write_text(session_id)
    return session_id


__all__ = ["get_or_create_session_id"]
