"""Planning tools for filesystem-based context management.

Replaces deepagents' built-in write_todos tool with a filesystem-backed
alternative. The plan is stored in `.rudra/PLAN.md`.

IMPORTANT — filesystem design:
  update_plan / read_plan both operate on the REAL disk file directly,
  bypassing the VirtualFileSystem in-memory cache. This keeps them in sync
  with deepagents' built-in edit_file / read_file tools, which also write
  to real disk. If read_plan used vfs.read_file(), it would return a stale
  cache that was never updated when edit_file ran.

Usage pattern for the agent:
  1. update_plan("- [ ] main.py\\n- [ ] models.py")
  2. write_file(file_path="main.py", content="...")
  3. edit_file('.rudra/PLAN.md', '- [ ] main.py', '- [x] main.py')
  4. Repeat steps 2-3 for each remaining item.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


def create_planning_tools(vfs, task: str = "") -> list:
    """Create planning tools that read/write .rudra/PLAN.md directly on disk.

    Args:
        vfs: VirtualFileSystem anchored to the project root (used for root_path only).
        task: The original user task — injected into return messages to remind the
              model of the framework/requirements.

    Returns:
        List of LangChain tools: [update_plan, read_plan]
    """
    plan_path: Path = vfs.root_path / ".rudra" / "PLAN.md"

    @tool
    def update_plan(plan_markdown: str) -> str:
        """Write or overwrite the agent's task plan to .rudra/PLAN.md.

        Each checklist item MUST be an actual filename — NOT a task description.
          CORRECT: - [ ] main.py
          CORRECT: - [ ] requirements.txt
          CORRECT: - [ ] models.py
          WRONG:   - [ ] Create FastAPI project structure
          WRONG:   - [ ] Install dependencies
          WRONG:   - [ ] Set up authentication

        Format:
          - `- [ ] filename.py` for pending files
          - `- [x] filename.py` for completed files

        After writing each file, check it off with edit_file:
          old_string = '- [ ] app.py'
          new_string = '- [x] app.py'

        Args:
            plan_markdown: Full markdown checklist where each item is a filename.

        Returns:
            Confirmation and instruction to write the first file immediately.
        """
        # Reject shrinking plans — prevents model from destroying its own checklist
        if plan_path.exists():
            existing_text = plan_path.read_text(encoding="utf-8")
            existing_count = sum(1 for l in existing_text.splitlines() if "- [ ]" in l or "- [x]" in l)
            new_count = sum(1 for l in plan_markdown.splitlines() if "- [ ]" in l or "- [x]" in l)
            if existing_count > 1 and new_count < existing_count:
                return (
                    f"REJECTED: Cannot reduce plan from {existing_count} to {new_count} items.\n"
                    f"Current plan:\n{existing_text}\n\n"
                    "Continue from the EXISTING plan. Write the next pending file."
                )

        # Sanitise: drop any .rudra/ items (e.g. PLAN.md itself) accidentally included.
        clean_lines = []
        for line in plan_markdown.splitlines():
            if "- [ ]" in line or "- [x]" in line:
                filename = line.strip().replace("- [ ]", "").replace("- [x]", "").strip()
                if filename.startswith(".rudra/") or filename.startswith(".rudra\\"):
                    continue  # silently drop internal state files
            clean_lines.append(line)
        plan_markdown = "\n".join(clean_lines)

        # Write directly to disk — bypasses VFS cache so read_plan stays in sync
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan_markdown, encoding="utf-8")
        # Invalidate VFS cache — pop both separator styles (Windows uses \, POSIX uses /)
        vfs.files.pop(str(Path(".rudra") / "PLAN.md"), None)
        vfs.files.pop(".rudra/PLAN.md", None)

        pending = [
            line.strip().replace("- [ ]", "").strip()
            for line in plan_markdown.splitlines()
            if "- [ ]" in line
        ]
        if pending:
            next_action = (
                f"Now write each file yourself with write_file(). "
                f"Start with the first pending file: {pending[0]}. "
                "Write ONE file per message — call write_file() once, then stop and wait."
            )
        else:
            next_action = "All items already checked off."
        return f"Plan saved. {next_action}"

    @tool
    def read_plan() -> str:
        """Read the current task plan from .rudra/PLAN.md.

        Always reads from disk so it reflects changes made by edit_file.

        Returns:
            Current plan markdown, or a message if no plan exists yet.
        """
        # Read directly from disk — never from VFS cache, which may be stale
        if plan_path.exists():
            return plan_path.read_text(encoding="utf-8")
        return (
            f"No plan found. You MUST call update_plan() now with a filename checklist.\n\n"
            f"Your task: {task}\n\n"
            f"Example — call update_plan() with something like:\n"
            f"  update_plan(plan_markdown='- [ ] app.py\\n- [ ] models.py\\n- [ ] requirements.txt')\n\n"
            f"List ONLY filenames. Do NOT ask the user — decide the files yourself based on the task above."
        )

    task_path: Path = vfs.root_path / ".rudra" / "current_task.md"

    @tool
    def write_task_assignment(file_path: str, instructions: str, context_files: str = "") -> str:
        """Write a coding task assignment to .rudra/current_task.md for the coder agent.

        Call this BEFORE the coder generates each file. The coder reads this file
        to know exactly what to build.

        Each item in the plan must be written with this tool before the coder runs.
        After writing the assignment for the first file, STOP — the orchestrator
        will invoke the coder and then ask you for the next file's assignment.

        Args:
            file_path: Exact relative path of the file to create (e.g. "src/main.py")
            instructions: Complete, detailed instructions for what the file must contain.
                          Include imports, classes, functions, endpoints, DB models, etc.
            context_files: Comma-separated list of existing files the coder should read first

        Returns:
            Confirmation message
        """
        task_path.parent.mkdir(parents=True, exist_ok=True)
        content = f"# Task Assignment\n\n**File to create:** `{file_path}`\n\n## Instructions\n\n{instructions}\n"
        if context_files.strip():
            content += f"\n## Context Files (read these first)\n\n{context_files}\n"
        task_path.write_text(content, encoding="utf-8")
        return (
            f"Task assignment written for `{file_path}`. "
            "The coder will read .rudra/current_task.md and write this file. STOP now."
        )

    return [update_plan, read_plan, write_task_assignment]
