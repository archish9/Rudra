"""MachinePathMiddleware — answer a file tool asked for the machine's filesystem.

**The defect (OPEN-91).** Run `fc543fb2b82f`, task t7: the coder decided its
work was already done and wanted to verify it by running the tests. It has no
shell, so it went looking for a Python interpreter with the file tools --
`/usr/bin/python*`, `/usr/local/bin/python*`, `/opt/homebrew/bin/python*`,
`/bin/python*`, 36 of them across 2,704 seconds.

Every one could only ever fail. Rudra builds every backend with
`virtual_mode=True`, so a leading `/` is the PROJECT root: the model's
`/usr/bin/python*` means `<project>/usr/bin/python*`, and the machine's
`/usr/bin` is not reachable through any file tool, on any OS. The answer it
got was the bare string `No files found` -- a **successful** result, which
reads as *look somewhere else*, and it did, for 45 minutes.

**Why this is not a fourth prompt paragraph.** At the time of the run,
`_CODER_PROMPT`'s `## YOU CANNOT RUN COMMANDS` said that searching the
filesystem for an interpreter was a slow way of finding out there is no
shell, and `_GENERAL_PURPOSE_PROMPT` said the same thing again. Both were in
context; the model did it anyway. That is OPEN-17's finding for the third
recorded time -- a prompt cannot outrank a prior -- and TODO.md's first
lesson says not to retry it.

**The seam that has worked is the tool's own answer.** OPEN-25 replaced a
prohibition with an explanation of where commands actually run and the bad
shell paths went to zero; OPEN-81 measured the same thing for file paths in
the same run. So this changes what the TOOL says, at the moment of the
mistake, and costs one model round trip instead of forty. `_CODER_PROMPT`'s
block was rewritten with this item to lead with the mechanism rather than the
prohibition -- the same reframing, one layer up -- but that half is a
companion, not the fix.

**Every uncertain case declines**, which is `gutter_indent.py`'s rule and the
same reasoning: a project MAY hold a `usr/bin/`, so the hint is appended only
where the result found nothing, and never in place of a real answer. It is
APPENDED and never prepended, because `subagents/runner.py`'s
MAX_CONSECUTIVE_FAILURES and `repeat_guard.py::_is_error` both key on content
that starts with `Error` -- rewriting the front of a failure would silently
disarm two other guards.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from rudra.compat.virtual_paths import looks_windows_absolute, virtual_to_host

logger = logging.getLogger(__name__)

MACHINE_HINT_NOTICE = "machine-path"
"""The `name` a hint carries in the trace and the debug log.

Named here because it is what a maintainer greps for -- `"name":
"machine-path"` in `debug-<id>.jsonl` counts how often a run went hunting,
which is the number that says whether this fix paid for itself.
"""

# Which argument of each tool is a PATH. Deliberately not "every string
# argument": `grep`'s `pattern` is a regular expression, in which `/usr/` is
# a character sequence and not a directory.
_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "glob": ("pattern", "path"),
    "grep": ("path",),
    "ls": ("path",),
    "read_file": ("file_path",),
}

# First segments that name the machine rather than a project. Every one of
# these appears in run `fc543fb2b82f`'s hunt or is the same directory under
# another OS's spelling -- the set is by SHAPE, so a Windows-shaped path is
# recognised on macOS and a `/usr` one on Windows (CLAUDE.md 1.8).
#
# `tmp` is deliberately absent: `/tmp/` is a hallucinated sandbox prefix
# `[compat] sandbox_paths` already owns, and a project really can hold a
# `tmp/`. So is `run`, for the second half of that reason alone.
MACHINE_DIRS = frozenset(
    {
        "bin",
        "sbin",
        "usr",
        "opt",
        "etc",
        "var",
        "lib",
        "lib64",
        "proc",
        "sys",
        "dev",
        "mnt",
        "home",
        "root",
        "private",
        "Users",
        "Applications",
        "Library",
        "System",
        "Volumes",
    }
)

_HINT = (
    "\n\nNote: {tool} searches only inside this project, which is at {root}. "
    'A leading "/" means THAT directory, so "{spelling}" was looked for at '
    '"{resolved}", which does not exist. The machine\'s own "{anchor}" is not '
    "reachable from any file tool here, on any OS."
)

# The half that answers the question the model actually had. `_CODER_PROMPT`
# says a version of this; the difference is that here it is the ANSWER to
# what the model just tried, in the tool result, rather than a clause in a
# system prompt it read before it had the question.
_NO_SHELL = (
    " You have no shell tool, so there is no interpreter, binary or test runner "
    "to find. When you stop, a verification gate runs the project's linter, type "
    "checker and full test suite and calls you again with the exact failure text "
    "if anything fails -- stopping IS how you find out whether your work is "
    "correct."
)

# And for a spec that does hold `execute`, whose "/" IS the machine (OPEN-21).
# Telling the tester there is no way to run anything would be false.
_HAS_SHELL = (
    " `execute` is the one tool here that sees the machine's filesystem; the file tools do not."
)

# What an empty result looks like at this boundary. `_format_file_paths`
# (deepagents/middleware/filesystem.py:775) returns the first for glob and
# grep; a read or a listing of something absent is reported as ordinary
# content beginning with "Error" rather than by raising
# (backends/filesystem.py:447).
_EMPTY = "No files found"


def machine_root(value: str) -> str | None:
    """The machine directory this spelling names, or None.

    Answered by SHAPE and never by host OS, the rule
    `compat/virtual_paths.py` is built on: a mac user's model and a Windows
    user's model make the same mistakes, because both are guessing from
    training data rather than from the machine they are running on.

    A Windows drive-absolute or UNC path is always one of these -- the
    backend strips the anchor, so `C:\\Windows\\py.exe` is
    `<project>/Windows/py.exe` and the drive is gone whatever followed it.
    """
    if not value:
        return None
    if looks_windows_absolute(value):
        return PureWindowsPath(value).anchor
    # A backslash-separated spelling with no drive (`\\usr\\bin`) is read as
    # POSIX absolute, exactly as `virtual_to_relative` reads it.
    plain = value.replace("\\", "/")
    if not plain.startswith("/"):
        return None
    parts = PurePosixPath(plain).parts
    if len(parts) < 2:
        return None
    return f"/{parts[1]}" if parts[1] in MACHINE_DIRS else None


class MachinePathMiddleware(AgentMiddleware):
    """Append an explanation when a project-rooted tool is asked for the machine.

    Fires only where the tool found nothing, so a project that really does
    hold `usr/bin/` is answered with its own files and never with this.
    """

    def __init__(
        self,
        role: str | None = None,
        *,
        project_path: Any = None,
        has_shell: bool = False,
        trace: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.project_path = project_path
        self.has_shell = has_shell
        self.trace = trace

    def _spelling(self, name: str, args: dict) -> tuple[str, str] | None:
        """The path argument that names the machine, with its root, or None."""
        for key in _PATH_ARGS.get(name, ()):
            value = args.get(key)
            if not isinstance(value, str):
                continue
            anchor = machine_root(value)
            if anchor is not None:
                return value, anchor
        return None

    def _found_nothing(self, content: str) -> bool:
        """Did the tool answer with nothing?

        Two spellings, because glob and grep report an empty search as a
        success and a read or a listing reports a missing path as an error.
        """
        stripped = content.lstrip()
        return stripped.startswith(_EMPTY) or stripped.startswith("Error")

    def _announce(self, spelling: str, anchor: str) -> None:
        """Say that a tool result was rewritten (TODO.md lesson 5).

        Swallows its own failure: a run that did its work must not be
        reported failed because a log line could not be written
        (CLAUDE.md 8a).
        """
        try:
            if self.trace is not None:
                self.trace.notice(
                    f'explained that "{spelling}" is outside this project '
                    f'("{anchor}" is the machine\'s filesystem, which no file '
                    f"tool here can reach)",
                    role=self.role or "agent",
                    name=MACHINE_HINT_NOTICE,
                )
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("machine-path hint not announced", exc_info=True)

    def _hint(self, name: str, spelling: str, anchor: str) -> str | None:
        """The text to append, or None when we cannot say where "/" points."""
        if self.project_path is None:
            return None
        root = Path(self.project_path)
        resolved = virtual_to_host(spelling, root)
        if resolved is None:
            return None
        text = _HINT.format(
            tool=name,
            root=root,
            spelling=spelling,
            resolved=resolved,
            anchor=anchor,
        )
        return text + (_HAS_SHELL if self.has_shell else _NO_SHELL)

    def _annotate(self, request, result: Any) -> Any:
        """`result` with the explanation appended, or `result` unchanged.

        A result that is neither a string nor a message carrying one is
        returned untouched: a `Command` carries state updates this has no
        business rewriting, which is `gutter_indent.py`'s "cannot confirm
        performs the call" rule one boundary later.
        """
        call = request.tool_call
        name = call.get("name") or ""
        if name not in _PATH_ARGS:
            return result

        content = getattr(result, "content", result)
        if not isinstance(content, str) or not self._found_nothing(content):
            return result

        named = self._spelling(name, call.get("args") or {})
        if named is None:
            return result
        spelling, anchor = named

        hint = self._hint(name, spelling, anchor)
        if hint is None:
            return result

        self._announce(spelling, anchor)
        if result is content:
            return content + hint
        return result.model_copy(update={"content": content + hint})

    def wrap_tool_call(self, request, handler):
        return self._annotate(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._annotate(request, await handler(request))


__all__ = ["MACHINE_HINT_NOTICE", "MACHINE_DIRS", "MachinePathMiddleware", "machine_root"]
