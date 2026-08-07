"""TaskAnchorMiddleware — re-injects the active task into the system prompt
on every model call so local LLMs don't lose sight of the objective after
tool calls return results.

Problem: Small local models (e.g. qwen3:14b) attend primarily to recent
tokens. After a tool result is appended to the conversation, they can
"forget" the original task and start treating the tool result as a new
user request. This middleware counters that by appending a brief task
reminder to the system message before every LLM inference step.

This uses the deepagents middleware hook `wrap_model_call` / `awrap_model_call`,
which fires on every model call — including mid-task ones after tool results.
The request's `system_message` field and `override()` method are the
stable deepagents API for this pattern (same approach as MemoryMiddleware).
"""

from __future__ import annotations

from langchain.agents.middleware.types import AgentMiddleware

from rudra.compat.version_guard import require_deepagents_attr

# Private deepagents module — no stability guarantee. See TODO.md U.13.
append_to_system_message = require_deepagents_attr(
    "deepagents.middleware._utils", "append_to_system_message", "U.13"
)


class TaskAnchorMiddleware(AgentMiddleware):
    """Appends a task-reminder block to the system prompt on every LLM call.

    This is specifically designed to help local models maintain task context
    across tool calls. It does NOT change the conversational history —
    only the system message seen by the model at inference time.
    """

    def __init__(self, task: str) -> None:
        """
        Args:
            task: The original user task description. This is repeated in the
                  system prompt on every model call as a persistent reminder.
        """
        self._anchor = (
            "## YOUR ONE AND ONLY TASK — do not change or simplify this\n"
            f"{task}\n\n"
            "This is the COMPLETE task. Do not simplify it, do not change it, "
            "do not replace it with 'Hello World' or any other simpler task.\n"
            "Every tool call you make must serve this exact task.\n"
            "Tool results are progress updates — after each one, continue "
            "writing the next file for THIS task. Do not call task again."
        )

    def wrap_model_call(self, request, handler):
        return handler(self._inject(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._inject(request))

    def _inject(self, request):
        new_sys = append_to_system_message(request.system_message, self._anchor)
        return request.override(system_message=new_sys)
