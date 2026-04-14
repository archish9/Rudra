"""Code execution tools for agents — with scratchpad log offloading.

run_command writes full stdout/stderr to .rudra/logs/cmd_output.txt
and returns only a short summary to the agent. This prevents large command
outputs (pip install, pytest, tsc, etc.) from bloating the context window.

grep_in_file lets the agent search that log file without reading it all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import tool

# Maximum characters of stdout/stderr to surface in the tool result
_PREVIEW_CHARS = 300


def create_code_tools(vfs, timeout: int = 60) -> list:
    """Create code execution tools with output scratchpad offloading.

    Full stdout/stderr is written to .rudra/logs/cmd_output.txt.
    Only a short summary is returned to the agent.

    Args:
        vfs: VirtualFileSystem anchored to the project root.
        timeout: Maximum command execution time in seconds (default 60).

    Returns:
        List of LangChain tools: [run_command, grep_in_file]
    """

    @tool
    def run_command(command: str) -> str:
        """Run a shell command anchored to the project root directory.

        Full stdout/stderr is saved to .rudra/logs/cmd_output.txt.
        Only a short preview is returned here — use read_file or grep_in_file
        to inspect the full output.

        Safe uses:
          - Package installation: pip install -r requirements.txt
          - Linting / formatting: black ., pylint src/
          - Syntax checks: python -m py_compile app.py
          - Build commands: npm install, tsc --noEmit

        Do NOT use to run the application itself.
        Do NOT use for file I/O — use write_file / read_file instead.

        Args:
            command: Shell command to run (executed in the project root).

        Returns:
            Short summary: exit status + path to full log.
        """
        project_root: Path = vfs.root_path
        log_dir = project_root / ".rudra" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cmd_output.txt"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(project_root),
                timeout=timeout,
            )
            combined = (
                f"=== COMMAND ===\n{command}\n\n"
                f"=== EXIT CODE ===\n{result.returncode}\n\n"
                f"=== STDOUT ===\n{result.stdout}\n\n"
                f"=== STDERR ===\n{result.stderr}\n"
            )
            log_file.write_text(combined, encoding="utf-8")

            status = "SUCCESS" if result.returncode == 0 else "FAILED"
            preview_src = result.stderr if result.returncode != 0 else result.stdout
            preview = preview_src[:_PREVIEW_CHARS].replace("\n", " ↵ ")

            return (
                f"Command {status} (exit {result.returncode}). "
                f"Full output → .rudra/logs/cmd_output.txt\n"
                f"Preview: {preview}\n"
                f"Use read_file('.rudra/logs/cmd_output.txt') or "
                f"grep_in_file to inspect errors."
            )

        except subprocess.TimeoutExpired:
            msg = f"Command timed out after {timeout}s: {command}"
            log_file.write_text(msg, encoding="utf-8")
            return f"TIMEOUT: {msg}. See .rudra/logs/cmd_output.txt"

        except Exception as exc:
            msg = f"Error executing '{command}': {exc}"
            log_file.write_text(msg, encoding="utf-8")
            return f"ERROR: {msg}"

    @tool
    def grep_in_file(file_path: str, pattern: str) -> str:
        """Search for a pattern in a file (like grep, case-insensitive).

        Useful for scanning large log files without reading the whole thing.
        Results are capped at 50 lines to stay context-safe.

        Args:
            file_path: Path to the file (relative to project root).
            pattern: Substring to search for (case-insensitive).

        Returns:
            Matching lines with line numbers, or a not-found message.
        """
        content = vfs.read_file(file_path)
        if content is None:
            return f"Error: file not found: {file_path}"

        pattern_lower = pattern.lower()
        matches = [
            f"L{i + 1}: {line}"
            for i, line in enumerate(content.splitlines())
            if pattern_lower in line.lower()
        ]

        if not matches:
            return f"No matches for '{pattern}' in {file_path}"

        output = "\n".join(matches[:50])
        if len(matches) > 50:
            output += f"\n... and {len(matches) - 50} more matches (narrow your search)"
        return output

    return [run_command, grep_in_file]
