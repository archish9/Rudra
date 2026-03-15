"""Project configuration management."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ProjectContext:
    """Context holding information about the project tech stack."""
    
    primary_language: str = ""
    framework: str = ""
    database: str = ""
    additional_context: str = ""
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> ProjectContext:
        """Create from dictionary."""
        return cls(**data)


class ProjectConfigManager:
    """Manages reading and writing project configuration."""
    
    def __init__(self, project_dir: Path):
        """Initialize the config manager.
        
        Args:
            project_dir: Path to the project root directory
        """
        self.project_dir = Path(project_dir)
        self.config_file = self.project_dir / ".rudraanvil" / "project.json"
        
    def load(self) -> ProjectContext:
        """Load project configuration if it exists, otherwise return empty context.
        
        Returns:
            ProjectContext instance
        """
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                return ProjectContext.from_dict(data)
            except Exception:
                pass
        return ProjectContext()
        
    def save(self, context: ProjectContext) -> None:
        """Save project configuration to disk.
        
        Args:
            context: The project context to save
        """
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(context.to_dict(), indent=2), encoding="utf-8")
