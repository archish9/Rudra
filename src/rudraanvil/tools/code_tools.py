"""Code execution and quality tools for agents."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


def create_code_tools(vfs: VirtualFileSystem, timeout: int = 30) -> list:
    """Create code execution and quality tools.
    
    Args:
        vfs: The virtual filesystem
        timeout: Maximum execution time in seconds
        
    Returns:
        List of LangChain tools
    """
    
    @tool
    def run_python(code: str) -> str:
        """Run Python code in a sandboxed environment.
        
        WARNING: Only use for testing small code snippets.
        
        Args:
            code: Python code to execute
            
        Returns:
            Stdout, stderr, and exit code
        """
        try:
            result = subprocess.run(
                ["python", "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(vfs.root_path),
            )
            output = []
            if result.stdout:
                output.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")
            output.append(f"Exit code: {result.returncode}")
            return "\n".join(output)
        except subprocess.TimeoutExpired:
            return f"Error: Execution timed out after {timeout} seconds"
        except Exception as e:
            return f"Error: {str(e)}"
    
    @tool
    def run_command(command: str, working_dir: str = "") -> str:
        """Run a shell command in the project directory.
        
        Use this for running tests, linting, or other development commands.
        
        Args:
            command: The shell command to run
            working_dir: Optional subdirectory to run in (relative to project root)
            
        Returns:
            Command output and exit code
        """
        try:
            cwd = vfs.root_path
            if working_dir:
                cwd = cwd / working_dir
            
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd),
            )
            output = []
            if result.stdout:
                output.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")
            output.append(f"Exit code: {result.returncode}")
            return "\n".join(output)
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds"
        except Exception as e:
            return f"Error: {str(e)}"
    
    @tool
    def lint_python(path: str) -> str:
        """Run ruff linter on a Python file or directory.
        
        Args:
            path: Path to file or directory to lint
            
        Returns:
            Linting results
        """
        try:
            target = vfs.root_path / path
            result = subprocess.run(
                ["ruff", "check", str(target)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(vfs.root_path),
            )
            if result.returncode == 0:
                return "No linting issues found."
            return result.stdout + result.stderr
        except FileNotFoundError:
            return "Error: ruff not installed. Run 'pip install ruff' to install."
        except Exception as e:
            return f"Error: {str(e)}"
    
    @tool
    def format_python(path: str) -> str:
        """Format a Python file using ruff.
        
        Args:
            path: Path to the file to format
            
        Returns:
            Formatting result
        """
        try:
            target = vfs.root_path / path
            result = subprocess.run(
                ["ruff", "format", str(target)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(vfs.root_path),
            )
            if result.returncode == 0:
                # Reload the formatted content into VFS
                if target.exists():
                    vfs.write_file(path, target.read_text())
                return f"Successfully formatted {path}"
            return f"Error formatting: {result.stderr}"
        except FileNotFoundError:
            return "Error: ruff not installed. Run 'pip install ruff' to install."
        except Exception as e:
            return f"Error: {str(e)}"
    
    @tool
    def run_pytest(test_path: str = "tests/", options: str = "-v") -> str:
        """Run pytest on the project tests.
        
        Args:
            test_path: Path to tests (default: tests/)
            options: Additional pytest options (default: -v)
            
        Returns:
            Test results
        """
        try:
            cmd = f"pytest {test_path} {options}"
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120,  # Tests can take longer
                cwd=str(vfs.root_path),
            )
            output = []
            if result.stdout:
                output.append(result.stdout)
            if result.stderr:
                output.append(result.stderr)
            output.append(f"Exit code: {result.returncode}")
            return "\n".join(output)
        except subprocess.TimeoutExpired:
            return "Error: Tests timed out after 120 seconds"
        except Exception as e:
            return f"Error: {str(e)}"
    
    @tool
    def check_syntax(path: str) -> str:
        """Check Python file for syntax errors without running it.
        
        Args:
            path: Path to the Python file
            
        Returns:
            'Valid' or syntax error details
        """
        content = vfs.read_file(path)
        if content is None:
            return f"Error: File not found: {path}"
        
        try:
            compile(content, path, "exec")
            return "Valid: No syntax errors found"
        except SyntaxError as e:
            return f"Syntax Error at line {e.lineno}: {e.msg}"
    
    return [
        run_python,
        run_command,
        lint_python,
        format_python,
        run_pytest,
        check_syntax,
    ]
