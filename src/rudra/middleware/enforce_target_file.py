"""EnforceTargetFileMiddleware — block write_file calls for wrong files.

Coder models often write extra files they weren't assigned (e.g., writes
main.py when told to write schemas.py). This middleware physically blocks
those writes so the coder is forced to write the correct target file.

Created fresh per file with the exact target path from current_task.md.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from langchain_core.messages import ToolMessage
from langchain.agents.middleware.types import AgentMiddleware


class EnforceTargetFileMiddleware(AgentMiddleware):
    """Block write_file calls that don't match the assigned target file."""

    def __init__(self, target_file: str) -> None:
        self._target = target_file
        self._target_basename = PurePosixPath(target_file).name

    def _extract_file_path(self, args: dict) -> str:
        return args.get("file_path", args.get("filename", args.get("path", "")))

    def _is_allowed(self, name: str, file_path: str) -> bool:
        if name != "write_file":
            return True
        normalized = file_path.lstrip("/").replace("\\", "/")
        return (
            normalized == self._target
            or normalized.endswith("/" + self._target)
            or PurePosixPath(normalized).name == self._target_basename
        )

    def wrap_tool_call(self, request, handler):
        name = request.tool_call.get("name")
        if name == "write_file":
            args = request.tool_call.get("args", {})
            file_path = self._extract_file_path(args)
            if not self._is_allowed(name, file_path):
                return ToolMessage(
                    content=(
                        f"BLOCKED: You must write '{self._target}', not '{file_path}'.\n"
                        f"Call write_file(file_path='{self._target}', content='...complete code...') now."
                    ),
                    tool_call_id=request.tool_call["id"],
                )
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        name = request.tool_call.get("name")
        if name == "write_file":
            args = request.tool_call.get("args", {})
            file_path = self._extract_file_path(args)
            if not self._is_allowed(name, file_path):
                return ToolMessage(
                    content=(
                        f"BLOCKED: You must write '{self._target}', not '{file_path}'.\n"
                        f"Call write_file(file_path='{self._target}', content='...complete code...') now."
                    ),
                    tool_call_id=request.tool_call["id"],
                )
        return await handler(request)
