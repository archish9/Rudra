"""Code execution and testing tools for autonomous development."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional
from langchain_core.tools import tool


def create_execution_tools(project_path: Path, timeout: int = 60) -> list:
    """Create tools for executing code and running tests.
    
    Args:
        project_path: Root directory of the project
        timeout: Maximum execution time in seconds
        
    Returns:
        List of LangChain tools for code execution
    """
    
    def run_command(cmd: list[str], cwd: Optional[Path] = None) -> tuple[int, str, str]:
        """Run a shell command and return exit code, stdout, stderr."""
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or project_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, "", f"Error executing command: {str(e)}"
    
    @tool
    def run_python_file(file_path: str, args: str = "") -> str:
        """Execute a Python file and return the output.
        
        Args:
            file_path: Path to the Python file (relative to project root)
            args: Optional command-line arguments to pass to the script
            
        Returns:
            Execution output (stdout + stderr) and exit code
        """
        cmd = [sys.executable, file_path]
        if args:
            cmd.extend(args.split())
        
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(f"STDOUT:\n{stdout}")
        if stderr:
            output.append(f"STDERR:\n{stderr}")
        output.append(f"Exit code: {exit_code}")
        
        return "\n\n".join(output)
    
    @tool
    def run_pytest(test_path: str = "", args: str = "-v") -> str:
        """Run pytest tests and return results.
        
        Args:
            test_path: Optional specific test file or directory (empty for all tests)
            args: Pytest arguments (default: -v for verbose)
            
        Returns:
            Test results including passed/failed counts
        """
        cmd = [sys.executable, "-m", "pytest"]
        if args:
            cmd.extend(args.split())
        if test_path:
            cmd.append(test_path)
        
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(f"STDERR:\n{stderr}")
        
        # Parse results
        if exit_code == 0:
            output.append("\n✅ All tests passed")
        elif exit_code == 1:
            output.append("\n❌ Some tests failed")
        elif exit_code == 5:
            output.append("\n⚠️ No tests found")
        else:
            output.append(f"\n❌ Pytest error (exit code: {exit_code})")
        
        return "\n".join(output)
    
    @tool
    def run_unittest(test_path: str = "") -> str:
        """Run Python unittest tests.
        
        Args:
            test_path: Optional specific test module (e.g., 'tests.test_main')
            
        Returns:
            Test results
        """
        cmd = [sys.executable, "-m", "unittest"]
        if test_path:
            cmd.append(test_path)
        else:
            cmd.append("discover")
        
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(stderr)
        
        if exit_code == 0:
            output.append("\n✅ All tests passed")
        else:
            output.append(f"\n❌ Tests failed (exit code: {exit_code})")
        
        return "\n".join(output)
    
    @tool
    def run_linter(file_path: str = ".", tool_name: str = "ruff") -> str:
        """Run a linter on the code to check for issues.
        
        Args:
            file_path: Path to file or directory to lint (default: current directory)
            tool_name: Linter to use ('ruff', 'pylint', 'flake8')
            
        Returns:
            Linting results with issues found
        """
        if tool_name == "ruff":
            cmd = ["ruff", "check", file_path]
        elif tool_name == "pylint":
            cmd = ["pylint", file_path]
        elif tool_name == "flake8":
            cmd = ["flake8", file_path]
        else:
            return f"Unknown linter: {tool_name}"
        
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(stderr)
        
        if exit_code == 0:
            output.append("\n✅ No linting issues found")
        else:
            output.append(f"\n⚠️ Linting issues found (exit code: {exit_code})")
        
        return "\n".join(output) if output else "No output from linter"
    
    @tool
    def run_formatter(file_path: str = ".", tool_name: str = "ruff") -> str:
        """Format code using a code formatter.
        
        Args:
            file_path: Path to file or directory to format
            tool_name: Formatter to use ('ruff', 'black', 'autopep8')
            
        Returns:
            Formatting results
        """
        if tool_name == "ruff":
            cmd = ["ruff", "format", file_path]
        elif tool_name == "black":
            cmd = ["black", file_path]
        elif tool_name == "autopep8":
            cmd = ["autopep8", "--in-place", "--recursive", file_path]
        else:
            return f"Unknown formatter: {tool_name}"
        
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(stderr)
        
        if exit_code == 0:
            output.append(f"\n✅ Code formatted successfully with {tool_name}")
        else:
            output.append(f"\n❌ Formatting failed (exit code: {exit_code})")
        
        return "\n".join(output) if output else f"Formatted with {tool_name}"
    
    @tool
    def install_package(package_name: str) -> str:
        """Install a Python package using pip.
        
        Args:
            package_name: Name of the package to install (e.g., 'requests', 'fastapi==0.100.0')
            
        Returns:
            Installation result
        """
        cmd = [sys.executable, "-m", "pip", "install", package_name]
        exit_code, stdout, stderr = run_command(cmd)
        
        output = []
        if stdout:
            output.append(stdout)
        if stderr:
            output.append(stderr)
        
        if exit_code == 0:
            output.append(f"\n✅ Successfully installed {package_name}")
        else:
            output.append(f"\n❌ Failed to install {package_name}")
        
        return "\n".join(output)
    
    @tool
    def check_syntax(file_path: str) -> str:
        """Check Python file for syntax errors without executing it.
        
        Args:
            file_path: Path to the Python file
            
        Returns:
            Syntax check result
        """
        cmd = [sys.executable, "-m", "py_compile", file_path]
        exit_code, stdout, stderr = run_command(cmd)
        
        if exit_code == 0:
            return f"✅ {file_path} has valid Python syntax"
        else:
            return f"❌ Syntax error in {file_path}:\n{stderr}"
    
    return [
        run_python_file,
        run_pytest,
        run_unittest,
        run_linter,
        run_formatter,
        install_package,
        check_syntax,
    ]
