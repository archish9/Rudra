"""FixWriteParamsMiddleware — auto-correct common file tool parameter mistakes.

qwen3:14b uses `filename` instead of `file_path` ~50% of the time on both
write_file and edit_file, and occasionally wraps content in markdown fences.
Fix both silently so the tool call succeeds without burning a retry turn.
"""

from __future__ import annotations

import re

from langchain.agents.middleware.types import AgentMiddleware


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_\-]*\n?(.*?)```\s*$", re.DOTALL)


def _strip_fences(content: str) -> str:
    m = _FENCE_RE.match(content.strip())
    return m.group(1) if m else content


# Tools that use file_path but model sometimes passes filename/path instead
_FILE_TOOLS = {"write_file", "edit_file"}


class FixWriteParamsMiddleware(AgentMiddleware):
    """Auto-correct write_file and edit_file parameter names; strip markdown fences."""

    def _fix_args(self, request):
        if request.tool_call.get("name") not in _FILE_TOOLS:
            return request
        args = dict(request.tool_call.get("args", {}))
        # Rename filename/path → file_path
        if "filename" in args and "file_path" not in args:
            args["file_path"] = args.pop("filename")
        elif "path" in args and "file_path" not in args:
            args["file_path"] = args.pop("path")
        # Strip markdown fences from content (write_file only)
        if "content" in args:
            args["content"] = _strip_fences(args["content"])
        request.tool_call["args"] = args
        return request

    def wrap_tool_call(self, request, handler):
        return handler(self._fix_args(request))

    async def awrap_tool_call(self, request, handler):
        return await handler(self._fix_args(request))
