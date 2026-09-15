"""ExecuteGuardMiddleware -- what the model is told about `execute`.

Two halves of one subject, which is why they share a module: what the model
believes about the shell before it calls it, and what it is told when that
belief has just cost it a turn.

**The root cause is upstream's tool description, not the model.** deepagents
builds every `execute` tool from one template
(deepagents/middleware/filesystem.py:1290-1298), and it reaches the model as
the tool schema:

    Executes a shell command in an isolated sandbox and returns combined
    stdout/stderr with the exit code (truncated if very large).

    Usage:
    - Quote paths containing spaces (e.g. cd "/path/with spaces").
    - Chain commands with ';' or '&&' ...
    - Use absolute paths and avoid `cd` so the working directory stays
      stable; ...

Three pushes toward the same mistake. It says **"isolated sandbox"**, so the
model reasons it is in a container and picks a container path. Its only
worked example is a **`cd`**. And **"use absolute paths"** contradicts
`subagents/registry.py`'s `_PATH_RULES` -- "Use RELATIVE paths only" /
"NEVER use absolute paths" -- so the model is holding two orders at once and
emits a blend of them. Measured 2026-08-25 (TODO.md OPEN-25): the tester ran
`cd /app && python -m pytest ...` and
`cd /mnt/w01d0e0020-... && python -m pytest ...`, neither directory existing,
two model calls each.

**Replaced, not argued with.** OPEN-17 measured three separate attempts to
overrule an injected prompt with prompt text, all of which lost: *a prompt
cannot outrank a prompt*. `_COMMAND_RULES` still states the fact -- the
shell is already in the project root -- but the contradicting text is removed
from the schema rather than contested in the system prompt.

**And the warning is keyed on the `cd`, never on the path.** D4 gated
sandbox-prefix stripping because `/src/` and `/tmp/` are hallucinated
prefixes *and* ordinary absolute directories, so rewriting them breaks
legitimate commands: `cp x /tmp/y` is a normal thing to run. The bug here is
not the prefix, it is that a `cd` to an absolute path is always wrong when
the shell already starts where the work is. Keying on the `cd` leaves
`cp x /tmp/y` structurally untouched, and catches `/mnt/<uuid>`, which is in
no prefix set. `SANDBOX_PREFIXES` only sharpens the wording once the `cd`
has already decided.

**A third thing the model is told, since OPEN-120: why pip refused.**
`permissions/env.py` sets `PIP_REQUIRE_VIRTUALENV=1`, so an install outside a
virtualenv now fails with pip's own sentence. Run `a04f89bd2ed6`'s tester had
already hunted `pip`, then `which pip3`, before it installed into the
machine's Python; refused and told nothing, the next hunt is `--user` or
`--isolated`. So the result says where a dependency goes instead. Keyed on
pip's OUTPUT, never on the command, for `permissions/env.py`'s reason: `pip`,
`pip3`, `python -m pip` and a Makefile target all print it.

Nothing here rewrites a command. The audit log records what the model asked
for, and what ran is what the user approved.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import BaseTool

from rudra.compat.path_constants import SANDBOX_PREFIXES
from rudra.compat.virtual_paths import looks_windows_absolute

logger = logging.getLogger(__name__)

EXECUTE = "execute"

RUDRA_EXECUTE_DESCRIPTION = """Executes a shell command and returns combined stdout/stderr with the exit code (truncated if very large).

The command runs in THIS PROJECT's root directory. That is already your
working directory -- you are there before the command starts.

Usage:
- NEVER `cd` before a command. There is nowhere else to go, and a `cd` to an
  absolute path will fail: this is not a container, and paths like /app,
  /workspace, /testbed, /root and /mnt/<id> do not exist here.
- Name files RELATIVE to the project root: `python -m pytest tests/test_x.py -v`.
- Quote paths containing spaces (e.g. "tests/my tests/test_x.py").
- Chain commands with ';' or '&&' (use '&&' when a command depends on the
  previous); do not use newlines except inside quoted strings.
- Use the optional timeout to override the default (0 disables it on
  backends that support that).
- Use the glob tool rather than `find`, the grep tool rather than `grep -r`,
  and read_file rather than cat/head/tail.

Only available on backends implementing SandboxBackendProtocol; otherwise it returns an error."""


# A `cd` at the start of the command or after a separator, with its argument
# quoted or bare. Anchored on `(?:^|[;&|]|\n)` so `abcd /app` is not a `cd`,
# and the separator class covers `&&`, `||` and a pipe without having to
# enumerate the pairs.
_CD_RE = re.compile(
    r"""(?:^|[;&|\n])\s*cd\s+(?:"([^"]*)"|'([^']*)'|([^\s;&|]+))""",
)

# deepagents formats every execute result with this line
# (filesystem.py:2764), including the offloaded path (:2786). It is the one
# signal both write, which is why it is the fallback when no artifact
# carries the exit code.
_FAILED_MARKER = "[Command failed with exit code"

# pip's own words when PIP_REQUIRE_VIRTUALENV refuses
# (pip/_internal/cli/base_command.py:213). tests/test_execute_guard.py runs
# the machine's real pip to pin this text, because pip owns it.
PIP_REFUSAL = "Could not find an activated virtualenv (required)."
PIP_REFUSED_NOTICE = "pip-refused"

_PIP_NOTE = (
    "\n\n[Rudra] pip refuses to install outside a virtualenv here, on purpose: "
    "installing into the machine's Python changes the user's own packages. "
    "Declare the dependency in requirements.txt or pyproject.toml instead -- "
    "Rudra installs declared dependencies into this project's .venv before "
    "every gate run."
)


def _is_absolute(path: str) -> bool:
    """Absolute by SHAPE, on any host (CLAUDE.md §1.8).

    A model emits the platform its training data suggests, not the one it is
    running on, so `C:\\workspace` has to be recognised on macOS.
    """
    return path.startswith(("/", "\\")) or looks_windows_absolute(path)


def _absolute_cd_target(command: str) -> str | None:
    """The first absolute path this command `cd`s into, or None."""
    for match in _CD_RE.finditer(command):
        target = next((group for group in match.groups() if group is not None), "")
        if target and _is_absolute(target):
            return target
    return None


def _content(result: Any) -> str:
    content = getattr(result, "content", result)
    return content if isinstance(content, str) else ""


def _failed(result: Any) -> bool:
    """Did the shell run this command and report a non-zero exit?

    The artifact is preferred because it is structured, but deepagents omits
    it when the exit code is unknown (filesystem.py:2777-2779). A result that
    never reached the shell at all -- a denial, an unsupported backend -- has
    neither, and is correctly not a failure of this kind.
    """
    artifact = getattr(result, "artifact", None)
    if isinstance(artifact, dict) and "exit_code" in artifact:
        return artifact["exit_code"] != 0
    return _FAILED_MARKER in _content(result)


def _note(target: str) -> str:
    known = any(target.startswith(prefix.rstrip("/")) for prefix in SANDBOX_PREFIXES)
    shape = (
        " That is a container path from training data, and there is no container here."
        if known
        else " There is no container here."
    )
    return (
        f"\n\n[Rudra] This command tried to `cd {target}`, and `{target}` does not exist."
        f"{shape} `execute` already runs in this project's root directory -- you are "
        f"there before the command starts. Run it again with no `cd` at all, naming "
        f"files relative to the project root."
    )


def _rewrite_execute_description(tools: Any) -> list[Any] | None:
    """A copy of `tools` with `execute` re-described, or None if unchanged.

    `model_copy` rather than assignment: the list belongs to the
    FilesystemMiddleware that built it, and one agent's rewrite must not
    reach another's tool object.
    """
    if not tools:
        return None
    rewritten: list[Any] = []
    changed = False
    for tool in tools:
        if isinstance(tool, BaseTool) and tool.name == EXECUTE:
            rewritten.append(tool.model_copy(update={"description": RUDRA_EXECUTE_DESCRIPTION}))
            changed = True
        elif isinstance(tool, dict) and tool.get("name") == EXECUTE:
            copied = tool.copy()
            copied["description"] = RUDRA_EXECUTE_DESCRIPTION
            rewritten.append(copied)
            changed = True
        else:
            rewritten.append(tool)
    return rewritten if changed else None


class ExecuteGuardMiddleware(AgentMiddleware):
    """Re-describe `execute`, and explain a `cd` or a pip refusal that just failed.

    The three handles below only report, and default to None so the guard
    still builds with no run at all -- which every test above does.
    """

    def __init__(self, *, role: str | None = None, usage: Any = None, trace: Any = None) -> None:
        super().__init__()
        self.role = role
        self.usage = usage
        self.trace = trace

    def _request(self, request):
        tools = _rewrite_execute_description(getattr(request, "tools", None))
        return request if tools is None else request.override(tools=tools)

    def _announce_pip_refusal(self, command: str) -> None:
        """Count and name a refused install (CLAUDE.md §8a). Swallows its own failure."""
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_install_refused(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("pip refusal not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"pip refused to install outside a virtualenv: {command}",
                    role=role,
                    name=PIP_REFUSED_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("pip refusal not announced", exc_info=True)

    def _annotated(self, request, result):
        """The result with every note that applies appended, or the result itself."""
        if (request.tool_call or {}).get("name") != EXECUTE:
            return result
        if not _failed(result):
            return result
        command = (request.tool_call.get("args") or {}).get("command")
        if not isinstance(command, str):
            return result
        content = _content(result)
        note = ""
        target = _absolute_cd_target(command)
        if target is not None:
            note += _note(target)
        if PIP_REFUSAL in content:
            note += _PIP_NOTE
            self._announce_pip_refusal(command)
        if not note:
            return result
        if isinstance(result, str):
            return result + note
        return result.model_copy(update={"content": content + note})

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))

    def wrap_tool_call(self, request, handler):
        return self._annotated(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._annotated(request, await handler(request))


__all__ = [
    "PIP_REFUSAL",
    "PIP_REFUSED_NOTICE",
    "RUDRA_EXECUTE_DESCRIPTION",
    "ExecuteGuardMiddleware",
]
