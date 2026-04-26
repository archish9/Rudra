"""ContinueAfterWriteMiddleware — appends a continuation prompt to write_file
results so the subagent doesn't stop too early OR loop endlessly.

Problem A: After write_file returns, local models (qwen3:14b) may stop and
treat the successful result as task completion, ending the loop.

Problem B: With a naïve "keep writing" nudge the model loses track of which
files it already wrote (as context grows) and rewrites the same files forever.

Solution: Track every file written in this middleware instance. Each nudge
includes the running "files written so far" list so the model can compare
against the task description and know exactly which files remain. On a
re-write attempt (same path seen before), inject a strong WARNING so the
model stops rewriting and moves on to genuinely missing files.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import AgentMiddleware


class ContinueAfterWriteMiddleware(AgentMiddleware):
    """Stateful continuation middleware for the file-writing agent."""

    def __init__(self, task: str = "", plan_path: Path | None = None):
        self.task = task
        self._plan_path = plan_path
        # Ordered list of unique file paths written in this session
        self._files_written: list[str] = []

    def _get_remaining(self) -> list[str]:
        """Read PLAN.md and return pending filenames, excluding files already written this session."""
        if self._plan_path and self._plan_path.exists():
            pending = [
                line.strip().replace("- [ ]", "").strip()
                for line in self._plan_path.read_text(encoding="utf-8").splitlines()
                if "- [ ]" in line
            ]
            # Exclude files already written in this session so the nudge
            # never tells the model to rewrite a file it just wrote.
            return [f for f in pending if f not in self._files_written]
        return []

    @staticmethod
    def _normalize(file_path: str) -> str:
        return file_path.lstrip("/\\") if file_path else ""

    def _augment_write(self, result: ToolMessage, request) -> ToolMessage:
        raw_path = request.tool_call.get("args", {}).get("file_path", "")
        file_path = self._normalize(raw_path)

        already_written = file_path in self._files_written
        if file_path and not already_written:
            self._files_written.append(file_path)

        written_list = ", ".join(self._files_written) if self._files_written else "none"
        remaining = self._get_remaining()

        if already_written:
            continuation = (
                f"\n\nWARNING: '{file_path}' was already written in this session.\n"
                f"Files written so far: {written_list}\n"
                "Do NOT rewrite files already written. "
            )
            if remaining:
                continuation += (
                    f"Remaining from plan: {', '.join(remaining)}\n"
                    f"Write the next one NOW: write_file(file_path='{remaining[0]}', content='<complete code here>')"
                )
            else:
                continuation += "All planned files are written. STOP."
        else:
            continuation = f"\n\nFile written. Files written so far: {written_list}.\n"
            if remaining:
                continuation += (
                    f"Remaining from plan: {', '.join(remaining)}\n"
                    f"Write the next one NOW: write_file(file_path='{remaining[0]}', content='<complete code here>')"
                )
            else:
                continuation += "All planned files are written. STOP — do not add extra files."

        return ToolMessage(
            content=result.content + continuation,
            tool_call_id=request.tool_call["id"],
        )

    @staticmethod
    def _is_error(result: ToolMessage) -> bool:
        c = result.content or ""
        return "Error:" in c or "error" in c.lower()[:30]

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        if request.tool_call.get("name") == "write_file" and not self._is_error(result):
            return self._augment_write(result, request)
        return result

    async def awrap_tool_call(self, request, handler):
        result = await handler(request)
        if request.tool_call.get("name") == "write_file" and not self._is_error(result):
            return self._augment_write(result, request)
        return result
