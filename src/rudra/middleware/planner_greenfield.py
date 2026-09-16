"""GreenfieldReadMiddleware — answer a file read on an empty project (OPEN-116).

**The defect.** Run `8f160d92c6da` spent 2191.3 of 4088.0 model seconds --
53.6% -- listing and reading a project that held `.DS_Store` and `.mcp.json`:
`ls /` in all three planner stages, then `ls /.rudra`, `read_file
/.rudra/AGENTS.md` and `read_file /.mcp.json`. Across the archive 20 of 25
planner stages opened with `ls /`. Run `a04f89bd2ed6`, after OPEN-117 hid
`.rudra/`, still opened all three with it, on a project whose whole answer
was `['/.mcp.json']`.

**The fix is absence** (`agent/planner_agent.py::create_planner_agent`): a
stage whose project holds nothing but `filesystem/tree.py::NON_CONTENT_NAMES`
is built without `ls`, `read_file`, `glob` and `grep`, and its prompt says the
project is empty. OPEN-17's rule: a prompt asking a model not to reach for a
tool loses, and a tool that is not there cannot be reached.

**This is the answer to reaching anyway.** Absence alone hands a habitual
`ls` to langgraph's tool-list echo, which says what the model cannot do and
nothing about what to do instead -- OPEN-103, where three echoes in a row
killed 3 of 4 coder invocations, and three in a row halt a planner stage just
the same. The habit is measured, not assumed: the coder, shown a listing and
never told to `ls`, still opened with one in 18 of 46 invocations.

**Registered only on a greenfield stage**, so a reading stage never meets it;
and it answers only an UNREGISTERED call (`request.tool` None), so it can
never hide a file from a stage that holds the tool. It names only tools the
stage holds, for PlannerWriteMiddleware's reason (OPEN-15).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

GREENFIELD_READ_NOTICE = "greenfield-read"
"""The `name` on the NOTICE, and what a maintainer greps `debug-<id>.jsonl`
for. One spelling, as a module constant (`CLAUDE.md` §8a)."""

# The file tools a greenfield stage is built without. Writes are not here:
# PlannerWriteMiddleware answers those on every stage, and two answers for one
# call would be counted twice.
_READ_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})

# What the stage can do instead, most specific first, naming only a tool it
# holds.
_ROUTES = (
    ("add_tasks", "Declare the work with `add_tasks`; the coder creates every file it needs."),
    ("record_fact", "Record what the request settles with `record_fact`, and why."),
)

_ROUTELESS = "Finish what this stage is for, and stop."

_REJECTED = (
    "REJECTED: there is no `{name}` tool at this stage. This project holds no "
    "files yet -- nothing to list, search or read -- so the stage was built "
    "without file tools, and its complete listing is already under PROJECT "
    "STRUCTURE in your instructions.\n\n"
    "Work from the request. {route}"
)


def _subject_of(args: object) -> str:
    """The path or pattern the call named, or "" when it named neither."""
    if not isinstance(args, dict):
        return ""
    for key in ("file_path", "path", "pattern"):
        value = args.get(key)
        if value:
            return str(value)
    return ""


class GreenfieldReadMiddleware(AgentMiddleware):
    """Answer `ls`/`read_file`/`glob`/`grep` on a stage built without them."""

    def __init__(
        self,
        stage_tools: tuple[str, ...] | frozenset[str] = (),
        *,
        role: str = "planner",
        usage: Any = None,
        trace: Any = None,
    ) -> None:
        super().__init__()
        # NOT `self.tools`, for the trap PlannerWriteMiddleware records:
        # `AgentMiddleware.tools` is what a middleware CONTRIBUTES.
        self.stage_tools = frozenset(stage_tools)
        self.role = role
        self.usage = usage
        self.trace = trace

    def _route(self) -> str:
        for name, sentence in _ROUTES:
            if name in self.stage_tools:
                return sentence
        return _ROUTELESS

    def _announce(self, name: str, subject: str) -> None:
        """Count and say that a read was answered (`CLAUDE.md` §8a).

        Both halves swallow their own failure: a run that did its work must
        not be reported failed because a log line could not be written.
        """
        try:
            if self.usage is not None:
                self.usage.record_greenfield_read_answered(self.role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("greenfield read not counted", exc_info=True)
        try:
            if self.trace is not None:
                target = f" {subject}" if subject else ""
                self.trace.notice(
                    f"answered `{name}`{target} on a {self.role} stage built without "
                    "file tools: the project holds no files yet",
                    role=self.role,
                    name=GREENFIELD_READ_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("greenfield read not announced", exc_info=True)

    def _refusal(self, request):
        """A ToolMessage carrying the route, or None to let the call through."""
        if getattr(request, "tool", None) is not None:
            return None
        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        if name not in _READ_TOOLS:
            return None

        self._announce(name, _subject_of(call.get("args")))
        return ToolMessage(
            content=_REJECTED.format(name=name, route=self._route()),
            tool_call_id=call.get("id", ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)


__all__ = ["GREENFIELD_READ_NOTICE", "GreenfieldReadMiddleware"]
