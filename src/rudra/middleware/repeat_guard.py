"""RepeatGuardMiddleware — stop an agent re-running a call that already failed.

`loop/bounds.py` already does this one level up: two identical failure
signatures stop a task (C6.5a). Nothing did it *inside* an agent turn, so a
model could call one tool with byte-identical arguments until the recursion
limit caught it. Measured 2026-08-24: `read_file` with
`{'file_path': '/. rudra/AGENTS.md'}` four times in a row, failing
identically, before the model tried anything else (TODO.md OPEN-10).

The principle is S9c.1's -- the model decides what work exists, Python
decides when to stop.

**Read tools only, and that is the whole safety argument.** A missing file
does not appear because you asked a third time, so short-circuiting a
repeated `read_file` can only save a round trip. A *command* is different:
`execute` can legitimately succeed on retry after a flaky test, a network
blip or a file another step has since written. Guarding that would turn a
token cost into a correctness bug, so `execute` -- and every writing tool --
is deliberately outside `_GUARDED_TOOLS`.

The count is per (tool, arguments) and resets the moment that exact call
succeeds, so a read that fails while a file is being written and then works
is never held against the model.

**Two rules, one mechanism (OPEN-39 Phase 2).** Since 2026-08-30 the same
no-progress test also covers calls that SUCCEEDED: a guarded read repeated
with identical arguments, and nothing in between that could have changed the
answer, is refused rather than re-run. Measured on run 83f34f50210c: 47
`read_file` calls over 6 distinct files -- 41 re-reads, 16 of them inside a
single invocation, where the bytes were already in the model's own
transcript. The dominant shape is read-after-own-write, the coder confirming
a write it had just made.

Nothing is cached and nothing is served from a copy. The refusal is safe for
exactly the reason the failure rule is: `_record` drops all state on any
non-guarded call, so a short-circuit can only happen when no write, edit,
delete or command has intervened. The one case it gets wrong is a process
OUTSIDE Rudra editing a project file mid-turn, which the loop already assumes
away -- `loop/engine.py::attempt_snapshot` compares a before and an after on
the same assumption.

**The refusal must never read as a tool failure.** `trace/stream.py` counts a
result whose first line begins "Error"/"Traceback"/"Errno"/"[Errno"/"BLOCKED:"
as one, and `subagents/runner.py` halts a subagent after three consecutive
failures -- so an "Error:"-prefixed dedupe message would convert this saving
into three dead invocations, which is OPEN-16's shape. The failure refusal
leads with "Error:" because it IS one; this one leads with "Already read:".

**A refusal answers the call it refused, and says so as Rudra (OPEN-57).**
Both refusals used to be returned as a bare `str`. langgraph puts a
wrapper's return value straight into `{messages: [...]}`
(prebuilt/tool_node.py:881-886), where `add_messages` coerces a bare string
to a HumanMessage -- so run11's debug log holds Rudra's own dedupe text as
`"kind": "user"`, the model's `tool_call` was left with nothing answering
it, and every consumer that pairs a call with its result lost the pair. Two
halves fix it and both are needed: the returned `ToolMessage` is what the
MODEL reads, and the `TraceKind.NOTICE` is what says RUDRA did this rather
than a tool. The text is unchanged -- prefixing it to make it classifiable
is what the paragraph above forbids.

One consequence is deliberate and was decided rather than inherited: the
failure refusal is a ToolMessage whose content leads with "Error:", so
`subagents/runner.py:302` now counts it, and a third identical failing read
is the third consecutive failure that halts the invocation. That is what
would have happened before this guard existed -- returning something the
counter could not see was masking those halts. The dedupe refusal is not an
error and RESETS that counter, which is the same rule read the other way.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

# Deterministic reads. A repeat of one of these after a failure cannot
# succeed, which is what makes short-circuiting safe. `execute` is
# excluded on purpose -- see the module docstring.
_GUARDED_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})

MAX_IDENTICAL_FAILURES = 2
"""Failures of one exact call before the next is refused rather than run.

Two, not one: the first retry is worth allowing -- a model correcting
itself on the second attempt is normal, and a guard that fires on the
first failure would be indistinguishable from the tool simply being
broken. The third identical call is the one that has stopped being a
retry and started being a loop.
"""


MAX_IDENTICAL_READS = 1
"""Successful answers to one exact call before the next is refused.

One, not two, and the asymmetry with MAX_IDENTICAL_FAILURES is the point.
A failure earns its retry because a model correcting itself on the second
attempt is normal. A success has nothing to correct -- the answer is in the
transcript verbatim -- so the second identical call is already the waste.

Refusing only the third would also be too late to help: `runner.py`'s
MAX_REPEATED_CALLS halts the whole invocation on the third, and two of run
83f34f50210c's five halts were exactly that (`read_file` on `models.py`,
then on `app.py`), each costing an attempt.
"""


def _signature(name: str, args: dict[str, Any]) -> str:
    """A stable key for one exact call.

    `sort_keys` because dict ordering follows whatever the model emitted,
    and `default=str` because an argument that is not JSON-serialisable
    must degrade to a usable key rather than raise inside a tool call.
    """
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def _is_error(result: Any) -> bool:
    """Did this tool call fail?

    deepagents reports filesystem failures as ordinary ToolMessage content
    beginning with "Error:" rather than by raising
    (backends/filesystem.py:447), so the string is the signal available
    here. Checked on `.content` when present so a ToolMessage and a bare
    string are treated alike.
    """
    content = getattr(result, "content", result)
    return isinstance(content, str) and content.lstrip().startswith("Error")


class RepeatGuardMiddleware(AgentMiddleware):
    """Refuse a read that has already failed twice with identical arguments.

    State lives on the instance, and one instance is built per agent, so
    the count is per agent run -- the same lifetime `build.py` gives every
    other middleware it constructs.
    """

    def __init__(
        self,
        max_identical_failures: int = MAX_IDENTICAL_FAILURES,
        max_identical_reads: int = MAX_IDENTICAL_READS,
        *,
        role: str | None = None,
        usage: Any = None,
        trace: Any = None,
    ) -> None:
        """`role`, `usage` and `trace` are optional and duck-typed, on
        ModelRetryMiddleware's precedent (`subagents/build.py:239-243`):
        callers outside a full run build stand-in contexts, and half of them
        have no accounting to hand. `role` and `usage` are both needed
        before anything is counted -- `RunUsage._slot` would otherwise open
        a row named None -- and `trace` is what makes a refusal audible
        (OPEN-57).
        """
        super().__init__()
        self.max_identical_failures = max_identical_failures
        self.max_identical_reads = max_identical_reads
        self.role = role
        self.usage = usage
        self.trace = trace
        self._failures: dict[str, int] = {}
        self._last_error: dict[str, str] = {}
        # How many times each signature has already been answered
        # successfully with nothing since that could have changed the
        # answer. A count rather than a set, so `max_identical_reads` is a
        # real dial and the two rules read the same way.
        self._answered: dict[str, int] = {}

    def _target(self, args: dict[str, Any]) -> str:
        """What the call was aimed at, for the refusal and for the notice.

        One function, because a notice naming a different path from the
        refusal beside it is worse than a notice with no path at all.
        """
        return str(args.get("file_path") or args.get("path") or args.get("pattern") or "")

    def _refusal(self, signature: str, name: str) -> str:
        seen = self._failures[signature]
        return (
            f"Error: {name} has already failed {seen} times with these exact "
            f"arguments, and was not run again. The error each time was: "
            f"{self._last_error.get(signature, 'unknown')}\n"
            f"Calling it again with the same arguments will not work. Change "
            f"the arguments, use ls to find the correct path, or continue "
            f"without this file."
        )

    def _repeat_refusal(self, name: str, args: dict[str, Any]) -> str:
        """The answer to a read that has already been answered.

        Leads with "Already read", never "Error" -- see the module
        docstring. It carries the target because a bare "you did that
        already" leaves the model to work out WHICH of its calls was
        refused, and the whole saving is one round trip.
        """
        target = self._target(args)
        return (
            f"Already read: `{name}` on '{target}' was answered earlier in this "
            f"turn and nothing has changed it since, so it was not run again. "
            f"That earlier result is still current -- use it. To see something "
            f"else, call a different path or pattern; to change the file, write "
            f"or edit it."
        )

    def _blocked(self, request) -> str | None:
        """The refusal to return instead of running this call, or None.

        Failures are tested first. The two rules cannot both apply to one
        signature -- a success clears the failure count and a failure is
        never in `_answered` -- but the order is fixed anyway, because a
        reader should not have to prove that to know which message wins.
        """
        name = request.tool_call.get("name")
        if name not in _GUARDED_TOOLS:
            return None
        args = request.tool_call.get("args", {})
        signature = _signature(name, args)
        if self._failures.get(signature, 0) >= self.max_identical_failures:
            self._announce(
                f"refused: `{name}` on '{self._target(args)}' failed "
                f"{self._failures[signature]} times identically -- not run again"
            )
            return self._refusal(signature, name)
        if self._answered.get(signature, 0) >= self.max_identical_reads:
            self._count_dedupe()
            self._announce(
                f"dedupe: `{name}` on '{self._target(args)}' was already answered "
                f"this turn -- not run again"
            )
            return self._repeat_refusal(name, args)
        return None

    def _announce(self, payload: str) -> None:
        """Say that a refusal happened, to whoever is listening (OPEN-57).

        One `name` for both rules, because a reader looking up
        `repeat-guard` must find the whole middleware there; the payload is
        what says which rule fired. VERBOSE on screen and in the debug log
        at every level, which is OPEN-44's choice inherited through
        OPEN-45: a refusal annotates a run rather than reporting on it.

        `trace` is the second half of the fix and the returned message is
        the first. A NOTICE alone would leave the model's tool_call
        unanswered; a ToolMessage alone would say the tool answered, when
        what happened is that RUDRA did.

        Swallowing follows ModelRetryMiddleware._report and TraceSink.emit:
        a guard that exists to save a round trip must not end a run over
        its own bookkeeping.
        """
        if self.trace is None:
            return
        try:
            self.trace.notice(payload, role=self.role or "agent", name="repeat-guard")
        except Exception:  # noqa: BLE001 -- observability never ends a run
            pass

    def _as_message(self, request, refusal: str) -> ToolMessage:
        """The refusal, addressed to the call it refused (OPEN-57).

        A bare `str` return goes straight into `{messages: [...]}`
        (langgraph prebuilt/tool_node.py:881-886), where `add_messages`
        coerces it to a HumanMessage -- so Rudra's own words were recorded,
        rendered and replayed as a line the human typed, the AIMessage's
        tool_call was left with nothing answering it, and every consumer
        that pairs a call with its result lost the pair.

        Only what this middleware invents is wrapped. A result that came
        back from the handler is already a message and is returned
        untouched: re-wrapping one would drop its status and its id.
        """
        return ToolMessage(
            content=refusal,
            name=str(request.tool_call.get("name") or ""),
            tool_call_id=str(request.tool_call.get("id") or ""),
        )

    def _count_dedupe(self) -> None:
        """One re-read this guard answered instead of running (OPEN-39).

        Counted only for the repeat rule, never for the failure one: the
        saving OPEN-39 Phase 2 claims is re-reads avoided, and OPEN-10's
        saving was banked in 2026-08-24. Two things in one number would be
        neither.
        """
        if self.usage is None or self.role is None:
            return
        self.usage.record_dedupe(self.role)

    def _record(self, request, result: Any) -> None:
        name = request.tool_call.get("name")
        if name not in _GUARDED_TOOLS:
            # Anything else -- a write, an edit, a command -- may have
            # changed what the guarded reads would see, so every count is
            # dropped. That is what makes this a NO-PROGRESS rule rather
            # than a quota: "you tried this N times and nothing happened in
            # between", which is exactly loop/bounds.py's semantics one
            # level up. Cleared even when the call failed, because a failed
            # command can still have written something.
            #
            # Without this the guard is a correctness bug of its own: a read
            # that fails twice, is fixed by a write, and is retried would
            # stay blocked for the rest of the turn, and the block can never
            # lift because the read that would clear it never runs.
            self._failures.clear()
            self._last_error.clear()
            # And the successes, for the stronger version of the same
            # reason: a write is exactly how the answer to a read changes,
            # so every previous answer stops being current here. This one
            # line is the whole correctness argument for the repeat rule.
            self._answered.clear()
            return
        signature = _signature(name, request.tool_call.get("args", {}))
        if _is_error(result):
            self._failures[signature] = self._failures.get(signature, 0) + 1
            self._last_error[signature] = str(getattr(result, "content", result))[:200]
            self._answered.pop(signature, None)
        else:
            self._failures.pop(signature, None)
            self._last_error.pop(signature, None)
            self._answered[signature] = self._answered.get(signature, 0) + 1

    def wrap_tool_call(self, request, handler):
        refusal = self._blocked(request)
        if refusal is not None:
            return self._as_message(request, refusal)
        result = handler(request)
        self._record(request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        refusal = self._blocked(request)
        if refusal is not None:
            return self._as_message(request, refusal)
        result = await handler(request)
        self._record(request, result)
        return result


__all__ = ["MAX_IDENTICAL_FAILURES", "MAX_IDENTICAL_READS", "RepeatGuardMiddleware"]
