"""File operation tools for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


def create_file_tools(vfs: VirtualFileSystem) -> list:
    """Create file operation tools that work with the virtual filesystem.
    
    Args:
        vfs: The virtual filesystem to operate on
        
    Returns:
        List of LangChain tools
    """
    
    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file.
        
        Args:
            path: Path to the file (relative to project root)
            
        Returns:
            The file contents, or an error message if not found
        """
        content = vfs.read_file(path)
        if content is None:
            return f"Error: File not found: {path}"
        return content
    
    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file. Creates the file if it doesn't exist.
        
        Args:
            path: Path to the file (relative to project root)
            content: Content to write to the file
            
        Returns:
            Success message
        """
        vfs.write_file(path, content)
        return f"Successfully wrote to {path}"
    
    @tool
    def edit_file(path: str, old_content: str, new_content: str) -> str:
        """Edit a file by replacing old content with new content.
        
        Use this for targeted edits instead of rewriting the entire file.
        
        Args:
            path: Path to the file (relative to project root)
            old_content: The exact content to find and replace
            new_content: The content to replace it with
            
        Returns:
            Success or error message
        """
        if vfs.edit_file(path, old_content, new_content):
            return f"Successfully edited {path}"
        return f"Error: Could not find the specified content in {path}"
    
    @tool
    def create_directory(path: str) -> str:
        """Create a directory.
        
        Args:
            path: Path to the directory (relative to project root)
            
        Returns:
            Success message
        """
        vfs.create_directory(path)
        return f"Successfully created directory: {path}"
    
    @tool
    def list_directory(path: str = "") -> str:
        """List the contents of a directory.
        
        Args:
            path: Path to the directory (empty for project root)
            
        Returns:
            List of files and directories
        """
        items = vfs.list_directory(path)
        if not items:
            return f"Directory is empty or not found: {path or '.'}"
        return "\n".join(items)
    
    @tool
    def delete_file(path: str) -> str:
        """Delete a file from the project.
        
        Args:
            path: Path to the file (relative to project root)
            
        Returns:
            Success or error message
        """
        if vfs.delete_file(path):
            return f"Successfully deleted: {path}"
        return f"Error: File not found: {path}"
    
    @tool
    def file_exists(path: str) -> str:
        """Check if a file or directory exists.
        
        Args:
            path: Path to check (relative to project root)
            
        Returns:
            'true' or 'false'
        """
        return "true" if vfs.exists(path) else "false"
    
    @tool
    def get_project_tree() -> str:
        """Get a tree view of the project structure.
        
        Returns:
            Tree representation of files and directories
        """
        return vfs.get_tree()
    
    return [
        read_file,
        write_file,
        edit_file,
        create_directory,
        list_directory,
        delete_file,
        file_exists,
        get_project_tree,
    ]
