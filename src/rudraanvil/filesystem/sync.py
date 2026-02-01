"""Sync virtual filesystem to real disk."""

from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel

from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


class SyncMode(str, Enum):
    """Mode for syncing files to disk."""
    
    OVERWRITE = "overwrite"  # Directly overwrite files
    BACKUP = "backup"  # Create backup before overwriting
    DIFF_ONLY = "diff_only"  # Only show diffs, don't write


@dataclass
class SyncResult:
    """Result of a sync operation."""
    
    files_created: list[str]
    files_modified: list[str]
    files_backed_up: list[str]
    errors: list[str]
    
    @property
    def success(self) -> bool:
        """Whether sync completed without errors."""
        return len(self.errors) == 0
    
    def summary(self) -> str:
        """Get a summary of the sync operation."""
        lines = []
        if self.files_created:
            lines.append(f"Created: {len(self.files_created)} files")
        if self.files_modified:
            lines.append(f"Modified: {len(self.files_modified)} files")
        if self.files_backed_up:
            lines.append(f"Backed up: {len(self.files_backed_up)} files")
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        return ", ".join(lines) if lines else "No changes"


class FileSyncManager:
    """Manages syncing virtual filesystem to real disk."""
    
    def __init__(self, vfs: VirtualFileSystem, console: Optional[Console] = None):
        """Initialize the sync manager.
        
        Args:
            vfs: The virtual filesystem to sync from
            console: Optional Rich console for output
        """
        self.vfs = vfs
        self.console = console or Console()
    
    def _create_backup(self, path: Path) -> Optional[Path]:
        """Create a backup of an existing file.
        
        Args:
            path: Path to the file to backup
            
        Returns:
            Path to the backup file, or None if backup failed
        """
        if not path.exists():
            return None
        
        backup_dir = self.vfs.root_path / ".rudraanvil" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{path.name}.{timestamp}.bak"
        backup_path = backup_dir / backup_name
        
        try:
            shutil.copy2(path, backup_path)
            return backup_path
        except Exception:
            return None
    
    def get_diff(self, path: str) -> str:
        """Get a unified diff for a modified file.
        
        Args:
            path: Path to the file
            
        Returns:
            Unified diff string
        """
        if path not in self.vfs.files:
            return ""
        
        file = self.vfs.files[path]
        
        if file.is_new:
            # Show all lines as additions
            lines = file.content.splitlines(keepends=True)
            return "".join([f"+{line}" for line in lines])
        
        if not file.is_modified:
            return ""
        
        original = (file.original_content or "").splitlines(keepends=True)
        modified = file.content.splitlines(keepends=True)
        
        diff = difflib.unified_diff(
            original,
            modified,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        
        return "".join(diff)
    
    def show_diff(self, path: str) -> None:
        """Display a diff for a modified file with syntax highlighting.
        
        Args:
            path: Path to the file
        """
        diff = self.get_diff(path)
        if not diff:
            return
        
        file = self.vfs.files.get(path)
        if file and file.is_new:
            self.console.print(Panel(
                Syntax(file.content, self._get_language(path), line_numbers=True),
                title=f"[green]NEW: {path}[/green]",
                border_style="green",
            ))
        else:
            self.console.print(Panel(
                Syntax(diff, "diff"),
                title=f"[yellow]MODIFIED: {path}[/yellow]",
                border_style="yellow",
            ))
    
    def _get_language(self, path: str) -> str:
        """Get syntax highlighting language for a file path."""
        extensions = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "jsx",
            ".tsx": "tsx",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".html": "html",
            ".css": "css",
            ".sql": "sql",
            ".sh": "bash",
            ".toml": "toml",
        }
        suffix = Path(path).suffix.lower()
        return extensions.get(suffix, "text")
    
    def preview_changes(self) -> None:
        """Show a preview of all changes that would be made."""
        new_files = self.vfs.get_new_files()
        modified_files = self.vfs.get_modified_files()
        
        if not new_files and not modified_files:
            self.console.print("[dim]No changes to sync[/dim]")
            return
        
        self.console.print("\n[bold]Changes to sync:[/bold]\n")
        
        for path in new_files:
            self.show_diff(path)
        
        for path in modified_files:
            if path not in new_files:  # Avoid showing twice
                self.show_diff(path)
    
    def sync(self, mode: SyncMode = SyncMode.OVERWRITE, paths: Optional[list[str]] = None) -> SyncResult:
        """Sync virtual filesystem changes to real disk.
        
        Args:
            mode: Sync mode to use
            paths: Optional list of specific paths to sync (syncs all if None)
            
        Returns:
            SyncResult with details of the operation
        """
        result = SyncResult(
            files_created=[],
            files_modified=[],
            files_backed_up=[],
            errors=[],
        )
        
        if mode == SyncMode.DIFF_ONLY:
            self.preview_changes()
            return result
        
        # Get files to sync
        if paths is None:
            files_to_sync = list(self.vfs.files.keys())
        else:
            files_to_sync = paths
        
        for path in files_to_sync:
            if path not in self.vfs.files:
                continue
            
            file = self.vfs.files[path]
            
            # Skip directories and unmodified files
            if file.is_directory:
                continue
            if not file.is_new and not file.is_modified:
                continue
            
            real_path = self.vfs.root_path / path
            
            try:
                # Create parent directories if needed
                real_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Backup if needed
                if mode == SyncMode.BACKUP and real_path.exists():
                    backup_path = self._create_backup(real_path)
                    if backup_path:
                        result.files_backed_up.append(str(backup_path))
                
                # Write the file
                real_path.write_text(file.content, encoding="utf-8")
                
                if file.is_new:
                    result.files_created.append(path)
                else:
                    result.files_modified.append(path)
                
                # Update file state
                file.is_new = False
                file.is_modified = False
                file.original_content = file.content
                
            except Exception as e:
                result.errors.append(f"{path}: {str(e)}")
        
        return result
    
    def sync_single(self, path: str, mode: SyncMode = SyncMode.OVERWRITE) -> bool:
        """Sync a single file to disk.
        
        Args:
            path: Path to the file
            mode: Sync mode
            
        Returns:
            True if successful, False otherwise
        """
        result = self.sync(mode=mode, paths=[path])
        return result.success
