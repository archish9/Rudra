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

**No refusal reads as a tool failure, and since OPEN-94 that is structural
rather than a spelling convention.** `trace/stream.py` counts a result whose
first line begins "Error"/"Traceback"/"Errno"/"[Errno"/"BLOCKED:" as one, and
`subagents/runner.py` halts a subagent after three consecutive failures -- so
an "Error:"-prefixed message would convert this saving into three dead
invocations, which is OPEN-16's shape. Two refusals dodged that by leading
with "Already read:"/"Already written:"; the third leads with "Error:"
because that is what the MODEL must read, and it was counted. Every refusal
now carries `REFUSAL_KEY` in `additional_kwargs` (`_as_message`, the one seam
they are all built at) and `message_is_error` answers from that field before
it looks at any text. The prose is untouched in all three -- prefixing it to
make it classifiable is what the paragraph below forbids.

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

**Three rules now, and the third is about writes (OPEN-60).** A write whose
content is byte-identical to what this middleware last saw written to that
same path, with no intervening tool call of any kind, is refused rather than
performed. It is the safest of the cases in this file and the argument is
not the read one: a repeated read is refused because the ANSWER is already
in the transcript, and a no-op write is refused because the POST-CONDITION
is already satisfied -- the bytes requested are the bytes present. Nothing
is cached and nothing is served from a copy.

Measured over five runs before it existed: 30 of 127 writes were
byte-identical rewrites, ~93,280 characters of OUTPUT -- the expensive kind
-- and run9 re-emitted 33% of everything it wrote. Under the strict
invalidation this implements, 20 of those 127 are refusable.

**Invalidation is by capability, not by symmetry.** Any call that CAN change
a file -- `execute`, `edit_file`, `delete`, anything unguarded -- drops the
belief. A read does not, because a read cannot change a file. That
asymmetry with the read rules above is deliberate and was measured: the
symmetric version shipped first, and every no-op rewrite that still got
through it was let through by a read (`read_file` 6, `ls` 1, `glob` 1 across
five runs), not one by a call that can change anything. Measured by driving
this middleware over all five runs of evidence, refusing them takes the catch
rate from **20 of 28 to 26 of 28**, ~17,697 to ~21,756 output tokens.

**And by PATH, since OPEN-62 6c, wherever there is one path to be by.** A
`delete` or an `edit_file` changes the one file it names, so it drops that
file's belief and leaves the rest standing; `execute` -- and an MCP tool, and
`task`, and anything else this file has never heard of -- still drops
everything, because a blast radius that is unknown has to be assumed total.
The old rule cleared the whole map on any of them, which is path-blind: run9
deleted `app/tests/__pycache__` and thereby discarded what the guard knew
about `tests/test_app.py`, then rewrote it byte-identically. 3,818
characters, the largest single waste measured over run8-run13, and the same
driving method puts the catch rate at **27 of 28, ~22,711 output tokens**.

Invalidation compares spellings loosely (`_same_file`) while the KEY stays
the path the model typed. The two are different questions and they fail in
opposite directions: a key that over-matches refuses a write that was needed,
and an invalidation that over-matches costs one rewrite. Resolving the key
is OPEN-52's change and its own commit (OPEN-62 6b).

Of the two rewrites still performed after OPEN-60, ONE was OPEN-52's and the
other was this file's own bug. Corrected 2026-08-31 (OPEN-62 §5): the record
here used to say run9 wrote `tests/test_app.py`, DELETED it and wrote the
same 3,818 characters back -- real work, because the file was gone. run9's
log disagrees. The deletes named `app/tests/test_app.py` and
`app/tests/__pycache__`; the file rewritten was `tests/test_app.py`, which
was never deleted. **27 of the 28 were refusable, not 26**, and the sentence
had been repeated into four files. The other was run11's: it wrote
`app.py` and then `/app.py`, two spellings of ONE file, because `_target`
read the path the model typed. **That was OPEN-52, and it is fixed
(OPEN-62 6b)** -- `_target` and `_key_args` both resolve through
`compat/virtual_paths.py` now, so this middleware answers "which file" the
way the gate, the approval preview and the backend do (CR-B4). The item had
been measured four times at +0 on the read rules and was one run from
`WONTFIX`; the write rule is what changed the answer, and only in company
with 6a -- resolving the spelling finds nothing if the belief died with the
invocation, and the belief finds nothing if the spelling is a different key.
Driven over run8-run13 the three together refuse 30 of the 32
byte-identical rewrites those runs contain, ~23,542 output tokens. The two
they do not are an artifact of the replay rather than a gap: it reads each
project's END state, so a file that kept changing after its rewrite
(run10's `models.py` -- 1,830 characters written, 2,538 on disk today)
cannot confirm the belief and the write is performed. Every measurement
here is a lower bound for that reason.

**A claim in a closed record is evidence about what was believed, not about
what happened.** That one survived a review, a ledger entry and three
restatements because every reader checked it against the sentence rather
than against run9's log.

**The belief outlives the invocation, and the refusal is a fact (OPEN-62
6a).** This middleware is built PER INVOCATION (`subagents/runner.py:263`),
so `_written` starts empty each time -- and run13 spent two entire coder
invocations, 137 of its 368 task-loop seconds, re-emitting files an earlier
task had already written. The belief therefore also lives on `RunUsage`,
keyed by role, which is the object that already outlives an agent rebuild
and the shape OPEN-61 reached for one day earlier for the same reason.

The two maps are not the same kind of claim, and that is the whole design.
`_written` is what THIS invocation did and watched: it refuses on its own
authority, and it is invalidated by anything that could have changed a file.
The run map is what SOME EARLIER invocation did, across a boundary where
Rudra ran the gate, the fix loop and git and this middleware saw none of it
-- so it refuses nothing until `_disk_digest` has read the target and found
exactly those bytes. Nothing invalidates it, because the file answers every
question invalidation would have guessed at, and a rule that dropped the
belief on any intervening command was measured to be worth exactly zero:
coders run tests.

**This corrects a disposition that was written before there was a case to
test it against.** The paragraph here used to say across-invocation
repetition belongs to `loop/bounds.py` and that D9 is why it stays there.
`bounds.py` takes a `VerifyReport` and fires on two identical gate FAILURES
in a row (`loop/bounds.py:16,48`); run13's rewrites happened on tasks the
gate PASSED, so it never saw them and structurally cannot. And D9 forbids an
LLM deciding termination -- comparing two digests and then reading a file is
Python deciding, exactly as `_blocked_write` already was one invocation
lower down.

One consequence was decided here and REVERSED by OPEN-94, and the reversal
is worth reading because the original argument is a good one. It ran: the
failure refusal leads with "Error:", `subagents/runner.py` counts it, and a
third identical failing read becomes the third consecutive failure that
halts the invocation -- which is what would have happened before this guard
existed, so returning something the counter could not see was masking those
halts.

What it missed is that both things are true at once. For the MODEL the call
did not succeed. For the COUNTER no tool ran, and that counter exists to
spot an agent flailing against a broken ENVIRONMENT -- real calls really
failing. A short-circuit is the opposite signal: the guard is working,
cheaply, and the agent is still exploring. Counting them together makes the
counter fire faster the better the guard works, and it is unbounded --
`_failures` never expires within a turn, so once a signature has two real
failures every later call resolving to it is a free "Error:" line. Run
2cde3406f7d6's first coder reached three in 11.67 s and died having written
nothing; two of the three were this middleware's own text.

So a refusal is NO EVENT at both counters: it neither increments them nor
clears them. Both halves are needed -- clearing would let the guard weaken
the runaway bound in the other direction, with two real failures either side
of a free short-circuit never meeting.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.compat.virtual_paths import virtual_to_host, virtual_to_relative
from rudra.middleware.tool_route import runner_search_route, settled_write_route
from rudra.trace.stream import REFUSAL_KEY

# Deterministic reads. A repeat of one of these after a failure cannot
# succeed, which is what makes short-circuiting safe. `execute` is
# excluded on purpose -- see the module docstring.
_GUARDED_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})

# The same set under a public name, because a second guard reads it (OPEN-125).
# `subagents/runner.py` resets its `read_file` counts on any call outside it,
# which is this module's no-progress rule applied to the invocation halt --
# one definition, so the two guards cannot disagree about which calls are reads.
NO_PROGRESS_READS = _GUARDED_TOOLS

# Writes whose repeat is refusable, and it is a SEPARATE set on purpose
# (OPEN-60). `_GUARDED_TOOLS` means "reads whose repeat is refusable" and
# both read rules iterate it, so adding a write to it would apply the
# failure and dedupe rules to writes as a side effect -- and the dedupe
# rule keys on `json.dumps(args)`, which for a write is the file body.
#
# `edit_file` is deliberately outside it. An identical patch is not a
# no-op: re-applying old_string -> new_string once it has applied fails to
# find old_string, so the tool errors and the failure rule already covers
# it. Measured at 10 calls over five runs with 1 repeat, so there is no
# saving to weigh against that. `execute` is outside it for the reason the
# module docstring gives.
_GUARDED_WRITES = frozenset({"write_file"})

# Tools that change exactly the ONE file they name, so what they invalidate
# is that file's write-belief and nothing else (OPEN-62 6c). Everything not
# in here -- `execute` above all, but equally an MCP tool, `task`, or
# anything this file has never heard of -- keeps the wholesale answer,
# because a call whose blast radius is unknown has to be assumed total.
# That is the same capability argument OPEN-60 §14 settled for reads, read
# the other way round: a read cannot change a file, a command can change
# any file, and these two can change one file each.
_PATH_SCOPED = frozenset({"delete", "edit_file"})

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


def _same_file(a: str, b: str) -> bool:
    """Do these two spellings name one file, as far as invalidation cares?

    Deliberately loose and deliberately not `virtual_paths.py`: that
    function needs a project root this middleware is not given, and
    resolving the KEY is OPEN-52's change (OPEN-62 6b). This answers a
    narrower question -- may I keep believing something about the file this
    call just changed? -- where a false "yes" costs a rewrite and a false
    "no" costs the file, so it errs towards forgetting.

    Suffix matching is what covers the shapes the runs actually produce:
    `/app.py` after `app.py` (run11, run13) and an absolute host path after
    a relative one (run9). It over-matches `app.py` against `sub/app.py`,
    which is the direction that is safe.
    """
    a, b = _plain(a), _plain(b)
    if not a or not b:
        return False
    return a == b or a.endswith(f"/{b}") or b.endswith(f"/{a}")


def _plain(path: str) -> str:
    """One spelling of a path, with the decoration that never distinguishes
    two files removed: the separator, leading `./` segments, a leading `/`.

    `lstrip(".")` would do the same job in one call and get `.env` wrong,
    turning it into `env` -- which is only ever safe by accident.
    """
    plain = path.replace("\\", "/")
    while plain.startswith("./"):
        plain = plain[2:]
    return plain.lstrip("/")


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


# A `bin` directory anywhere in the path, or a last segment naming an
# interpreter or a test runner. Every shape the archive's interpreter hunts
# used -- `**/.venv/bin/pytest`, `/usr/bin/python*`, `**/.venv/**/python*` --
# and not `pytest.ini` or `test_python_utils.py`, which are files to read.
_RUNNER_SEARCH = re.compile(
    r"(?:^|[/\\])bin(?:[/\\]|$)|(?:^|[/\\*])(?:python[\d.]*|pytest|py\.test|pip[\d.]*)\*?$",
    re.IGNORECASE,
)
_SEARCH_ARGS = ("pattern", "path", "file_path")


def runner_shaped(args: dict[str, Any]) -> bool:
    """Is this read a search for something to RUN the project with (OPEN-134)?"""
    return any(
        isinstance(args.get(key), str) and _RUNNER_SEARCH.search(args[key]) is not None
        for key in _SEARCH_ARGS
    )


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
        project_path: Any = None,
        granted: frozenset[str] | tuple[str, ...] = (),
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
        # Where a virtual path lands on this host, and the only reason this
        # middleware needs one: an inherited belief is confirmed against the
        # file before it refuses anything (OPEN-62 6a). None disables that
        # confirmation, and with it every cross-invocation refusal.
        self.project_path = project_path
        # The tools this agent holds, so the write refusal can name a route
        # without naming a tool the agent lacks (OPEN-126, OPEN-15). NOT
        # `self.tools`: `AgentMiddleware.tools` is what a middleware
        # CONTRIBUTES, and langchain registers every entry of it.
        self.granted = frozenset(granted)
        self._failures: dict[str, int] = {}
        self._last_error: dict[str, str] = {}
        # How many times each signature has already been answered
        # successfully with nothing since that could have changed the
        # answer. A count rather than a set, so `max_identical_reads` is a
        # real dial and the two rules read the same way.
        self._answered: dict[str, int] = {}
        # What we believe is on disk at each path, as a digest of the last
        # content successfully written there, with nothing since that could
        # have changed it. A digest rather than the body: this dict lives
        # for the whole agent run, and holding raw file contents in it
        # would cost a file's worth of memory per write.
        self._written: dict[str, str] = {}

    def _shared(self) -> dict[str, str] | None:
        """The RUN's write-belief map for this role, or None.

        **Nothing invalidates it, and that is the design rather than an
        omission.** `_written` is invalidated because it refuses on its own
        authority; this one refuses nothing until `_disk_digest` has read
        the file and found exactly those bytes, so a `delete`, an
        `edit_file` or an `execute` between the two writes is already
        answered by the file itself -- gone, changed, or genuinely still
        holding them. Invalidating it as well would only make the guard
        forget things that are true, and measured over run8-run13 that is
        not hypothetical: clearing it on any intervening command dropped
        every cross-invocation belief a coder held, because coders run
        tests.

        `_written` above is this invocation's own, and it is not enough:
        `subagents/runner.py:263` builds a new middleware per dispatch, so
        the coder that wrote `database.py` for task 1 and the one that
        rewrote it byte-identically for task 2 shared nothing at all
        (OPEN-62 6a). The map that outlives them is `RunUsage`'s, which is
        the shape OPEN-61 reached for one day earlier and for the same
        reason.

        Duck-typed on `RunUsage` the way `usage` already is
        (`build.py:239-243`): a stand-in without the method degrades to the
        per-instance behaviour rather than raising inside a tool call.
        """
        if self.usage is None or self.role is None:
            return None
        beliefs_for = getattr(self.usage, "beliefs_for", None)
        if beliefs_for is None:
            return None
        try:
            beliefs = beliefs_for(self.role)
        except Exception:  # noqa: BLE001 -- accounting never ends a run
            return None
        return beliefs if isinstance(beliefs, dict) else None

    def _disk_digest(self, target: str) -> str | None:
        """What is ACTUALLY at `target` right now, fingerprinted, or None.

        This is what turns an inherited belief into a fact. Within one
        invocation the guard performed the write itself and watched
        everything since, so the post-condition argument stands on its own.
        Across a boundary it does not: Rudra runs the gate, the fix loop
        and git in between and this middleware sees none of it, so a
        run-scoped belief that trusted itself would eventually refuse a
        write that genuinely needed making -- and work that never happens
        is strictly worse than a wasted turn (OPEN-59).

        None means "cannot confirm", which is always the answer that
        PERFORMS the write: no project path, a backend route outside the
        project (`/artifacts/`, `/skills/`), a file that is gone, or bytes
        that will not compare -- a text-mode write that translated newlines
        is the one to expect on Windows, and it degrades to the old
        behaviour rather than to a wrong refusal.
        """
        if self.project_path is None or not target:
            return None
        try:
            host = virtual_to_host(target, Path(self.project_path))
            if host is None:
                return None
            return hashlib.sha256(host.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return None

    def _target(self, args: dict[str, Any]) -> str:
        """What the call was aimed at, for the KEY, the refusal and the notice.

        One function, because a notice naming a different path from the
        refusal beside it is worse than a notice with no path at all -- and
        since OPEN-62 6b it is also the write rule's key, so the three
        cannot disagree about which file was meant either.

        **The path is resolved, not quoted (OPEN-52).**
        `compat/virtual_paths.py` is the one function that says which real
        file a model-written path names, and the gate
        (`permissions/rules.py`), the approval preview (`permissions/diff.py`)
        and the backend all route through it so they cannot disagree about
        which file a call touches (CR-B4). This middleware did not, so
        `app.py` and `/app.py` were two keys for one file: run11 wrote 303
        characters that way and run13 wrote 3,022. OPEN-50 landed exactly
        this for the other repeat guard (`subagents/runner.py::_call_key`)
        on 2026-08-29, and its halt text has named the resolved path since.

        Only a path argument is resolved. `pattern` is a grep expression
        that can look exactly like a path, and rewriting it would make two
        different searches one key -- the same carve-out `_call_key` makes
        for `subagent_type`. Without a project path there is nothing to
        resolve against and the spelling is the key, which is the behaviour
        that shipped with OPEN-10.
        """
        raw = args.get("file_path") or args.get("path")
        if raw:
            # `or str(raw)` covers a resolver that declines to place the
            # path -- a backend route like `/artifacts/`. The guard counts,
            # it never rewrites the call, so an unresolvable spelling keys
            # on itself rather than on nothing (`runner.py:203-206`).
            return self._resolved(args) or str(raw)
        return str(args.get("pattern") or "")

    def _resolved(self, args: dict[str, Any]) -> str | None:
        """This call's path as a project-relative spelling, or None.

        `_target`'s first half, lifted out because OPEN-95 needs the
        question it answers rather than the answer: *did* the resolution
        happen? `_target` cannot say -- it falls back to the raw spelling,
        so a `/artifacts/` route and a resolved `src` are the same shape
        coming out of it. `_refusal` must know, because the sentence it
        adds ("these spellings are one call") is TRUE exactly when this
        returned something and false otherwise -- with no project path
        nothing is resolved and the spelling is the key.

        One function rather than two copies of the same three lines:
        `verify/stubs.py::is_build_output` is what happens when a rule
        this small is written out twice (OPEN-63, OPEN-64).
        """
        raw = args.get("file_path") or args.get("path")
        if not raw or self.project_path is None:
            return None
        return virtual_to_relative(str(raw), Path(self.project_path))

    def _key_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """`args` with its path argument resolved, for the read rules' key.

        A copy, never the call's own dict: this middleware counts calls and
        must not change the one the model made. Every other argument is
        left exactly as it arrived, the read window (OPEN-35) included --
        page 2 of a file is not a repeat of page 1, however either was
        spelled.
        """
        if self.project_path is None:
            return args
        keyed = dict(args)
        for field in ("file_path", "path"):
            raw = keyed.get(field)
            if raw:
                keyed[field] = virtual_to_relative(str(raw), Path(self.project_path)) or str(raw)
        return keyed

    def _forget(self, target: str) -> None:
        """Drop the write-belief about ONE file, however it was spelled.

        The key stays the path the model typed -- resolving it is OPEN-52's
        job and its own commit -- so invalidation compares LOOSELY instead:
        `/app.py`, `app.py` and `/Users/.../project/app.py` all name the
        same file, and a belief kept because the delete was spelled
        differently from the write would refuse a write that genuinely
        needed making. The asymmetry is deliberate and it points the safe
        way: over-forgetting costs a rewrite, under-forgetting costs the
        file. `loop/engine.py`'s own rule -- work that never happens is
        strictly worse than a wasted turn (OPEN-59) -- is the same
        preference stated one level up.

        An empty target means the call named no file at all, and a call
        this guard cannot attribute is one it cannot narrow: everything
        goes.
        """
        if not target:
            self._written.clear()
            return
        for believed in [k for k in self._written if _same_file(k, target)]:
            del self._written[believed]

    def _refusal(self, signature: str, name: str, args: dict[str, Any]) -> str:
        """The answer to a read that has already failed identically twice.

        Leads with "Error:", unlike its two siblings, because that is what
        the model must read: this call did not work and will not. Since
        OPEN-94 that word costs nothing -- `_as_message` marks every
        refusal and `trace/stream.py::message_is_error` answers from the
        mark, so no counter reads this prose. Do not restyle it to dodge a
        text check that no longer runs.

        **It names the RESOLVED target, and says nothing about the
        arguments (OPEN-95).** The signature is keyed on the file, not on
        the spelling (OPEN-52), so "these exact arguments" was a claim the
        model could see was untrue: run 2cde3406f7d6's coder was told it
        had already called `ls 'src'` twice when it had typed '/src' and
        the full host path, and was shown an error quoting '/src' as the
        answer to a question about 'src'. It then re-sent the identical
        call and the invocation halted -- the documented consequence of
        handing a model back its own argument with a contradiction
        attached (`gutter_indent.py`'s docstring records the same shape).

        The identity is right; what was missing is the EXPLANATION of the
        identity, which is `_spelling_note`. With it the old advice --
        "change the arguments" -- stops being an instruction that
        describes no possible action, so it goes: there is no spelling
        that is not this call. "use ls to find the correct path" goes for
        the plainer reason that `ls` is one of the four tools that can be
        refused here, so it could answer an `ls` refusal with `ls`. What
        is left is the branch that was third of three and was the only
        one that ever applied.
        """
        seen = self._failures[signature]
        return (
            f"Error: `{name}` on '{self._target(args)}' has already failed "
            f"{seen} times in this turn, and was not run again. The error "
            f"was: {self._last_error.get(signature, 'unknown')}\n"
            f"{self._spelling_note(args)}Re-sending it will not change that "
            f"answer. Look somewhere else, or carry on without it -- and if "
            f"your task is to CREATE that file, write it: parent directories "
            f"are made on the way."
        )

    def _spelling_note(self, args: dict[str, Any]) -> str:
        """Why re-spelling the path is not the way out, or "" (OPEN-95).

        Emitted only when the resolution actually happened, because that
        is exactly when the claim is true. Three cases decline, and each
        would otherwise be this item's own defect pointed the other way:
        no project path, where nothing is resolved and the spelling IS the
        key (`_target`'s last paragraph); a `grep` carrying only a
        `pattern`, which is never resolved because two spellings of a
        pattern are two different searches; and a path the resolver
        declined to place, such as a `/artifacts/` route, which keys on
        itself.

        It spells all three out rather than asserting the rule, because
        the model is looking at one of them and has to recognise it.
        `_PATH_RULES` lists the same three in the same order.
        """
        relative = self._resolved(args)
        if not relative:
            return ""
        return (
            f"'{relative}', '/{relative}' and '{self.project_path}/{relative}' "
            f"are one call here -- this guard keys on the file, not on how it "
            f"was typed -- so re-spelling the path reaches the same place. "
        )

    def _repeat_refusal(self, name: str, args: dict[str, Any]) -> str:
        """The answer to a read that has already been answered.

        Leads with "Already read", never "Error" -- see the module
        docstring. It carries the target because a bare "you did that
        already" leaves the model to work out WHICH of its calls was
        refused, and the whole saving is one round trip.
        """
        target = self._target(args)
        refusal = (
            f"Already read: `{name}` on '{target}' was answered earlier in this "
            f"turn and nothing has changed it since, so it was not run again. "
            f"That earlier result is still current -- use it. To see something "
            f"else, call a different path or pattern; to change the file, write "
            f"or edit it."
        )
        # A repeated search for an interpreter or a test runner is the model
        # trying to run the tests (OPEN-134), so it gets OPEN-126's route.
        # Only that shape: 118 of the archive's 171 dedupe refusals were
        # ordinary re-reads, and a route there is words on the wrong question.
        route = runner_search_route(self.granted) if runner_shaped(args) else ""
        return f"{refusal}\n\n{route}" if route else refusal

    def _write_refusal(self, name: str, args: dict[str, Any]) -> str:
        """The answer to a write whose bytes are already on disk.

        Leads with "Already written", never "Error" -- the module docstring
        says why, and OPEN-16 is what happens when it does not: three
        refusals in a row would read as three tool failures and
        `subagents/runner.py` would halt the invocation. Since OPEN-94 the
        classification is carried by `REFUSAL_KEY` rather than by the
        opening word, so this is now belt and braces rather than the only
        thing holding it.

        It says what the model should do next for the reason the read
        refusal does. "You already did that" leaves the model to work out
        which call was refused, and the whole saving is one round trip.
        """
        target = self._target(args)
        refusal = (
            f"Already written: '{target}' already contains exactly these bytes, "
            f"so `{name}` was not run again. The file is in the state you asked "
            f"for -- nothing needs writing. Move on to the next piece of work, "
            f"or edit the file if you meant to change it."
        )
        # The route the re-send was reaching for (OPEN-126): a script written
        # to run the tests, or the deliverable re-sent to say "done". "Move on"
        # alone was re-sent against in 8 of 13 archived cases.
        route = settled_write_route(self.granted)
        return f"{refusal}\n\n{route}" if route else refusal

    def _content_digest(self, args: dict[str, Any]) -> str | None:
        """The payload this write would put on disk, fingerprinted.

        None when the call carries no string body, which is what makes a
        malformed write fall through to being performed rather than
        refused: the guard counts, it never repairs a call.

        Hashed AFTER FixWriteParamsMiddleware, which `build.py:243` orders
        ahead of this one for exactly this class of reason -- that
        middleware strips markdown fences on the way to disk, so hashing
        the pre-repair payload would compare a fenced body against an
        unfenced one. Measured at 0 fences in 127 writes over five runs, so
        the ordering is what holds this rather than the data.
        """
        content = args.get("content")
        if not isinstance(content, str):
            return None
        return hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()

    def _blocked(self, request) -> str | None:
        """The refusal to return instead of running this call, or None.

        Failures are tested first. The two rules cannot both apply to one
        signature -- a success clears the failure count and a failure is
        never in `_answered` -- but the order is fixed anyway, because a
        reader should not have to prove that to know which message wins.
        """
        name = request.tool_call.get("name")
        args = request.tool_call.get("args", {})
        if name in _GUARDED_WRITES:
            return self._blocked_write(name, args)
        if name not in _GUARDED_TOOLS:
            return None
        signature = _signature(name, self._key_args(args))
        if self._failures.get(signature, 0) >= self.max_identical_failures:
            self._announce(
                f"refused: `{name}` on '{self._target(args)}' failed "
                f"{self._failures[signature]} times identically -- not run again"
            )
            return self._refusal(signature, name, args)
        if self._answered.get(signature, 0) >= self.max_identical_reads:
            self._count_dedupe()
            self._announce(
                f"dedupe: `{name}` on '{self._target(args)}' was already answered "
                f"this turn -- not run again"
            )
            return self._repeat_refusal(name, args)
        return None

    def _blocked_write(self, name: str, args: dict[str, Any]) -> str | None:
        """The refusal for a write that would change nothing, or None.

        Its own function rather than a third branch of `_blocked`, because
        it shares neither the signature nor the state of the two read
        rules: those key on the whole argument dict, this one on the path
        and a digest of the body.
        """
        digest = self._content_digest(args)
        if digest is None:
            return None
        target = self._target(args)
        if self._written.get(target) != digest:
            # Not this invocation's own write, so the run's belief is the
            # only one left -- and it refuses nothing until the file says
            # the same thing (OPEN-62 6a). A hit here is the coder that
            # wrote `database.py` for task 1 meeting the coder rewriting it
            # for task 2, which shares no instance state with it.
            shared = self._shared()
            if shared is None or shared.get(target) != digest:
                return None
            if self._disk_digest(target) != digest:
                return None
        self._count_write_skipped(len(args.get("content", "")))
        self._announce(
            f"skipped: `{name}` on '{target}' would write the bytes already there -- not run again"
        )
        return self._write_refusal(name, args)

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
        untouched: re-wrapping one would drop its status and its id -- and
        since OPEN-94 it would also put `REFUSAL_KEY` on a real tool
        failure, which is the one thing that must keep counting.

        `REFUSAL_KEY` is set on EVERY refusal, not only the failure one
        (OPEN-94). This is the single seam all three rules are built at, so
        a fourth rule is exempted by construction rather than by remembering
        to. The two that already lead with "Already read"/"Already written"
        were never counted as failures; marking them changes them from
        *clearing* the consecutive-failure counter to being no event at
        all, which is what they are.
        """
        return ToolMessage(
            content=refusal,
            name=str(request.tool_call.get("name") or ""),
            tool_call_id=str(request.tool_call.get("id") or ""),
            additional_kwargs={REFUSAL_KEY: True},
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

    def _count_write_skipped(self, chars: int) -> None:
        """One rewrite this guard refused instead of performing (OPEN-60).

        Kept off `reads_deduped` deliberately: that number is OPEN-39
        Phase 2's saving in re-reads, this one is output characters never
        emitted, and two things in one number would be neither.
        """
        if self.usage is None or self.role is None:
            return
        self.usage.record_write_skipped(self.role, chars)

    def _record(self, request, result: Any) -> None:
        name = request.tool_call.get("name")
        if name in _GUARDED_WRITES:
            # A write is still "anything else" to the read rules -- it is
            # exactly how the answer to a read changes -- so their state
            # drops here as it always did. What is new is that this call
            # also SETS what we believe is on disk, which is why it cannot
            # simply fall through to the branch below.
            self._failures.clear()
            self._last_error.clear()
            self._answered.clear()
            digest = self._content_digest(request.tool_call.get("args", {}))
            if digest is None or _is_error(result):
                # A write that errored did not satisfy the post-condition,
                # and a write we could not fingerprint is one we cannot
                # claim anything about. Either way the safe belief about
                # THAT PATH is none -- and about the others it is unchanged,
                # because a failed write to app.py says nothing about what
                # is on disk at test_app.py (OPEN-62 6c).
                self._forget(self._target(request.tool_call.get("args", {})))
                return
            # Added, not replaced. Files are independent -- writing b.py
            # says nothing about a.py -- and a version of this that kept
            # one path at a time refused 23 of 28 across five runs instead
            # of 26, because the coder's real shape is app.py, test_app.py,
            # app.py.
            target = self._target(request.tool_call.get("args", {}))
            self._written[target] = digest
            shared = self._shared()
            if shared is not None:
                shared[target] = digest
            return
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
            # And the same rule read once more, for writes (OPEN-60): a
            # call that can change a file means what we believe is on disk
            # stops being current.
            #
            # Which beliefs, though, is not the same question (OPEN-62 6c).
            # This used to clear the WHOLE map, which is path-blind: run9
            # deleted `app/tests/__pycache__` and thereby discarded what
            # the guard knew about `tests/test_app.py`, then rewrote it
            # byte-identically -- 3,818 characters, the largest single
            # waste measured over run8-run13. A `delete` or an `edit_file`
            # changes the one file it names, so only that belief goes.
            # Anything else still takes the whole map, and `execute` is
            # why: a shell command can touch any file, and that is the
            # safety half of the rule rather than the saving half.
            if name in _PATH_SCOPED:
                self._forget(self._target(request.tool_call.get("args", {})))
            else:
                self._written.clear()
            return
        # `self._written` is deliberately NOT cleared here, and the
        # asymmetry with `_answered` above is the point. A write is how a
        # read's answer changes, so a write drops read-belief. A read
        # changes nothing, so it does not drop write-belief.
        #
        # Measured, after this file shipped the symmetric version: every
        # no-op rewrite that still got through was let through by a read --
        # read_file 6, ls 1, glob 1 across five runs -- and not one by a
        # call that can change a file. Restoring them costs ~4,059 output
        # tokens and protects against nothing this module does not already
        # assume away (a process outside Rudra editing mid-turn).
        signature = _signature(name, self._key_args(request.tool_call.get("args", {})))
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


__all__ = [
    "MAX_IDENTICAL_FAILURES",
    "MAX_IDENTICAL_READS",
    "NO_PROGRESS_READS",
    "RepeatGuardMiddleware",
    "runner_shaped",
]
