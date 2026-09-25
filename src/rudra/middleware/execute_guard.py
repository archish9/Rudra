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
import shlex
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import BaseTool

from rudra.compat.path_constants import SANDBOX_PREFIXES
from rudra.compat.virtual_paths import looks_windows_absolute, virtual_to_host
from rudra.middleware.machine_paths import MACHINE_DIRS

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
    "If this is a Python project, declare the dependency in requirements.txt "
    "or pyproject.toml instead -- Rudra installs declared dependencies into "
    ".venv before every gate run. If it is not, use this project's own "
    "package manager instead."
)


# OPEN-151. The virtual spelling typed into a shell command.
COMMAND_PATH_NOTICE = "command-path"

_COMMAND_PATH_NOTE = (
    "\n\n[Rudra] `{token}` is this project's `{relative}` written the way the "
    'FILE TOOLS spell it -- to them a leading "/" means this project\'s root. '
    "`execute` is a real shell, so it looked for `{token}` at the root of the "
    "MACHINE, where nothing of this project exists. `execute` already runs in "
    "this project's root directory: run it again naming `{relative}` relative "
    'to that root, with no leading "/".'
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


def _command_tokens(command: str) -> list[str]:
    """The command's words, quotes resolved the way a shell resolves them.

    `shlex.split` because the token is rarely the first word -- run G2's
    second call is `ls -la /.venv/bin/ | grep python` -- and because a quoted
    path with a space in it is one token, not two. It raises on an
    unterminated quote, which a model emits; a whitespace split answers that
    case well enough to find a bare path and never raises.
    """
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _virtual_path_token(
    command: str, project_root: Path, skip: str | None
) -> tuple[str, str] | None:
    """The first token that is this project's path written with a leading "/".

    Six conditions, every one of them declining rather than guessing. The
    SEVENTH is at the call site and not here, because it is about the shell's
    answer rather than about the command: the token must be one the shell
    itself named. Two of the six were measured rather than reasoned:

    * `virtual_to_host` resolves both a bare "/" and a UNC "//server/share"
      to the project ROOT, which always exists -- so a resolution equal to the
      root is not evidence of anything.
    * a first segment in `MACHINE_DIRS` declines even when the project happens
      to hold that name, because `/etc/hosts` in a shell command is ordinary
      and correct. That frozenset is `machine_paths.py`'s, imported rather
      than respelled (CLAUDE.md 3: do not add a fourth copy).
    """
    root = Path(project_root)
    for token in _command_tokens(command):
        if token == skip:
            continue
        if not token.startswith("/") or token.startswith("//") or token.strip("/") == "":
            continue
        candidate = token.rstrip(";,")
        first = candidate.lstrip("/").split("/", 1)[0]
        if first in MACHINE_DIRS:
            continue
        try:
            if Path(candidate).exists():
                continue
            host = virtual_to_host(candidate, root)
            if host is None or host == root or not host.exists():
                continue
            relative = host.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        return candidate, relative
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

    def __init__(
        self,
        *,
        role: str | None = None,
        usage: Any = None,
        trace: Any = None,
        project_path: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.usage = usage
        self.trace = trace
        # OPEN-151. None is the honest answer with no run: there is nowhere to
        # resolve a virtual spelling against, so nothing about one can be
        # claimed -- `machine_paths.py`'s own rule at the same boundary.
        self.project_path = project_path

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

    def _announce_command_path(self, token: str, relative: str) -> None:
        """Count and name a command that used the virtual spelling (OPEN-151).

        What this guard prevents is a tool failure that never happens, and a
        failure that never happens leaves no mark on `calls`, `seconds` or
        tokens -- so it has to be counted here or nowhere (CLAUDE.md 8a).
        Swallows its own failure, like every writer on this path.
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_command_path_explained(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("command path not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"a command named {token}, which is this project's {relative}",
                    role=role,
                    name=COMMAND_PATH_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("command path not announced", exc_info=True)

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
        # OPEN-151, and AFTER the `cd` note so that `target` can stand it down:
        # `cd /app` with an `app/` in the project satisfies both rules and both
        # would say the same sentence, so the more specific one -- which names
        # the `cd` itself -- wins.
        #
        # Keyed on a path the SHELL ITSELF NAMED in its answer, which every one
        # of the three archived hits is (`/bin/sh: /.venv/bin/python: No such
        # file or directory`). A command that merely CONTAINS such a token and
        # failed for its own reason -- `pytest --cov=/src` on an assertion --
        # is not this defect, and a note there would be a wrong answer to a
        # real failure.
        if self.project_path is not None:
            named = _virtual_path_token(command, self.project_path, target)
            if named is not None and named[0] in content:
                token, relative = named
                note += _COMMAND_PATH_NOTE.format(token=token, relative=relative)
                self._announce_command_path(token, relative)
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
    "COMMAND_PATH_NOTICE",
    "PIP_REFUSAL",
    "PIP_REFUSED_NOTICE",
    "RUDRA_EXECUTE_DESCRIPTION",
    "ExecuteGuardMiddleware",
]
