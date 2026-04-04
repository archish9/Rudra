"""BlockTaskToolMiddleware — intercepts `task` subagent tool calls and returns
a redirect error so the main agent is forced to write files itself.

Problem: qwen3:14b ignores text-based "NEVER use the task tool" instructions
and immediately delegates to the general-purpose subagent, which then cycles
through write_todos status updates without ever calling write_file.

Solution: Use `wrap_tool_call` to intercept the result of any `task` tool call
and replace it with an error ToolMessage directing the model to use write_file
directly. The subagent still runs (we can't prevent execution in wrap_tool_call),
but its result is discarded — the model sees only the redirect error.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import AgentMiddleware


class BlockTaskToolMiddleware(AgentMiddleware):
    """Intercepts `task` subagent tool calls and redirects to write_file."""

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        if request.tool_call["name"] == "task":
            return ToolMessage(
                content=(
                    "ERROR: The task subagent tool is disabled. "
                    "You must write all files yourself using write_file() directly — do not delegate. "
                    "Call write_file() now with the COMPLETE content of the first file in your plan."
                ),
                tool_call_id=request.tool_call["id"],
            )
        return result

    async def awrap_tool_call(self, request, handler):
        result = await handler(request)
        if request.tool_call["name"] == "task":
            return ToolMessage(
                content=(
                    "ERROR: The task subagent tool is disabled. "
                    "You must write all files yourself using write_file() directly — do not delegate. "
                    "Call write_file() now with the COMPLETE content of the first file in your plan."
                ),
                tool_call_id=request.tool_call["id"],
            )
        return result
