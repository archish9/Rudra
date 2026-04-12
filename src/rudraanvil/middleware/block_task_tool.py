"""BlockTaskToolMiddleware — intercepts `task` subagent tool calls and forces
the main agent to write all files itself using write_file().

Problem: qwen3:14b always tries to delegate work via the `task` tool.
When blocked with "use write_todos first", the model:
  1. Struggles with write_todos schema (2-3 failed attempts per cycle)
  2. After write_todos succeeds, calls task AGAIN (treats write_todos as
     planning and task as execution — the block teaches the wrong pattern)

Solution: Skip write_todos entirely in the redirect message. Tell the model
to call write_file() directly for each file, with the correct parameter name
(file_path, not path) and explicit instruction to write COMPLETE code.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import AgentMiddleware

_BLOCK_MESSAGE = (
    "ERROR: The task subagent tool is disabled. You must write all files yourself.\n\n"
    "Check your plan and write the next file:\n"
    "1. Call read_plan() to see which files are still pending (marked '- [ ]')\n"
    "   If no plan exists yet, call update_plan() first with a full checklist\n"
    "2. Write the first PENDING file: write_file(file_path='...', content='COMPLETE code')\n"
    "   ONE file only — stop and wait for the result\n"
    "3. Check it off: edit_file('.rudraanvil/PLAN.md', old_string='- [ ] filename', new_string='- [x] filename')\n\n"
    "Rules:\n"
    "- Write COMPLETE, working code — no Hello World stubs, no TODOs\n"
    "- Use 'file_path' (NOT 'path') as the parameter name\n"
    "- Do NOT call task again — write the files yourself\n\n"
    "Call read_plan() now, then write the next pending file."
)


class BlockTaskToolMiddleware(AgentMiddleware):
    """Intercepts `task` tool calls and redirects directly to write_file."""

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] == "task":
            return ToolMessage(
                content=_BLOCK_MESSAGE,
                tool_call_id=request.tool_call["id"],
            )
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] == "task":
            return ToolMessage(
                content=_BLOCK_MESSAGE,
                tool_call_id=request.tool_call["id"],
            )
        return await handler(request)
