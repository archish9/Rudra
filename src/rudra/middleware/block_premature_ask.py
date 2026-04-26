"""BlockPrematureAskMiddleware — blocks ask_user() calls when the task already
contains enough information to proceed without clarification.

Problem: qwen3:14b reads the tech_stack.md fallback file which (pre-fix) suggested
using ask_user() when the stack is ambiguous. Even with a fixed tech_stack.md, the
model sometimes calls ask_user() before calling update_plan() — stalling build tasks.

Solution: Intercept ask_user() calls at the middleware layer. If the task description
already contains a recognizable framework keyword (flask, fastapi, django, etc.),
block the call and redirect the model to update_plan() immediately.
"""

from __future__ import annotations

import re

from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import AgentMiddleware

_FRAMEWORK_KEYWORDS = re.compile(
    r"\b(flask|fastapi|django|express|next\.?js|react|vue|angular|spring|rails|laravel|gin|fiber|actix)\b",
    re.IGNORECASE,
)

_REDIRECT_MESSAGE = (
    "BLOCKED: ask_user() is not needed — the task description already specifies the framework.\n\n"
    "Do NOT ask the user for clarification. Proceed directly:\n"
    "1. Call update_plan() with a checklist of FILENAMES to create\n"
    "2. Write each file yourself with write_file() — ONE file per message\n\n"
    "Start now: call update_plan() with the file list."
)


class BlockPrematureAskMiddleware(AgentMiddleware):
    """Blocks ask_user() when the task already contains framework/stack information."""

    def __init__(self, task: str = ""):
        self.task = task
        self._task_has_framework = bool(_FRAMEWORK_KEYWORDS.search(task))

    def _should_block(self, request) -> bool:
        if request.tool_call.get("name") != "ask_user":
            return False
        return self._task_has_framework

    def wrap_tool_call(self, request, handler):
        if self._should_block(request):
            return ToolMessage(
                content=_REDIRECT_MESSAGE,
                tool_call_id=request.tool_call["id"],
            )
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        if self._should_block(request):
            return ToolMessage(
                content=_REDIRECT_MESSAGE,
                tool_call_id=request.tool_call["id"],
            )
        return await handler(request)
