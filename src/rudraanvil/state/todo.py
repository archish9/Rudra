"""Dynamic todo list for agent task tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class TodoStatus(str, Enum):
    """Status of a todo item."""
    
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class TodoItem:
    """A single task item in the todo list."""
    
    id: str = field(default_factory=lambda: str(uuid4())[:8])
    title: str = ""
    description: str = ""
    status: TodoStatus = TodoStatus.PENDING
    priority: int = 0  # Higher = more important
    parent_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    assigned_agent: Optional[str] = None  # Sub-agent type assigned to this task
    
    def mark_in_progress(self, agent: Optional[str] = None) -> None:
        """Mark the item as in progress."""
        self.status = TodoStatus.IN_PROGRESS
        if agent:
            self.assigned_agent = agent
    
    def mark_completed(self) -> None:
        """Mark the item as completed."""
        self.status = TodoStatus.COMPLETED
        self.completed_at = datetime.now().isoformat()
    
    def mark_failed(self) -> None:
        """Mark the item as failed."""
        self.status = TodoStatus.FAILED
        self.completed_at = datetime.now().isoformat()
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        data = asdict(self)
        data["status"] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> TodoItem:
        """Create from dictionary."""
        data["status"] = TodoStatus(data["status"])
        return cls(**data)


@dataclass
class TodoList:
    """Dynamic todo list that agents use to track tasks."""
    
    items: list[TodoItem] = field(default_factory=list)
    
    def add(
        self,
        title: str,
        description: str = "",
        priority: int = 0,
        parent_id: Optional[str] = None,
    ) -> TodoItem:
        """Add a new todo item."""
        item = TodoItem(
            title=title,
            description=description,
            priority=priority,
            parent_id=parent_id,
        )
        self.items.append(item)
        return item
    
    def add_urgent(self, title: str, description: str = "") -> TodoItem:
        """Add an urgent item at the top of the list."""
        item = TodoItem(
            title=title,
            description=description,
            priority=100,
        )
        self.items.insert(0, item)
        return item
    
    def get(self, item_id: str) -> Optional[TodoItem]:
        """Get an item by ID."""
        for item in self.items:
            if item.id == item_id:
                return item
        return None
    
    def get_next_pending(self) -> Optional[TodoItem]:
        """Get the next pending item (highest priority first)."""
        pending = [i for i in self.items if i.status == TodoStatus.PENDING]
        if not pending:
            return None
        return max(pending, key=lambda x: x.priority)
    
    def get_in_progress(self) -> list[TodoItem]:
        """Get all items currently in progress."""
        return [i for i in self.items if i.status == TodoStatus.IN_PROGRESS]
    
    def get_completed(self) -> list[TodoItem]:
        """Get all completed items."""
        return [i for i in self.items if i.status == TodoStatus.COMPLETED]
    
    def get_children(self, parent_id: str) -> list[TodoItem]:
        """Get all children of a parent item."""
        return [i for i in self.items if i.parent_id == parent_id]
    
    def is_complete(self) -> bool:
        """Check if all items are completed or failed."""
        return all(
            i.status in (TodoStatus.COMPLETED, TodoStatus.FAILED)
            for i in self.items
        )
    
    def reorder(self, item_id: str, new_priority: int) -> None:
        """Change the priority of an item."""
        item = self.get(item_id)
        if item:
            item.priority = new_priority
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "items": [item.to_dict() for item in self.items]
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> TodoList:
        """Create from dictionary."""
        return cls(items=[TodoItem.from_dict(item) for item in data.get("items", [])])
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> TodoList:
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))
    
    def summary(self) -> str:
        """Get a summary of the todo list for LLM context."""
        pending = len([i for i in self.items if i.status == TodoStatus.PENDING])
        in_progress = len([i for i in self.items if i.status == TodoStatus.IN_PROGRESS])
        completed = len([i for i in self.items if i.status == TodoStatus.COMPLETED])
        failed = len([i for i in self.items if i.status == TodoStatus.FAILED])
        
        lines = [
            f"Todo List: {pending} pending, {in_progress} in progress, {completed} completed, {failed} failed",
            "",
        ]
        
        for item in self.items:
            status_icon = {
                TodoStatus.PENDING: "[ ]",
                TodoStatus.IN_PROGRESS: "[/]",
                TodoStatus.COMPLETED: "[x]",
                TodoStatus.FAILED: "[!]",
                TodoStatus.BLOCKED: "[B]",
            }[item.status]
            
            indent = "  " if item.parent_id else ""
            agent_info = f" ({item.assigned_agent})" if item.assigned_agent else ""
            lines.append(f"{indent}{status_icon} {item.title}{agent_info}")
        
        return "\n".join(lines)
