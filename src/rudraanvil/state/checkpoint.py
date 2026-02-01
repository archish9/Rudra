"""Checkpoint management for session persistence."""

from __future__ import annotations

import json
import signal
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rudraanvil.state.todo import TodoList


@dataclass
class Checkpoint:
    """A saved agent state."""
    
    session_id: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # Task state
    task_description: str = ""
    todo_list: dict = field(default_factory=dict)
    
    # Virtual filesystem state
    virtual_fs: dict = field(default_factory=dict)
    
    # Agent memory/context
    agent_memory: list[dict] = field(default_factory=list)
    
    # Sub-agent states
    sub_agent_states: dict = field(default_factory=dict)
    
    # Execution stats
    iterations_completed: int = 0
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    
    def update(self) -> None:
        """Update the timestamp."""
        self.updated_at = datetime.now().isoformat()
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> Checkpoint:
        """Create from dictionary."""
        return cls(**data)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> Checkpoint:
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))


def generate_session_id(task: str) -> str:
    """Generate a session ID from the task description and timestamp."""
    content = f"{task}-{datetime.now().isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:12]


class CheckpointManager:
    """Manages saving and loading checkpoints."""
    
    def __init__(self, checkpoint_dir: Path):
        """Initialize the checkpoint manager.
        
        Args:
            checkpoint_dir: Directory to store checkpoints (usually .rudraanvil/)
        """
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.current_checkpoint: Optional[Checkpoint] = None
        self._interrupt_handlers_installed = False
    
    def _get_checkpoint_path(self, session_id: str) -> Path:
        """Get the file path for a checkpoint."""
        return self.checkpoint_dir / f"checkpoint-{session_id}.json"
    
    def create(self, task: str, session_id: Optional[str] = None) -> Checkpoint:
        """Create a new checkpoint for a task.
        
        Args:
            task: The task description
            session_id: Optional session ID (auto-generated if not provided)
            
        Returns:
            The new checkpoint
        """
        if session_id is None:
            session_id = generate_session_id(task)
        
        checkpoint = Checkpoint(
            session_id=session_id,
            task_description=task,
            todo_list=TodoList().to_dict(),
        )
        
        self.current_checkpoint = checkpoint
        return checkpoint
    
    def save(self, checkpoint: Optional[Checkpoint] = None) -> Path:
        """Save a checkpoint to disk.
        
        Args:
            checkpoint: The checkpoint to save (uses current if not provided)
            
        Returns:
            The path where the checkpoint was saved
        """
        checkpoint = checkpoint or self.current_checkpoint
        if checkpoint is None:
            raise ValueError("No checkpoint to save")
        
        checkpoint.update()
        path = self._get_checkpoint_path(checkpoint.session_id)
        path.write_text(checkpoint.to_json())
        return path
    
    def load(self, session_id: str) -> Checkpoint:
        """Load a checkpoint from disk.
        
        Args:
            session_id: The session ID to load
            
        Returns:
            The loaded checkpoint
            
        Raises:
            FileNotFoundError: If the checkpoint doesn't exist
        """
        path = self._get_checkpoint_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {session_id}")
        
        checkpoint = Checkpoint.from_json(path.read_text())
        self.current_checkpoint = checkpoint
        return checkpoint
    
    def list_sessions(self) -> list[dict]:
        """List all available checkpoint sessions.
        
        Returns:
            List of session info dicts with id, task, and updated_at
        """
        sessions = []
        for path in self.checkpoint_dir.glob("checkpoint-*.json"):
            try:
                checkpoint = Checkpoint.from_json(path.read_text())
                sessions.append({
                    "session_id": checkpoint.session_id,
                    "task": checkpoint.task_description[:50] + "..." if len(checkpoint.task_description) > 50 else checkpoint.task_description,
                    "updated_at": checkpoint.updated_at,
                    "iterations": checkpoint.iterations_completed,
                })
            except Exception:
                continue
        
        # Sort by updated_at descending
        sessions.sort(key=lambda x: x["updated_at"], reverse=True)
        return sessions
    
    def delete(self, session_id: str) -> bool:
        """Delete a checkpoint.
        
        Args:
            session_id: The session ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        path = self._get_checkpoint_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def update_todo_list(self, todo_list: TodoList) -> None:
        """Update the todo list in the current checkpoint."""
        if self.current_checkpoint:
            self.current_checkpoint.todo_list = todo_list.to_dict()
    
    def update_virtual_fs(self, virtual_fs: dict) -> None:
        """Update the virtual filesystem in the current checkpoint."""
        if self.current_checkpoint:
            self.current_checkpoint.virtual_fs = virtual_fs
    
    def update_agent_memory(self, memory: list[dict]) -> None:
        """Update the agent memory in the current checkpoint."""
        if self.current_checkpoint:
            self.current_checkpoint.agent_memory = memory
    
    def increment_iterations(self) -> None:
        """Increment the iteration counter."""
        if self.current_checkpoint:
            self.current_checkpoint.iterations_completed += 1
    
    def install_interrupt_handlers(self) -> None:
        """Install signal handlers to save on interrupt (Ctrl+C)."""
        if self._interrupt_handlers_installed:
            return
        
        def handler(signum: int, frame: Any) -> None:
            if self.current_checkpoint:
                self.save()
            raise KeyboardInterrupt
        
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        self._interrupt_handlers_installed = True
