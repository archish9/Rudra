"""Planning tools for filesystem-based context management.

Replaces deepagents' built-in write_todos tool with a filesystem-backed
alternative. The plan is stored in `.rudraanvil/PLAN.md`.

IMPORTANT — filesystem design:
  update_plan / read_plan both operate on the REAL disk file directly,
  bypassing the VirtualFileSystem in-memory cache. This keeps them in sync
  with deepagents' built-in edit_file / read_file tools, which also write
  to real disk. If read_plan used vfs.read_file(), it would return a stale
  cache that was never updated when edit_file ran.

Usage pattern for the agent:
  1. update_plan("- [ ] main.py\\n- [ ] models.py")
  2. write_file(file_path="main.py", content="...")
  3. edit_file('.rudraanvil/PLAN.md', '- [ ] main.py', '- [x] main.py')
  4. Repeat steps 2-3 for each remaining item.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


def create_planning_tools(vfs, task: str = "") -> list:
    """Create planning tools that read/write .rudraanvil/PLAN.md directly on disk.

    Args:
        vfs: VirtualFileSystem anchored to the project root (used for root_path only).
        task: The original user task — injected into the update_plan return message
              to remind the model of the framework/requirements.

    Returns:
        List of LangChain tools: [update_plan, read_plan]
    """
    plan_path: Path = vfs.root_path / ".rudraanvil" / "PLAN.md"

    @tool
    def update_plan(plan_markdown: str) -> str:
        """Write or overwrite the agent's task plan to .rudraanvil/PLAN.md.

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
        # Write directly to disk — bypasses VFS cache so read_plan stays in sync
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan_markdown, encoding="utf-8")
        # Invalidate VFS cache — pop both separator styles (Windows uses \, POSIX uses /)
        vfs.files.pop(str(Path(".rudraanvil") / "PLAN.md"), None)
        vfs.files.pop(".rudraanvil/PLAN.md", None)

        pending = [
            line.strip().replace("- [ ]", "").strip()
            for line in plan_markdown.splitlines()
            if "- [ ]" in line
        ]
        if pending:
            files_list = ", ".join(pending)
            task_hint = f" for: {task}" if task else ""
            next_action = (
                f"Now call task(subagent_type='general-purpose', description='Write these files{task_hint}: "
                f"{files_list}. Write COMPLETE, production-ready code for ALL of them.'). "
                "The subagent will write all files. Do NOT call write_file yourself."
            )
        else:
            next_action = "All items already checked off."
        return f"Plan saved. {next_action}"

    @tool
    def read_plan() -> str:
        """Read the current task plan from .rudraanvil/PLAN.md.

        Always reads from disk so it reflects changes made by edit_file.

        Returns:
            Current plan markdown, or a message if no plan exists yet.
        """
        # Read directly from disk — never from VFS cache, which may be stale
        if plan_path.exists():
            return plan_path.read_text(encoding="utf-8")
        return "No plan found. Call update_plan() to create one first."

    return [update_plan, read_plan]
