"""Virtual filesystem for safe agent file operations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class VirtualFile:
    """A file in the virtual filesystem."""
    
    path: str
    content: str
    is_directory: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    modified_at: str = field(default_factory=lambda: datetime.now().isoformat())
    is_new: bool = True  # True if file doesn't exist on real FS
    is_modified: bool = False  # True if content changed from real FS
    original_content: Optional[str] = None  # Content from real FS for diff
    
    def update_content(self, content: str) -> None:
        """Update file content and mark as modified."""
        if self.content != content:
            self.content = content
            self.modified_at = datetime.now().isoformat()
            self.is_modified = True
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "path": self.path,
            "content": self.content,
            "is_directory": self.is_directory,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "is_new": self.is_new,
            "is_modified": self.is_modified,
            "original_content": self.original_content,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> VirtualFile:
        """Create from dictionary."""
        return cls(**data)


class VirtualFileSystem:
    """In-memory virtual filesystem for safe agent operations.
    
    All file operations happen in memory first, then can be synced
    to the real filesystem after completion.
    """
    
    def __init__(self, root_path: Path):
        """Initialize the virtual filesystem.
        
        Args:
            root_path: The real filesystem root path this VFS mirrors
        """
        self.root_path = root_path.resolve()
        self.files: dict[str, VirtualFile] = {}
        self._ignored_patterns: list[str] = []
    
    def _normalize_path(self, path: str) -> str:
        """Normalize a path to be relative to root."""
        p = Path(path)
        if p.is_absolute():
            try:
                p = p.relative_to(self.root_path)
            except ValueError:
                raise ValueError(f"Path {path} is outside root {self.root_path}")
        return str(p)
    
    def _is_ignored(self, path: str) -> bool:
        """Check if a path should be ignored."""
        from fnmatch import fnmatch
        for pattern in self._ignored_patterns:
            if fnmatch(path, pattern) or fnmatch(os.path.basename(path), pattern):
                return True
        return False
    
    def load_gitignore(self) -> None:
        """Load patterns from .gitignore if it exists."""
        gitignore = self.root_path / ".gitignore"
        if gitignore.exists():
            patterns = []
            for line in gitignore.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
            self._ignored_patterns = patterns
        
        # Always ignore common patterns
        self._ignored_patterns.extend([
            ".git",
            ".git/*",
            "__pycache__",
            "__pycache__/*",
            "*.pyc",
            ".rudraanvil",
            ".rudraanvil/*",
            "node_modules",
            "node_modules/*",
            ".venv",
            ".venv/*",
            "venv",
            "venv/*",
        ])
    
    def load_from_disk(self, max_depth: int = 10, extensions: Optional[list[str]] = None) -> None:
        """Load existing files from the real filesystem into virtual FS.
        
        Args:
            max_depth: Maximum directory depth to scan
            extensions: Optional list of file extensions to include (e.g., ['.py', '.js'])
        """
        self.load_gitignore()
        
        def should_include(path: Path) -> bool:
            if extensions:
                return path.suffix in extensions
            return True
        
        def scan_dir(dir_path: Path, depth: int = 0) -> None:
            if depth > max_depth:
                return
            
            try:
                for item in dir_path.iterdir():
                    rel_path = str(item.relative_to(self.root_path))
                    
                    if self._is_ignored(rel_path):
                        continue
                    
                    if item.is_dir():
                        self.create_directory(rel_path)
                        scan_dir(item, depth + 1)
                    elif item.is_file() and should_include(item):
                        try:
                            content = item.read_text(encoding="utf-8")
                            self.files[rel_path] = VirtualFile(
                                path=rel_path,
                                content=content,
                                is_new=False,
                                original_content=content,
                            )
                        except (UnicodeDecodeError, PermissionError):
                            # Skip binary or inaccessible files
                            pass
            except PermissionError:
                pass
        
        scan_dir(self.root_path)
    
    def read_file(self, path: str) -> Optional[str]:
        """Read a file from the virtual filesystem.
        
        Args:
            path: Path to the file (relative to root)
            
        Returns:
            File content or None if not found
        """
        path = self._normalize_path(path)
        
        # Check virtual FS first
        if path in self.files:
            file = self.files[path]
            if not file.is_directory:
                return file.content
        
        # Fall back to real FS
        real_path = self.root_path / path
        if real_path.exists() and real_path.is_file():
            try:
                content = real_path.read_text(encoding="utf-8")
                # Cache in virtual FS
                self.files[path] = VirtualFile(
                    path=path,
                    content=content,
                    is_new=False,
                    original_content=content,
                )
                return content
            except (UnicodeDecodeError, PermissionError):
                return None
        
        return None
    
    def write_file(self, path: str, content: str) -> None:
        """Write a file to the virtual filesystem.
        
        Args:
            path: Path to the file (relative to root)
            content: Content to write
        """
        path = self._normalize_path(path)
        
        if path in self.files:
            self.files[path].update_content(content)
        else:
            # Check if file exists on real FS
            real_path = self.root_path / path
            original = None
            is_new = True
            
            if real_path.exists():
                try:
                    original = real_path.read_text(encoding="utf-8")
                    is_new = False
                except (UnicodeDecodeError, PermissionError):
                    pass
            
            self.files[path] = VirtualFile(
                path=path,
                content=content,
                is_new=is_new,
                is_modified=True,
                original_content=original,
            )
    
    def edit_file(self, path: str, old_content: str, new_content: str) -> bool:
        """Edit a file by replacing old content with new content.
        
        Args:
            path: Path to the file
            old_content: Content to find and replace
            new_content: Content to replace with
            
        Returns:
            True if replacement was made, False otherwise
        """
        current = self.read_file(path)
        if current is None:
            return False
        
        if old_content not in current:
            return False
        
        updated = current.replace(old_content, new_content, 1)
        self.write_file(path, updated)
        return True
    
    def delete_file(self, path: str) -> bool:
        """Delete a file from the virtual filesystem.
        
        Args:
            path: Path to the file
            
        Returns:
            True if deleted, False if not found
        """
        path = self._normalize_path(path)
        if path in self.files:
            del self.files[path]
            return True
        return False
    
    def create_directory(self, path: str) -> None:
        """Create a directory in the virtual filesystem.
        
        Args:
            path: Path to the directory
        """
        path = self._normalize_path(path)
        if path not in self.files:
            self.files[path] = VirtualFile(
                path=path,
                content="",
                is_directory=True,
            )
    
    def list_directory(self, path: str = "") -> list[str]:
        """List contents of a directory.
        
        Args:
            path: Path to the directory (empty for root)
            
        Returns:
            List of file/directory names in the directory
        """
        path = self._normalize_path(path) if path else ""
        
        # Combine virtual FS and real FS listings
        items = set()
        
        # Virtual FS
        for file_path in self.files:
            if path:
                if file_path.startswith(path + "/"):
                    rest = file_path[len(path) + 1:]
                    items.add(rest.split("/")[0])
            else:
                items.add(file_path.split("/")[0])
        
        # Real FS
        real_path = self.root_path / path if path else self.root_path
        if real_path.exists() and real_path.is_dir():
            for item in real_path.iterdir():
                rel = item.name
                if not self._is_ignored(rel):
                    items.add(rel)
        
        return sorted(items)
    
    def exists(self, path: str) -> bool:
        """Check if a file or directory exists.
        
        Args:
            path: Path to check
            
        Returns:
            True if exists in virtual or real FS
        """
        path = self._normalize_path(path)
        
        if path in self.files:
            return True
        
        real_path = self.root_path / path
        return real_path.exists()
    
    def get_modified_files(self) -> list[str]:
        """Get all files that have been modified."""
        return [
            path for path, file in self.files.items()
            if file.is_modified and not file.is_directory
        ]
    
    def get_new_files(self) -> list[str]:
        """Get all new files that don't exist on real FS."""
        return [
            path for path, file in self.files.items()
            if file.is_new and not file.is_directory
        ]
    
    def get_tree(self, path: str = "", indent: int = 0) -> str:
        """Get a tree representation of the filesystem.
        
        Args:
            path: Starting path
            indent: Current indentation level
            
        Returns:
            Tree string representation
        """
        lines = []
        items = self.list_directory(path)
        
        for item in items:
            full_path = f"{path}/{item}" if path else item
            prefix = "  " * indent
            
            is_dir = False
            if full_path in self.files:
                is_dir = self.files[full_path].is_directory
            else:
                real_path = self.root_path / full_path
                is_dir = real_path.is_dir()
            
            if is_dir:
                lines.append(f"{prefix}📁 {item}/")
                lines.append(self.get_tree(full_path, indent + 1))
            else:
                marker = ""
                if full_path in self.files:
                    if self.files[full_path].is_new:
                        marker = " [NEW]"
                    elif self.files[full_path].is_modified:
                        marker = " [MODIFIED]"
                lines.append(f"{prefix}📄 {item}{marker}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "root_path": str(self.root_path),
            "files": {path: file.to_dict() for path, file in self.files.items()},
            "ignored_patterns": self._ignored_patterns,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> VirtualFileSystem:
        """Create from dictionary."""
        vfs = cls(Path(data["root_path"]))
        vfs.files = {path: VirtualFile.from_dict(file) for path, file in data.get("files", {}).items()}
        vfs._ignored_patterns = data.get("ignored_patterns", [])
        return vfs
