"""FixWriteParamsMiddleware — auto-correct common file tool parameter mistakes.

Fixes applied (in order):
1. Parameter rename: `filename` or `path` → `file_path` for write/edit tools
2. Sandbox prefix stripping: removes known LLM-hallucinated path prefixes
   (/testbed/, /workspace/, /home/user/, etc.) from all file tool paths
3. Markdown fence stripping: removes ```lang ... ``` wrappers from file content
"""

from __future__ import annotations

import re

from langchain.agents.middleware.types import AgentMiddleware

from rudra.compat.path_constants import SANDBOX_PREFIXES


# Info string is anything up to the newline — matches the coverage that
# compat/overwrite_backend.py:43 used to provide before U.3 deleted it.
# See TODO.md U.15.
_FENCE_RE = re.compile(r"^```[^\n]*\n?(.*?)```\s*$", re.DOTALL)

# Tools that use file_path but model sometimes passes filename/path instead
_FILE_TOOLS = {"write_file", "edit_file"}

# All tools that carry a path argument (superset of _FILE_TOOLS)
_PATH_TOOLS = {"write_file", "edit_file", "read_file", "ls", "glob"}

# Path-bearing argument names to clean across all path tools
_PATH_ARG_KEYS = ("file_path", "path", "pattern")


def _strip_fences(content: str) -> str:
    m = _FENCE_RE.match(content.strip())
    return m.group(1) if m else content


def _strip_sandbox_prefix(path: str) -> str:
    """Strip known sandbox/training-env prefixes from an absolute POSIX path."""
    if not path.startswith("/"):
        return path
    for prefix in SANDBOX_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


class FixWriteParamsMiddleware(AgentMiddleware):
    """Auto-correct file tool parameters: rename args, clean paths, strip fences."""

    def _fix_args(self, request):
        name = request.tool_call.get("name")
        if name not in _FILE_TOOLS and name not in _PATH_TOOLS:
            return request

        args = dict(request.tool_call.get("args", {}))

        if name in _FILE_TOOLS:
            # Fix parameter name: filename/path → file_path
            if "filename" in args and "file_path" not in args:
                args["file_path"] = args.pop("filename")
            elif "path" in args and "file_path" not in args:
                args["file_path"] = args.pop("path")
            # Strip markdown fences from file content
            if "content" in args:
                args["content"] = _strip_fences(args["content"])

        # Strip sandbox prefixes from all path-bearing args (second defense layer)
        for key in _PATH_ARG_KEYS:
            if key in args and isinstance(args[key], str):
                cleaned = _strip_sandbox_prefix(args[key])
                if cleaned != args[key]:
                    args[key] = cleaned

        request.tool_call["args"] = args
        return request

    def wrap_tool_call(self, request, handler):
        return handler(self._fix_args(request))

    async def awrap_tool_call(self, request, handler):
        return await handler(self._fix_args(request))
