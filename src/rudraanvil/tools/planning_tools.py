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
        """Write or overwrite the agent's task plan to .rudraanvil/PLAN.md.

        Use this INSTEAD of write_todos to track your task checklist.
        Write a Markdown checklist using standard checkbox syntax:
          - `- [ ] task description` for pending items
          - `- [x] task description` for completed items

        Example:
            update_plan(
                "# Build Plan\\n"
                "- [ ] Create src/main.py\\n"
                "- [ ] Create requirements.txt\\n"
                "- [ ] Create README.md\\n"
            )

        After writing, use read_plan() to confirm, then use edit_file to
        check off items as you complete them:
            edit_file('.rudraanvil/PLAN.md', '- [ ] Create src/main.py', '- [x] Create src/main.py')

        Args:
            plan_markdown: Full markdown content of the plan/checklist.
                           Should include a heading and a checkbox list.

        Returns:
            Confirmation message with next steps
        """
        vfs.write_file(_PLAN_PATH, plan_markdown)
        return (
            f"Plan written to {_PLAN_PATH}. "
            "Use read_plan() to review it. "
            "Use edit_file to check off items as you complete each one: "
            "edit_file('.rudraanvil/PLAN.md', '- [ ] <task>', '- [x] <task>')"
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
