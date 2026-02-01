"""Git tools for version control operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


def create_git_tools(vfs: VirtualFileSystem) -> list:
    """Create git operation tools.
    
    Args:
        vfs: The virtual filesystem
        
    Returns:
        List of LangChain tools
    """
    
    def _run_git(args: list[str]) -> tuple[str, int]:
        """Run a git command and return output and exit code."""
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                text=True,
                cwd=str(vfs.root_path),
            )
            output = result.stdout + result.stderr
            return output.strip(), result.returncode
        except FileNotFoundError:
            return "Error: git not installed", 1
        except Exception as e:
            return f"Error: {str(e)}", 1
    
    @tool
    def git_status() -> str:
        """Get the current git status of the project.
        
        Returns:
            Git status output showing changed files
        """
        output, code = _run_git(["status", "--short"])
        if code != 0:
            return f"Error: {output}"
        if not output:
            return "No changes in working directory"
        return output
    
    @tool
    def git_diff(path: str = "") -> str:
        """Show git diff for a file or the entire project.
        
        Args:
            path: Optional path to show diff for (shows all if empty)
            
        Returns:
            Git diff output
        """
        args = ["diff"]
        if path:
            args.append(path)
        output, code = _run_git(args)
        if code != 0:
            return f"Error: {output}"
        if not output:
            return "No differences to show"
        return output
    
    @tool
    def git_add(paths: str = ".") -> str:
        """Stage files for commit.
        
        Args:
            paths: Space-separated paths to stage (default: '.' for all)
            
        Returns:
            Success or error message
        """
        output, code = _run_git(["add"] + paths.split())
        if code != 0:
            return f"Error: {output}"
        return f"Successfully staged: {paths}"
    
    @tool
    def git_commit(message: str) -> str:
        """Create a git commit with the staged changes.
        
        Args:
            message: Commit message describing the changes
            
        Returns:
            Commit result
        """
        output, code = _run_git(["commit", "-m", message])
        if code != 0:
            return f"Error: {output}"
        return output
    
    @tool
    def git_log(count: int = 5) -> str:
        """Show recent git commits.
        
        Args:
            count: Number of commits to show (default: 5)
            
        Returns:
            Git log output
        """
        output, code = _run_git([
            "log", 
            f"-{count}", 
            "--oneline",
            "--decorate"
        ])
        if code != 0:
            return f"Error: {output}"
        if not output:
            return "No commits found"
        return output
    
    @tool
    def git_branch() -> str:
        """List all git branches and show current branch.
        
        Returns:
            List of branches with current marked
        """
        output, code = _run_git(["branch", "-a"])
        if code != 0:
            return f"Error: {output}"
        if not output:
            return "No branches found (is this a git repository?)"
        return output
    
    @tool
    def git_init() -> str:
        """Initialize a new git repository in the project.
        
        Returns:
            Success or error message
        """
        output, code = _run_git(["init"])
        if code != 0:
            return f"Error: {output}"
        return "Initialized git repository"
    
    @tool
    def git_checkout(branch: str, create: bool = False) -> str:
        """Switch to a different branch.
        
        Args:
            branch: Name of the branch to switch to
            create: If True, create the branch if it doesn't exist
            
        Returns:
            Success or error message
        """
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(branch)
        output, code = _run_git(args)
        if code != 0:
            return f"Error: {output}"
        return f"Switched to branch: {branch}"
    
    return [
        git_status,
        git_diff,
        git_add,
        git_commit,
        git_log,
        git_branch,
        git_init,
        git_checkout,
    ]
