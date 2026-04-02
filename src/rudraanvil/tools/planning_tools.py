"""Planning tools for filesystem-based context management.

Replaces the deepagents built-in `write_todos` tool with a filesystem-backed
equivalent that writes to .rudraanvil/PLAN.md, preventing the agent's task
plan from bloating the context window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem

_PLAN_PATH = ".rudraanvil/PLAN.md"


def create_planning_tools(vfs: VirtualFileSystem) -> list:
    """Create planning tools that write to .rudraanvil/PLAN.md.

    These tools replace the built-in write_todos tool. Instead of storing
    the plan in the LLM's message history, the plan is written to disk and
    read back on demand — giving the agent unlimited planning memory with
    zero ongoing token cost.

    Args:
        vfs: The virtual filesystem anchored to the project root

    Returns:
        List of LangChain tools: [update_plan, read_plan]
    """

    @tool
    def update_plan(plan_markdown: str) -> str:
        """Write the initial task plan to .rudraanvil/PLAN.md.

        Call this ONCE at the start with ALL items unchecked (- [ ]).
        Do NOT pre-mark any items as done — you haven't written the files yet.

        Example:
            update_plan(
                "# Build Plan\\n"
                "- [ ] Create src/main.py\\n"
                "- [ ] Create requirements.txt\\n"
                "- [ ] Create README.md\\n"
            )

        After creating the plan, do NOT call update_plan() again.
        Instead, for each item in order:
          1. Call write_file() with the COMPLETE file content
          2. Then check it off: edit_file('.rudraanvil/PLAN.md', '- [ ] Create src/main.py', '- [x] Create src/main.py')

        Args:
            plan_markdown: Markdown checklist. ALL items must be unchecked (- [ ]).

        Returns:
            Confirmation and next instruction
        """
        existing = vfs.read_file(_PLAN_PATH)
        if existing is not None and "- [ ]" in existing and "- [ ]" not in plan_markdown:
            # Model is trying to overwrite the plan with all items pre-marked done,
            # bypassing the actual file-writing work.
            return (
                "ERROR: You cannot mark items done without first writing the files. "
                "The plan already has unchecked items. "
                "Your next step is to call write_file() for the first unchecked item. "
                "Use read_plan() to see what needs to be done, then write the file."
            )

        vfs.write_file(_PLAN_PATH, plan_markdown)
        return (
            f"Plan written to {_PLAN_PATH}. "
            "NEXT: write the first file. Call write_file(file_path='...', content='...complete code...'), "
            "then check it off with edit_file('.rudraanvil/PLAN.md', '- [ ] <task>', '- [x] <task>'). "
            "Do NOT call update_plan() again — use edit_file() to check off items."
        )

    @tool
    def read_plan() -> str:
        """Read the current task plan from .rudraanvil/PLAN.md.

        Call this to check your next pending item without re-reading the
        entire conversation history. This is the key to keeping context small.

        Returns:
            Current plan content as markdown, or a message if no plan exists
        """
        content = vfs.read_file(_PLAN_PATH)
        if content is None:
            return (
                "No plan found at .rudraanvil/PLAN.md. "
                "Use update_plan() to create your task plan first."
            )
        return content

    return [update_plan, read_plan]
