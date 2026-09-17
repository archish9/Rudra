"""ToolRouteMiddleware — answer a call to a tool this agent does not have (OPEN-103).

**The defect.** Run `f845b496a2aa`'s coder called a tool named `bash` fifteen
times and one named `exit` twice, and every call was answered by langgraph's
own tool-node echo:

    Error: bash is not a valid tool, try one of [ls, read_file, write_file,
    edit_file, delete, glob, grep, task, compact_conversation, remember,
    search_memory].

Three of its four invocations then died on exactly three of those in a row --
119.0 s, 145.7 s and 222.7 s, 487.5 s of the coder's 1565.8 s -- and two of
the three had already written their files and wanted only to check them. The
commands were ordinary: `python -m pytest tests/unit/test_models.py -v`.

**The coder having no shell is deliberate and correct** (`registry.py`,
`_WRITER_FS`; OPEN-36 is the record) and nothing here grants one. The ANSWER
was the defect, three ways at once: it carried no correction, which is
OPEN-100's finding one agent down -- *a refusal carrying no correction is one
the model answers by retrying*; it was counted as a tool failure; and it
advertised `task`, which `DelegationGuardMiddleware` withholds from every
shipped spec (OPEN-26, OPEN-101 section 2.4 on a second stack).
`planner_write.py` is the precedent, and was registered on the planner only.

**The route is read off the spec's grants, never restated** (OPEN-15). A spec
holding `execute` -- the tester -- is told to use it; a writer without one is
told what `_CODER_PROMPT` and `machine_paths.py` already say, that the gate
runs the suite when it stops and calls it back with the failure; a read-only
spec, which no gate calls back, is told to read and report. `granted` is the
tuples `_rules_for` and `_interrupt_on_for` read, so `[tools] shell = false`
behaves as it does for `MachinePathMiddleware`: the tool stays registered and
answers that execution is unavailable.

**Closed sets, and everything outside them is left to the echo.** A wrong
route is worse than the echo (`planner_write.py`'s stated limit), so an
invented name that is neither shell- nor completion-shaped reaches langgraph
unchanged. Two structural guards come first and either one declines: the
call must be UNREGISTERED -- `request.tool` is None, pinned in
`tests/test_deepagents_contract.py` -- and the name must not be one the spec
holds. An MCP server that registers a real `run` is never intercepted.

**Still counted as a tool failure, deliberately.** `status="error"` is what
`trace/stream.py::message_is_error` reads first, so `subagents/runner.py`
halts an invocation on three of these in a row exactly as it did on three
echoes. OPEN-103's plan said a `REJECTED:` lead would stop that; measured, it
does not, for this or for any refusal Rudra has (OPEN-118). The owner decided
on 2026-09-14 to keep it counted: the gate still runs after a coder halt
(`loop/engine.py:653-657`), so a model that ignores the route three times
loses little, while an uncounted refusal is bounded only by `MAX_TOTAL_CALLS`.
The route is the fix; the counter is what bounds a model that will not read
it. `tool_routes_answered` climbing past one per invocation is that model.

**Subagent stack only.** The planner has `PlannerWriteMiddleware` for the
calls it was observed making, and no stage of it is called back by a gate, so
the writer's sentence would be false there.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

TOOL_ROUTE_NOTICE = "tool-route"
"""The `name` on the NOTICE, and what a maintainer greps `debug-<id>.jsonl`
for. One spelling, as a module constant (`CLAUDE.md` §8a)."""

SHELL_NAMES = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "shell",
        "terminal",
        "command",
        "run",
        "run_command",
        "run_shell_command",
        "execute",
        "python",
        "python3",
        "pytest",
    }
)
"""Names that can only mean *run this command*. `execute` is here for the
specs that do not hold it; a spec that does is never answered for it."""

FINISH_NAMES = frozenset(
    {
        "exit",
        "quit",
        "done",
        "finish",
        "complete",
        "task_complete",
        "complete_task",
        "attempt_completion",
    }
)
"""Names that can only mean *I am finished*. OPEN-42's `task_complete` and
this run's `exit` among them; a category, never one run's spelling."""

# Where a shell-shaped call carries its command, in the order models use them.
_COMMAND_KEYS = ("command", "cmd", "script", "code")

# The model's own argument is quoted so it can see which call was refused.
# Capped, because a `python -c` payload can be the size of a file.
_QUOTE_LIMIT = 200

_READ_TOOLS = ("read_file", "grep")
_WRITE_TOOLS = ("edit_file", "write_file")

_USE_EXECUTE = (
    "REJECTED: there is no `{name}` tool. {command} was not run.\n\n"
    "`execute` is this agent's shell -- call it with the same command.{suite}"
)

GATE_RUNS_ON_STOP = (
    "When you stop, a verification gate runs the project's linter, type checker "
    "and full test suite, and calls you again with the exact failure text if "
    "anything fails -- stopping IS how you find out whether your work is correct."
)
"""What a writer with no shell is told about the tests. Shared with the repeat
guard's write refusal (OPEN-126), so the two answers cannot drift apart."""

_NO_SHELL_WRITER = (
    "REJECTED: there is no `{name}` tool, and no shell of any kind in this "
    "agent. {command} was not run, and nothing here can run it.\n\n"
    "You do not need to. " + GATE_RUNS_ON_STOP + "\n\n"
    "{hands}Do not call `{name}` again, or any other name for a shell."
)

_NO_SHELL_READER = (
    "REJECTED: there is no `{name}` tool, and no shell of any kind in this "
    "agent. {command} was not run, and nothing here can run it.\n\n"
    "{hands}Do not call `{name}` again, or any other name for a shell."
)

_FINISH = (
    "REJECTED: there is no `{name}` tool, and no tool of that kind -- "
    "finishing is not a tool call.\n\n"
    "Reply with text and call no tool. That is what ends your turn, it always "
    "works, and the reply is the only thing the caller sees: make it one or "
    "two lines saying what you did.{no_file}"
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values)


def settled_write_route(granted: frozenset[str]) -> str:
    """The route for an agent whose write was already on disk (OPEN-126).

    `repeat_guard.py` refuses a byte-identical re-send and used to say only
    "move on". Across the archive the model sent the same bytes again after
    reading that in 8 of 13 cases, for two reasons this answers: it had written
    a script to run the tests (`run_tests.sh`, `run_pytest.py` -- "Now let me run
    the tests to verify they pass:"), or it was re-sending its deliverable to
    say it had finished. That is OPEN-103's reflex reaching for a REGISTERED
    tool, so it gets OPEN-103's route.

    Names only what `granted` holds (OPEN-15). Empty for an empty set, so a
    guard built without grants -- the planner's, and every bare one in the
    tests -- keeps its words.
    """
    if not granted:
        return ""
    if "run_tests" in granted:
        run = "To run the test suite, call `run_tests`."
    elif "execute" in granted:
        run = "To run a command, call `execute`."
    else:
        run = (
            "Writing a file does not run it, and nothing in this agent can run one "
            "-- nor does anything need to. " + GATE_RUNS_ON_STOP
        )
    return f"{run} If your work is finished, reply with one or two lines and call no tool."


class ToolRouteMiddleware(AgentMiddleware):
    """Answer an unregistered shell- or completion-shaped call with the route.

    Answers rather than annotating, for `planner_write.py`'s reason: there is
    no tool to run, so letting the call through only reaches the echo this
    replaces.
    """

    def __init__(
        self,
        role: str | None = None,
        *,
        granted: frozenset[str] | tuple[str, ...] = (),
        usage: Any = None,
        trace: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        # NOT `self.tools` -- `AgentMiddleware.tools` is the list a middleware
        # CONTRIBUTES, and langchain registers every entry of it
        # (`planner_write.py`'s trap, and why that one takes `stage_tools`).
        self.granted = frozenset(granted)
        self.usage = usage
        self.trace = trace

    # --- what to say ---------------------------------------------------------

    def _held(self, names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(name for name in names if name in self.granted)

    def _shell_route(self, name: str, command: str) -> tuple[str, str]:
        """The answer and a one-line summary of it for the notice."""
        if "execute" in self.granted:
            suite = (
                " To run the whole test suite, `run_tests` is the better call."
                if "run_tests" in self.granted
                else ""
            )
            return (
                _USE_EXECUTE.format(name=name, command=command, suite=suite),
                "use execute",
            )

        reads = self._held(_READ_TOOLS)
        writes = self._held(_WRITE_TOOLS)
        if writes:
            hands = "Fix what is wrong with `{}`, then reply with one or two lines and call no tool. ".format(
                writes[0]
            )
            if reads:
                hands = f"Check the code by reading it with `{reads[0]}`. " + hands
            return (
                _NO_SHELL_WRITER.format(name=name, command=command, hands=hands),
                "no shell here; the gate runs the tests when it stops",
            )

        hands = "Report what you found in your reply and call no tool. "
        if reads:
            hands = f"Check what you need by reading it -- {_quoted(reads)}. " + hands
        return (
            _NO_SHELL_READER.format(name=name, command=command, hands=hands),
            "no shell here; read and report",
        )

    def _finish_route(self, name: str) -> tuple[str, str]:
        no_file = (
            " Do not write a file to announce that you have finished -- a file ends nothing."
            if self._held(_WRITE_TOOLS)
            else ""
        )
        return _FINISH.format(name=name, no_file=no_file), "finishing is a reply with no tool call"

    # --- the diagnostic ------------------------------------------------------

    def _announce(self, name: str, command: str, summary: str) -> None:
        """Count and say that a call was routed (`CLAUDE.md` §8a).

        Both halves swallow their own failure: a run that did its work must
        not be reported failed because a log line could not be written.
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_tool_route_answered(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("tool route not counted", exc_info=True)
        try:
            if self.trace is not None:
                subject = f" ({command})" if command else ""
                self.trace.notice(
                    f"answered a call to `{name}`{subject}, which the {role} does not "
                    f"have, with the route: {summary}",
                    role=role,
                    name=TOOL_ROUTE_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("tool route not announced", exc_info=True)

    # --- the wrapper ---------------------------------------------------------

    def _refusal(self, request):
        """A ToolMessage carrying the route, or None to let the call through."""
        if getattr(request, "tool", None) is not None:
            return None
        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        if not name or name in self.granted:
            return None

        if name in SHELL_NAMES:
            command = _command_of(call.get("args"))
            content, summary = self._shell_route(
                name, f"`{command}`" if command else "That command"
            )
        elif name in FINISH_NAMES:
            command = ""
            content, summary = self._finish_route(name)
        else:
            return None

        self._announce(name, command, summary)
        return ToolMessage(
            content=content,
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


def _command_of(args: object) -> str:
    """The command a shell-shaped call carried, capped, or "" when it had none."""
    if not isinstance(args, dict):
        return ""
    for key in _COMMAND_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text if len(text) <= _QUOTE_LIMIT else text[:_QUOTE_LIMIT] + " ..."
    return ""


__all__ = [
    "FINISH_NAMES",
    "GATE_RUNS_ON_STOP",
    "SHELL_NAMES",
    "TOOL_ROUTE_NOTICE",
    "ToolRouteMiddleware",
    "settled_write_route",
]
