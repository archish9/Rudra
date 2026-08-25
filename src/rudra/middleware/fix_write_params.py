"""FixWriteParamsMiddleware — auto-correct common file tool parameter mistakes.

Fixes applied (in order):
1. Parameter rename: `filename` or `path` → `file_path` for write/edit tools
2. Markdown fence stripping: removes ```lang ... ``` wrappers from file content
3. Split dot-segment repair: `/. rudra/x` → `/.rudra/x` on every path arg
4. Sandbox prefix stripping: removes known LLM-hallucinated path prefixes
   (/testbed/, /workspace/, /home/user/, etc.) from all file tool paths

It also REFUSES one call rather than repairing it: a write whose only
purpose is to bring a directory into being (OPEN-22). There is nothing to
repair there -- the model wants a directory and `write_file` makes files --
so the only useful answer is an error it can act on.
"""

from __future__ import annotations

import re

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.compat.path_constants import SANDBOX_PREFIXES

# Info string is anything up to the newline — matches the coverage that
# compat/overwrite_backend.py used to provide before U.3 deleted it (its
# regex was `^```[^\n]*\n(.*)\n```$`; the file is gone, so no line is cited).
# See TODO.md U.15.
_FENCE_RE = re.compile(r"^```([^\n]*)\n?(.*?)```\s*$", re.DOTALL)

# Info strings that mark the outer fence as a *wrapper* around a whole
# document rather than one code block inside it. A markup info string is
# the only thing that distinguishes "model wrapped my README in ```markdown"
# from "this README's own first line is a ```bash block" -- both shapes open
# and close with a fence and carry fences inside. See TODO.md OPEN-3.
_MARKUP_INFO = {"markdown", "md", "mdx", "rst", "text", "txt", ""}

# Tools that use file_path but model sometimes passes filename/path instead
_FILE_TOOLS = {"write_file", "edit_file"}

# All tools that carry a path argument (superset of _FILE_TOOLS)
_PATH_TOOLS = {"write_file", "edit_file", "read_file", "ls", "glob"}

# Path-bearing argument names to clean across all path tools
_PATH_ARG_KEYS = ("file_path", "path", "pattern")


def _strip_fences(content: str) -> str:
    """Remove an outer ```lang ... ``` wrapper, but never real fenced content.

    Strip only when the interior carries no fence of its own, or when the
    info string names a markup type -- that is exactly what separates a
    wrapper the model added from a Markdown file whose own content opens
    and closes with fences (TODO.md OPEN-3).
    """
    m = _FENCE_RE.match(content.strip())
    if m is None:
        return content
    info, inner = m.group(1).strip().lower(), m.group(2)
    first = info.split()[0] if info else ""
    if "```" in inner and first not in _MARKUP_INFO:
        return content
    return inner


# A path segment that is exactly `.` followed by whitespace and then a
# name: `/. rudra/AGENTS.md` for `/.rudra/AGENTS.md` (OPEN-8). Anchored to
# `^` or `/` so a dot INSIDE a segment is never touched, and requiring a
# non-space, non-slash after the whitespace so there is actually a segment
# to rejoin.
#
# Narrow on purpose. Stripping whitespace from paths generally would break
# `My Documents`, which is a real directory on every desktop OS; what makes
# THIS safe is that `. rudra` -- a directory whose name is dot, space, name
# -- is not a thing anyone creates, while a model splitting `.rudra` into
# two tokens is measured behaviour.
_SPLIT_DOT_RE = re.compile(r"(^|/)\.[ \t]+(?=[^/\s])")


def _repair_split_dot_segment(path: str) -> str:
    """Rejoin a dotfile segment the model split with whitespace."""
    return _SPLIT_DOT_RE.sub(r"\1.", path)


def _strip_sandbox_prefix(path: str) -> str:
    """Strip known sandbox/training-env prefixes from an absolute POSIX path."""
    if not path.startswith("/"):
        return path
    for prefix in SANDBOX_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


_DIRECTORY_PLACEHOLDER = (
    "REJECTED: `{path}` has no file extension and its content is only a "
    "comment, which is how an agent writes a file when what it wants is a "
    "directory. Directories here are created implicitly -- writing "
    "`{path}/<name>.py` creates `{path}/` on the way. Write the file you "
    "actually want; do not write a placeholder to make its directory."
)


def _is_directory_placeholder(path: str, content: object) -> bool:
    """Is this a write whose only purpose is to create a directory?

    Held to the same bar as `_repair_split_dot_segment`: it must not be able
    to fire on something a person would write by hand. Three conditions, all
    required.

    * No suffix -- `tests`, not `tests.py`.
    * Not a dotfile -- `.gitignore` and `.env` have no suffix either, and a
      `.gitignore` of nothing but comments is useless but legal.
    * Content is at most three lines and every one of them is blank or a
      `#` comment.

    `LICENSE`, `Makefile` and `Dockerfile` are all suffix-less and all carry
    real content, so none of them match. What matches is the measured shape:
    `write_file('/tests', '# This is a placeholder to create the directory')`
    (OPEN-22), which produced a 47-byte FILE named `tests` and doomed the
    five later tasks that needed `tests/` to be a directory.
    """
    if not isinstance(content, str):
        return False
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if not name or name.startswith(".") or "." in name:
        return False
    lines = [line.strip() for line in content.strip().splitlines()]
    if len(lines) > 3:
        return False
    return bool(lines) and all(not line or line.startswith("#") for line in lines)


class FixWriteParamsMiddleware(AgentMiddleware):
    """Auto-correct file tool parameters: rename args, clean paths, strip fences.

    D4 splits this middleware in two:

    * **Always on** — markdown-fence stripping, `filename`/`path` ->
      `file_path` aliasing, and split dot-segment repair. Fence stripping is
      *required*, not a small-model workaround: 0.7.4's
      `FilesystemBackend.write()` no longer strips, so nothing else in the
      stack does (TODO.md U.3, U.15). The dot-segment repair is always on
      because it cannot fire on a path anyone would write by hand, and
      because the write path fails SILENTLY without it -- a mangled read
      errors, a mangled write creates a junk directory and reports success
      (TODO.md OPEN-8).
    * **Opt-in** — sandbox-prefix stripping, behind `[compat] sandbox_paths`.
      It was built for a training-sandbox path shape that a 32B model on a
      normal machine does not emit, and it rewrites legitimate absolute
      paths when it does fire.
    """

    def __init__(self, strip_sandbox_prefixes: bool = False) -> None:
        super().__init__()
        self.strip_sandbox_prefixes = strip_sandbox_prefixes

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
            if isinstance(args.get("content"), str):
                # isinstance, not `in`: a model emitting a list for
                # `content` raised AttributeError inside wrap_tool_call
                # instead of the tool returning a validation error it could
                # correct. The sandbox branch below already guards this way
                # (CR-E11).
                args["content"] = _strip_fences(args["content"])

        # Always on, and after the alias above so it cleans the key the
        # model's path actually ended up under. `content` is deliberately
        # not in _PATH_ARG_KEYS: file content is data, and a line reading
        # `. rudra` inside a document is not a path.
        for key in _PATH_ARG_KEYS:
            if key in args and isinstance(args[key], str):
                args[key] = _repair_split_dot_segment(args[key])

        # Strip sandbox prefixes from all path-bearing args (second defense
        # layer). Opt-in per D4 — see the class docstring.
        if self.strip_sandbox_prefixes:
            for key in _PATH_ARG_KEYS:
                if key in args and isinstance(args[key], str):
                    cleaned = _strip_sandbox_prefix(args[key])
                    if cleaned != args[key]:
                        args[key] = cleaned

        request.tool_call["args"] = args
        return request

    def _refusal(self, request):
        """A ToolMessage refusing a directory-placeholder write, or None."""
        call = request.tool_call
        if call.get("name") != "write_file":
            return None
        args = call.get("args", {})
        path = args.get("file_path")
        if not isinstance(path, str) or not _is_directory_placeholder(path, args.get("content")):
            return None
        return ToolMessage(
            content=_DIRECTORY_PLACEHOLDER.format(path=path.rstrip("/")),
            tool_call_id=call.get("id", ""),
            name="write_file",
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        request = self._fix_args(request)
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        request = self._fix_args(request)
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
