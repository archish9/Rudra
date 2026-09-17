"""Invoking one subagent and reporting what it did.

Deterministic on purpose: the caller names the subagent, so no model
decides who runs. D9 requires the loop to be deterministic, and a planner
choosing when to review would put an LLM back in control of it.

Nothing here raises for a runtime failure. Step 9c's loop must act on a
bad subagent run the way it acts on a failed gate, and an exception
escaping into that loop is A1.39's shape -- one transient provider error
killing a run and discarding completed work.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.compat.virtual_paths import virtual_to_relative
from rudra.context.deadline import span_deadline
from rudra.context.usage import model_time_of, model_wait_note
from rudra.permissions.approval import run_with_approvals
from rudra.subagents.build import build_agent
from rudra.subagents.registry import REGISTRY
from rudra.trace.stream import StreamState
from rudra.trace.stream import is_rudra_refusal as _is_rudra_refusal
from rudra.trace.stream import message_is_error as _message_is_error

# Carried from _stream_coder (main_agent.py:241-243), which Step 9c
# deletes. These are per-invocation guards: one subagent looping on itself.
# The across-invocation bounds -- max fix attempts, no-progress detection --
# are C6.5a and belong to the loop, not here.
MAX_CONSECUTIVE_FAILURES = 3
MAX_REPEATED_CALLS = 3

MAX_TOTAL_CALLS = 80
"""Tool calls one subagent invocation may make, of any kind (A1.92).

The other two guards catch *failing* and catch *repeating*. Neither
catches spending. Measured on Step 15c's acceptance: denied a `bash` tool
it never had, a delegated subagent issued **204 successful `glob` calls
over 12 minutes** looking for a Python interpreter -- ending on
`**/python` against `/` -- and nothing stopped it. `MAX_REPEATED_CALLS`
keys on `(tool, target)`, so 204 different patterns are 204 firsts;
`MAX_CONSECUTIVE_FAILURES` never fired because every glob *succeeded*
(`No files found` is a successful result).

80 is chosen against observed work, not guessed: a healthy coder
invocation in that same acceptance ran to well under 20 calls, and the
planner's busiest stage to about 30. It is a runaway bound, not a budget
-- if real work ever approaches it, raise it rather than teaching people
to expect halts.

It is HALF the runaway bound since OPEN-91; see MAX_INVOCATION_SECONDS,
which covers the regime where this one is loose."""

MAX_INVOCATION_SECONDS = 1200.0
"""Wall-clock seconds one subagent invocation may spend (OPEN-91).

**The unit was the defect, not the number.** MAX_TOTAL_CALLS above was
calibrated against an observation -- 204 globs over 12 minutes, i.e. 3.5 s
per call -- at which rate 80 calls is 4.7 minutes and a sensible bound.
Run `fc543fb2b82f` measured 46.0 s per coder call, at which rate the SAME
80 calls is 61 minutes. Its task t7 spent 2,704 s on 41 globs hunting an
interpreter the file tools cannot reach, stopped at 55 calls on the
model's own initiative, and this project's only guard against exactly that
never fired.

    A guard denominated in calls has a wall-clock cost that varies by more
    than an order of magnitude with the provider. The thing the user
    complains about is seconds.

1200 is chosen against the same run: its healthy t1 coder was ONE call and
201 s, and a hard task at 46 s/call and ~20 calls is ~15 minutes. So 20
minutes clears observed real work and halves the observed runaways.

**Both bounds are kept, and each covers the regime where the other is
loose.** On a fast local model 20 minutes is 300+ calls, which is
MAX_TOTAL_CALLS' case; on a 46 s/call provider 80 calls is an hour, which
is this one's. `[agent] max_invocation_seconds` overrides it -- a user on
a slow provider is the person best placed to raise it -- and 0 turns it
off, the way `max_questions = 0` does.

Checked as each chunk arrives, so a model call already in flight is not
interrupted mid-request: the halt lands at the first chunk after the
deadline, which is one model round trip of slack."""

# Tool calls worth counting repeats for. `task` is included and now
# genuinely reachable, which is what closes A1.20.
_WATCHED_TOOLS = frozenset({"write_file", "edit_file", "read_file", "task"})

_LOG = logging.getLogger("rudra.subagents.runner")
"""Where one line per subagent invocation goes (OPEN-89).

Under the `rudra` tree, so `trace/debug.py` writes it into
`debug-<id>.jsonl` with no new plumbing -- the same route
`context/middleware.py` takes for a model call, and for the same reason:
the file a user attaches to a bug report should answer "where did the
time go" without a parser.
"""

SUBAGENT_KIND = "subagent_done"
"""The `kind` an invocation summary carries. One string, named here,
because it is what a maintainer greps for."""

TOP_TOOLS = 6
"""How many tool names the summary lists, busiest first.

A cap and not the whole tally: the point is the SHAPE of an invocation --
run `fc543fb2b82f`'s runaway coder reads `glob: 41, read_file: 11` at a
glance -- and a long tail of ones adds bytes without adding that.
"""


def log_invocation(
    name: str,
    *,
    seconds: float,
    total_calls: int,
    tools: dict[str, int],
    ok: bool,
    halted: str | None = None,
    error: str | None = None,
    nudged: bool = False,
    nudge_outcome: str | None = None,
) -> None:
    """Write what one subagent invocation cost and what it spent it on.

    OPEN-89: `total_calls` was maintained here for the A1.92 runaway bound
    and discarded at every `return`, and `SubagentResult` carries no
    measurement at all -- so the per-invocation table that makes a slow run
    legible existed in no file. Reconstructing it meant scanning the debug
    log for `kind: "user"` lines addressed to a subagent and counting
    `tool_call` lines up to the next one.

    The tool histogram is the half that carries the diagnosis rather than
    the symptom. Run `fc543fb2b82f`'s task t7 spent 2,704 s on 55 calls;
    "55 calls" says it was busy, and `glob: 41` says it was hunting the
    filesystem for an interpreter it cannot use (OPEN-91).

    `nudged` and `nudge_outcome` are OPEN-98's number, and `nudge_outcome`
    is the half that says whether the heuristic is any good: mostly
    `continued` means it is earning its keep, mostly `confirmed_done` means
    it is firing on agents that really had finished and the opener list
    wants narrowing. `announced_again` is a true positive the agent did not
    act on, and `unanswered` a nudge no model saw -- `nudge_outcome_of` holds
    the four, and neither of the last two argues for narrowing anything
    (OPEN-122). Both are always written, `""` when there was no nudge,
    for `halted`'s reason -- a key that appears only on the runs where
    something happened is a key every reader has to guard for.

    Never raises -- write_usage_log's rule (loop/engine.py).
    """
    try:
        ranked = sorted(tools.items(), key=lambda item: (-item[1], item[0]))
        _LOG.debug(
            "subagent finished",
            extra={
                "event": {
                    "kind": SUBAGENT_KIND,
                    "role": name,
                    "seconds": round(float(seconds), 2),
                    "tool_calls": total_calls,
                    "tools": dict(ranked[:TOP_TOOLS]),
                    "ok": ok,
                    "halted": halted or "",
                    "error": error or "",
                    "nudged": nudged,
                    "nudge_outcome": nudge_outcome or "",
                }
            },
        )
    except Exception:  # noqa: BLE001 - accounting must not end a run
        return


# --- the continuation heuristic (OPEN-98) ---------------------------------
#
# langgraph's ReAct loop ends on an AIMessage with no tool calls, and
# `_FINISH_RULES` teaches that deliberately (OPEN-42: run7's coder, given no
# stop verb, invented `task_complete` and wrote `/DONE` eleven times). But
# the signal carries no intent. A model emitting text-only means one of two
# things, and the second is the more ordinary:
#
#   "Created the file with tabs and a form."        -> finished.  Correct.
#   "Wait, I made an error. ... Let me check the    -> thinking.  Wrong.
#    project structure."
#
# The second is verbatim from run 2cde3406f7d6 at=180.333, and it states the
# defect that went on to fail that run, 1.3 s after committing it. Its turn
# ended, `subagent_done` said `ok: true`, and the tester's own steps 3 and 4
# -- run the suite, report what failed -- never happened.
#
# A prompt cannot fix this. The model was not disobeying; it followed
# `_FINISH_RULES` mechanically (text, no tool) while intending the opposite.
# TODO.md's first lesson, and here it is not even prompt losing to prompt.

# Discourse markers that open a sentence without changing what it is. A
# hand-off keeps being a hand-off after "Now"; a report keeps being a report.
# Stripped before the openers below are tried, so the list stays short.
_LEAD_INS = ("now ", "then ", "so ", "ok, ", "okay, ", "alright, ", "first, ", "next, ")

# First person, future tense, no report. Every one of these announces an
# action that has NOT happened yet.
_CONTINUATION_OPENERS = (
    "let me ",
    "let's ",
    "let us ",
    "i'll ",
    "i will ",
    "i need to ",
    "i should ",
    "i'm going to ",
    "i am going to ",
)

# "Let me know if you want anything changed" opens exactly like "Let me check
# the project structure" and means the opposite -- it is a sign-off. The only
# carve-out, and it is a verb list rather than a phrase list because the tail
# varies ("let me know if", "let me know when", "let me know what").
_SIGN_OFF_VERBS = ("know",)

# Words that are a hand-off on their own, matched as the whole first word so
# "waiting for the build" and "holding at 8 tests" are not swept in.
_CONTINUATION_WORDS = ("wait",)

# Two-word openers that carry no first-person pronoun.
_CONTINUATION_PHRASES = ("hold on", "one moment")

# Above this, the final sentence is a summary rather than a hand-off. A
# hand-off is short by nature -- the measured one is 38 characters.
_MAX_HANDOFF_CHARS = 160

CONTINUATION_NUDGE = (
    "You stopped without calling a tool, but your last message announced "
    "something you were about to do rather than reporting what you did.\n\n"
    "If you are finished: reply with one or two lines saying what you did. "
    "That ends your turn, and it is the only thing the caller sees.\n\n"
    "If you meant to keep going: do it now -- call the tool. This is your "
    "last turn either way."
)
"""One extra turn, and it says so.

Bounded out loud on purpose (OPEN-98 scope 4): a false positive costs one
round trip, and the wording has to make stopping the easy answer so it does
not also cost a finished agent inventing more work. It leads with neither
`Error:` nor any other first-line marker, because `_message_is_error` and
`repeat_guard` both key on one (OPEN-94).
"""


def _final_sentence(text: str) -> str:
    """The last sentence of `text`, lower-cased and flattened.

    Only the last one decides. A report that mentions a next step in passing
    -- "Let me explain what I did: ... All 12 tests pass." -- is a report,
    and judging the whole message would call it a hand-off.
    """
    body = " ".join(text.split())
    if not body:
        return ""
    return body.rstrip(".!? ").split(". ")[-1].strip().lower()


def announces_continuation(text: str) -> bool:
    """Does this final message describe a NEXT action rather than a result?

    Narrow on purpose. A false negative costs exactly what happens today; a
    false positive costs a round trip and risks a finished agent inventing
    work, so every uncertain shape answers False.

    Measured shape (OPEN-98, run `2cde3406f7d6` at=180.333):
    "Wait, I made an error. The file path should be relative to the project
    root, not absolute. Let me check the project structure."

    "actually," is deliberately NOT an opener, though it is the most
    obviously continuation-shaped word left out: "Actually the file was
    already correct" is a report, and this rule's failure mode is calling a
    report a hand-off.
    """
    last = _final_sentence(text)
    if not last or len(last) > _MAX_HANDOFF_CHARS:
        return False

    words = last.split()
    if words[0].strip(",:;-") in _CONTINUATION_WORDS:
        return True
    if last.startswith(_CONTINUATION_PHRASES):
        return True

    for lead in _LEAD_INS:
        if last.startswith(lead):
            last = last[len(lead) :].lstrip()
            break

    for opener in _CONTINUATION_OPENERS:
        if not last.startswith(opener):
            continue
        rest = last[len(opener) :].split()
        return bool(rest) and rest[0].strip(",:;-") not in _SIGN_OFF_VERBS
    return False


def nudge_outcome_of(*, calls: int, reply: str | None) -> str:
    """What the nudged turn did, as the one word `subagent_done` records.

    `calls` is how many tool calls the nudged turn made; `reply` is the text of
    its last AIMessage, `""` when that message carried none, and None when the
    turn produced no message at all.

    - `continued` -- it called a tool. The heuristic caught an unfinished agent
      and the nudge got the work done.
    - `confirmed_done` -- it answered in words that are not a hand-off. The
      only outcome that says the agent HAD finished, so the only one CLAUDE.md
      8a lets argue for narrowing the opener list.
    - `announced_again` -- it answered in words that announce the next step a
      second time (OPEN-122). The heuristic was right and the agent still did
      not act. Run `a04f89bd2ed6`'s t8 coder, the first live nudge, replied
      "...I'll also update `database.py` to remove the duplicate `Todo` class
      definition." and was recorded `confirmed_done`, which reads the one true
      positive on record as a reason to remove it.
    - `unanswered` -- no message came back. The time bound ends a nudged turn
      in its `before_model` hook when the answer that earned the nudge crossed
      the limit (OPEN-114), and no model was asked anything.

    Before OPEN-122 a tool call was the only thing this looked at, so the last
    three were all `confirmed_done`.
    """
    if calls:
        return "continued"
    if reply is None:
        return "unanswered"
    if announces_continuation(reply):
        return "announced_again"
    return "confirmed_done"


# The error markers moved to rudra.trace.stream in Step 15a. Three copies
# of this list existed and all three had drifted -- see that module's
# ERROR_MARKERS docstring for what each one was missing.


@dataclass(frozen=True)
class SubagentContext:
    """Everything one run of subagents needs, built once.

    Bundled for the reason Gate itself is (permissions/__init__.py:50-56):
    these must be the *same* objects across calls. A second Gate would hold
    different session grants and re-prompt for something the user already
    answered `always` to.
    """

    project_path: Path
    backend: Any
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = ""
    # The run's FactStore, shared by reference so a fact recorded during
    # one task reaches the next dispatch's prompt (Step 10a). Optional
    # because the subagent machinery must stay constructible without a
    # run -- 9b's tests build a context with no facts at all.
    facts: Any = None
    # The run's skill sources, or None when skills are off. Shared by
    # reference for the same reason the gate and FactStore are: one run,
    # one cache. Only specs with wants_skills actually receive them.
    skills_sources: tuple[str, ...] | None = None
    # The run's RunUsage, shared by reference for the same reason the gate
    # and the FactStore are: one run, one tally. Optional because the
    # subagent machinery must stay constructible without a run -- 9b's
    # tests build a context with no facts either.
    usage: Any = None
    # The run's McpClient, or None when MCP is off or nothing is configured.
    # Shared by reference for the reason the gate and FactStore are: one run,
    # one set of server processes and one cached catalog.
    mcp: Any = None
    # The run's MemoryStore, or None when it could not be built. Shared by
    # reference for the reason the gate and FactStore are: one run, one
    # palace handle and one cached collection. Optional because the
    # subagent machinery must stay constructible without a run.
    memory: Any = None
    # The run's Langfuse client, or None when it is not configured
    # (OPEN-110). Shared by reference for the reason the gate and the
    # FactStore are: one run, one trace -- two clients would be two traces
    # of one run, which is the thing this feature exists to avoid.
    telemetry: Any = None
    # The run's TraceSink, or None when nothing is watching. Shared by
    # reference for the reason the gate and the FactStore are: one run,
    # one level and one record of it. Optional because the subagent
    # machinery must stay constructible without a run -- every 9b-era
    # test builds a context with no facts either.
    #
    # Before Step 15a there was no field here and no printing: this loop
    # consumed every chunk to drive the guards below and rendered none of
    # them, so the coder, tester and reviewer were invisible while they
    # did the run's actual work.
    trace: Any = None


@dataclass(frozen=True)
class SubagentResult:
    """What one subagent invocation produced.

    `text` is the final assistant message because that is all a delegating
    parent would have seen too (subagents.py:117) -- the direct and
    delegated paths return the same thing.

    ok=False with halted_reason means the subagent misbehaved; with error
    means it never ran. Step 9c needs to tell those apart for the same
    reason 9a's report splits `escalate` from a plain failure.

    `tools` is the invocation's tool histogram, and **None is not `{}`**
    (OPEN-98). None means nothing measured it -- a result built by hand, or
    a stand-in in the tests; `{}` means the invocation ran and called
    nothing. A consumer asking "did this agent do its job" must read the
    difference, or it fires on the harness rather than on a run, which is
    TODO.md's second watched habit. `run_subagent` always supplies it, even
    on the failure paths, because "what had it done when it died" is the
    question those paths are read for.
    """

    name: str
    text: str
    ok: bool
    halted_reason: str | None = None
    error: str | None = None
    tools: Mapping[str, int] | None = None


def _wants_token_stream(context: Any) -> bool:
    """Is token streaming on for this run?

    Read defensively: 9b-era callers -- and several tests -- build a
    SubagentContext with `cfg=None`, and an observability setting must not
    be the thing that makes those unbuildable.
    """
    agent_cfg = getattr(getattr(context, "cfg", None), "agent", None)
    return bool(getattr(agent_cfg, "stream_tokens", False))


def _invocation_limit(context: Any) -> float:
    """Seconds this invocation may spend, or 0.0 for no bound (OPEN-91).

    Read as defensively as `_wants_token_stream` above and for the same
    reason: 9b-era callers build a SubagentContext with `cfg=None`, and a
    runaway guard must not be the thing that makes those unbuildable. An
    unreadable value falls back to the constant rather than to no bound --
    the degraded mode of a guard is the guard, not its absence.
    """
    agent_cfg = getattr(getattr(context, "cfg", None), "agent", None)
    raw = getattr(agent_cfg, "max_invocation_seconds", MAX_INVOCATION_SECONDS)
    try:
        limit = float(raw)
    except (TypeError, ValueError):
        return MAX_INVOCATION_SECONDS
    return limit if limit > 0 else 0.0


def _call_key(tool_call: dict, project_root: Path) -> tuple[str, ...]:
    """What makes two tool calls "the same call" for the repeat guard.

    THE PATH IS RESOLVED, NOT QUOTED (OPEN-50). `compat/virtual_paths.py`
    is the one function that says which real file a model-written path
    names, and the gate, the approval preview and the backend all route
    through it "so they cannot disagree about which file a call touches"
    (CR-B4). This guard did not, so `/app.py`, `./app.py`, `app.py` and
    `<project>/app.py` were four keys for one file -- and it failed in both
    directions at once. Run b593a6137c64 wrote two real files 34 times over
    8 spellings: three-per-spelling means twelve writes before anything
    counts three, and then it halted the invocation anyway, ten times, every
    one a respelling. Both of that run's blocked tasks died of it.

    A1.92 is the same weakness reached through glob patterns and is NOT
    closed by this -- see MAX_TOTAL_CALLS above. Two patterns are not one
    path, and `glob` is deliberately still unwatched.

    Only the path arguments are resolved. `task` carries a `subagent_type`,
    which is not a path and keys exactly as it did.

    The read window is part of it (OPEN-35). `read_file` pages -- 100 lines
    at a time, `offset`/`limit` on deepagents' ReadFileSchema -- so keying
    on the path alone made page 3 of a file the third repeat of page 1, and
    `MAX_REPEATED_CALLS` killed the subagent. No agent could read a file
    past ~200 lines; run 36023bb8bdd1 ended with the reviewer asking for
    `offset: 200` of a 400-line test file. **Resolving the path does not
    retire the window** -- dropping it re-opens OPEN-35.

    THE PAYLOAD IS PART OF IT (OPEN-60). Resolving the path answered
    "which file", and this answers "the same write, or the next one". Until
    it did, three DIFFERENT writes to one file tripped the same halt as
    three identical ones -- and the halt is fatal, so a coder legitimately
    building a file up had its whole invocation killed and its work thrown
    away. Measured over run9 and run11: of thirteen sequences that reached
    MAX_REPEATED_CALLS, 4 carried identical content, 5 were mixed, and 4
    were entirely different content, i.e. an agent doing real work. run11
    invocation 3 wrote `app.py` at 45 bytes, then 404, then 352 -- three
    distinct shas -- and was halted for it.

    Hashed, never stored: this key lives in a dict for the life of an
    invocation, and keeping raw file bodies there would hold a file's worth
    of memory per write.

    `edit_file` carries a patch rather than a body, so its discriminator is
    the (old_string, new_string) pair. `read_file` and `task` carry no
    payload at all and key exactly as they did.

    The guard's target is unchanged: the identical call made again. Tools
    with no window -- write_file, edit_file, task -- carry an empty one.
    """
    args = tool_call.get("args") or {}
    raw = args.get("file_path") or args.get("path")
    if raw:
        # `or str(raw)` covers a resolver that declines to place the path;
        # the guard counts, it never rewrites the call, so an unresolvable
        # spelling keys on itself rather than on nothing.
        identifier = virtual_to_relative(str(raw), project_root) or str(raw)
    else:
        identifier = args.get("subagent_type") or ""
    window = (str(args.get("offset", "")), str(args.get("limit", "")))
    return (tool_call.get("name", ""), str(identifier), *window, _payload_key(args))


def _payload_key(args: dict) -> str:
    """A digest of what a call would put on disk, or "" if it carries none.

    Separate from `_call_key` because the two answer different questions
    and only one of them is about paths. NUL-joined so an edit whose
    old_string ends where its new_string begins cannot collide with one
    split the other way.
    """
    body = args.get("content")
    if not isinstance(body, str):
        old, new = args.get("old_string"), args.get("new_string")
        if not isinstance(old, str) and not isinstance(new, str):
            return ""
        body = f"{old}\x00{new}"
    return hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()


async def run_subagent(
    name: str,
    prompt: str,
    *,
    context: SubagentContext,
    thread_id: str | None = None,
) -> SubagentResult:
    """Run one subagent to completion and report what it said.

    Args:
        name: A REGISTRY key. An unknown name raises -- that is a
            programming error, not a runtime one.
        prompt: The task, as a user message.
        context: The shared per-run objects.
        thread_id: Override the generated one. Each invocation otherwise
            gets a fresh thread, matching what the orchestrator does for
            retries (main_agent.py:351) so a re-run starts clean. This does
            not fix A1.2 -- checkpoints are still never resumed.

    Returns:
        A SubagentResult. Never raises for a runtime failure.
    """
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        msg = f"unknown subagent '{name}'; available: {known}"
        raise ValueError(msg)

    spec = REGISTRY[name]

    # Started before build_agent, not after: a spec that fails to assemble
    # still cost the user the wait, and an invocation missing from the log
    # is the shape OPEN-89 was filed on.
    invocation_started = time.monotonic()
    # What this role's model had cost before the invocation, so a time halt
    # can say how much of it was waiting on the model (OPEN-113).
    model_before = model_time_of(getattr(context, "usage", None), spec.role)
    try:
        # The prompt IS the task: passing it through is what makes the
        # subagent's memory recall about the work rather than about its own
        # name (CR-C3).
        agent = build_agent(spec, context, prompt)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        log_invocation(
            name,
            seconds=time.monotonic() - invocation_started,
            total_calls=0,
            tools={},
            ok=False,
            error=str(exc),
        )
        return SubagentResult(name=name, text="", ok=False, error=str(exc), tools={})

    thread = thread_id or f"{context.session_id}-{name}-{uuid.uuid4().hex[:8]}"
    config: dict[str, Any] = {"configurable": {"thread_id": thread}}
    telemetry = getattr(context, "telemetry", None)
    if telemetry is not None:
        # One Langfuse trace per RUN, not per invocation (OPEN-110): every
        # subagent dispatch carries the same trace id, so a maintainer opens
        # one link and sees the coder, the tester and the planner stages in
        # one tree. langgraph propagates `callbacks` into subgraphs, so the
        # model and tool spans under this agent come for free.
        config.update(telemetry.config(name))

    # One counter per subgraph namespace, not one for the run (A1.20).
    # Parent and subagent carry separate `messages` lists, so a shared
    # counter advances past the shorter one and the guards below stop
    # seeing messages they are meant to count.
    seen: dict[tuple[str, ...], int] = {}
    state = StreamState(role=name)
    consecutive_failures = 0
    total_calls = 0
    # Counted alongside total_calls rather than derived from it: the count
    # says an invocation was busy and the histogram says what it was busy
    # WITH, which is the half that names the defect (OPEN-89).
    tools_used: dict[str, int] = {}
    repeated: dict[tuple[str, ...], int] = {}
    halted: str | None = None
    last_text = ""
    # 0.0 means no time bound; the call ceiling above still holds (OPEN-91).
    limit = _invocation_limit(context)

    def announce(reason: str, where: tuple[str, ...], index: int) -> None:
        """Say that a guard fired.

        A guard firing is a thing RUDRA did, so no chunk carries it and
        `trace.feed` cannot see it (OPEN-44). One spelling, because all
        four guards funnel here -- and TODO.md's lesson 5 is that a new
        guard is a new silence unless it is wired to something.

        The namespace and index are where it fired: a delegate's events
        read `role: coder` with a namespace and are not the coder's own,
        which is the distinction OPEN-37 turned on. The time bound fires in
        a `before_model` hook and is announced once the stream has drained
        (OPEN-114), so it passes the empty namespace -- it is a fact about
        the invocation, not about a position in a subgraph's message list.
        """
        if context.trace is None:
            return
        context.trace.notice(
            reason,
            role=state.role,
            name="guard",
            namespace=where,
            index=index,
            at=time.monotonic() - state.started,
        )

    nudged = False
    nudge_outcome: str | None = None

    async def drain(payload: dict) -> str | None:
        """Stream one turn of this invocation, updating every counter.

        Called at most twice (OPEN-98), and everything it touches is
        invocation-scoped rather than turn-scoped: `seen` keeps its
        namespace positions so the nudge's messages are counted once,
        `total_calls` and `limit` keep running so a nudge cannot buy an
        agent a second call ceiling, and `halted` short-circuits the second
        call entirely.

        Returns the one thing that IS turn-scoped: the text of the last
        AIMessage this turn produced, or None when it produced none. The
        nudge's outcome needs it and `last_text` cannot give it -- that is
        the invocation's last non-empty text, so a nudged turn that said
        nothing would read as the announcement that earned the nudge
        (OPEN-122).
        """
        nonlocal halted, last_text, total_calls, consecutive_failures
        reply: str | None = None

        async for chunk in run_with_approvals(
            agent,
            payload,
            config,
            context.gate,
            context.console,
            trace=context.trace,
            stream_tokens=_wants_token_stream(context),
            role=name,
        ):
            if halted is not None:
                break

            namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            messages = event.get("messages", []) if isinstance(event, dict) else []

            if context.trace is not None:
                context.trace.feed(chunk, state)

            # `where`, not `key`: the guard body below binds `key` to a
            # (tool, target) pair, and a shared name would file this
            # namespace's position under a tool call.
            where = tuple(namespace or ())
            processed = seen.get(where, 0)
            while processed < len(messages):
                message = messages[processed]
                kind = type(message).__name__

                if kind == "AIMessage":
                    text = str(getattr(message, "content", "") or "").strip()
                    reply = text
                    if text:
                        last_text = text
                    for tool_call in getattr(message, "tool_calls", []) or []:
                        # Counted for EVERY tool, before the _WATCHED_TOOLS
                        # filter: A1.92's runaway was 204 successful globs,
                        # and glob is not watched and never failed.
                        total_calls += 1
                        called = tool_call.get("name") or "?"
                        tools_used[called] = tools_used.get(called, 0) + 1
                        if total_calls >= MAX_TOTAL_CALLS:
                            halted = (
                                f"{total_calls} tool calls in one invocation -- stopping. "
                                f"The last was '{tool_call.get('name', '?')}'."
                            )
                            break
                        if tool_call.get("name") not in _WATCHED_TOOLS:
                            continue
                        key = _call_key(tool_call, context.project_path)
                        repeated[key] = repeated.get(key, 0) + 1
                        if repeated[key] >= MAX_REPEATED_CALLS:
                            halted = (
                                f"'{key[0]}' on '{key[1]}' repeated {repeated[key]}x -- stopping"
                            )
                            break
                elif kind == "ToolMessage":
                    if _is_rudra_refusal(message):
                        # NO EVENT (OPEN-94). Rudra short-circuited this
                        # call, so no tool ran: it is neither a failure --
                        # this counter is for an agent flailing against a
                        # broken environment -- nor a success that clears
                        # a real streak. Counting it made the counter fire
                        # faster the better the guard worked, and the
                        # refusals are the CHEAPEST calls in a run: run
                        # 2cde3406f7d6's coder reached three in 11.67 s.
                        pass
                    elif _message_is_error(message):
                        consecutive_failures += 1
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            halted = f"{consecutive_failures} consecutive tool failures -- stopping"
                    else:
                        consecutive_failures = 0

                if halted is not None:
                    announce(halted, where, processed)
                    break
                processed += 1
            seen[where] = processed

        # The time bound is not read here (OPEN-114). It fires in
        # SpanDeadlineMiddleware's `before_model` hook, which runs after the
        # previous answer's tools have run and before the next call is paid
        # for; this loop used to read the clock as a chunk ARRIVED and
        # `break`, which closed the generator langgraph's step loop lives
        # inside and cancelled the tool node -- on the subagent stack that
        # discarded call is typically `write_file`, the deliverable itself.
        #
        # So the halt is ANNOUNCED here instead, once the stream has drained:
        # the sentence needs `total_calls` and the model-latency share, which
        # only this function holds. `halted is None` keeps the first guard to
        # fire, and the announcement before the nudge below is what stops a
        # halted invocation being handed another turn.
        if halted is None and deadline.tripped_at is not None:
            halted = (
                f"{deadline.tripped_at:.0f}s in one invocation, over the {limit:.0f}s limit "
                f"-- stopping after {total_calls} tool calls."
            )
            # Where the time went, from the tally that already holds it
            # (OPEN-113): "over the limit" alone reads the same for a
            # 55-call hunt and for a provider taking six minutes a call.
            wait = model_wait_note(
                getattr(context, "usage", None), spec.role, model_before, deadline.tripped_at
            )
            if wait:
                halted = f"{halted} {wait}"
            announce(halted, (), 0)
        return reply

    try:
        # ONE deadline for the invocation, nudge included: the second turn
        # must not buy an agent a second allowance, exactly as `total_calls`
        # and the call ceiling carry across it. The context manager resets the
        # variable however the span ends -- a leak would bound the NEXT
        # invocation from this one's start, and this function reports a raised
        # exception rather than raising it.
        with span_deadline(limit) as deadline:
            await drain({"messages": [{"role": "user", "content": prompt}]})
            # ONE more turn, and only when the last thing said was a plan
            # rather than a report (OPEN-98). After the guards, so a halted
            # invocation is never handed another turn -- that would undo the
            # guard -- and bounded by construction: there is one call site,
            # and the nudge itself says it is the last turn either way.
            if halted is None and announces_continuation(last_text):
                nudged = True
                calls_before = total_calls
                reply = await drain({"messages": [{"role": "user", "content": CONTINUATION_NUDGE}]})
                # What the number is FOR (CLAUDE.md 8a): `continued` and
                # `announced_again` both say the heuristic caught an unfinished
                # agent, and only `confirmed_done` says it fired on a finished
                # one. A tool call alone cannot tell those apart -- run
                # a04f89bd2ed6's one live nudge announced its next step again
                # and was recorded done (OPEN-122).
                nudge_outcome = nudge_outcome_of(calls=total_calls - calls_before, reply=reply)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        log_invocation(
            name,
            seconds=time.monotonic() - invocation_started,
            total_calls=total_calls,
            tools=tools_used,
            ok=False,
            error=str(exc),
            nudged=nudged,
            nudge_outcome=nudge_outcome,
        )
        return SubagentResult(
            name=name, text=last_text, ok=False, error=str(exc), tools=dict(tools_used)
        )

    # Every exit is logged, including the ordinary one. A record that only
    # covers the failures cannot answer "was this invocation unusual?",
    # which is the question a slow run actually asks.
    log_invocation(
        name,
        seconds=time.monotonic() - invocation_started,
        total_calls=total_calls,
        tools=tools_used,
        ok=halted is None,
        halted=halted,
        nudged=nudged,
        nudge_outcome=nudge_outcome,
    )
    if halted is not None:
        return SubagentResult(
            name=name,
            text=last_text,
            ok=False,
            halted_reason=halted,
            tools=dict(tools_used),
        )
    return SubagentResult(name=name, text=last_text, ok=True, tools=dict(tools_used))


__all__ = [
    "MAX_INVOCATION_SECONDS",
    "CONTINUATION_NUDGE",
    "MAX_TOTAL_CALLS",
    "SUBAGENT_KIND",
    "SubagentContext",
    "SubagentResult",
    "announces_continuation",
    "log_invocation",
    "run_subagent",
]
