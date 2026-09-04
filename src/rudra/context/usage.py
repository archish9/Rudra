"""What one run spent, per role.

One accumulator per run, shared by reference the way the ledger and the
fact store are (loop/ledger.py, facts/store.py): every agent mutates the
same object, and the panel reads it once at the end. A copy would report a
fraction of the run.

`None` is load-bearing here. `usage_metadata` is optional on an AIMessage,
and a local endpoint may omit it. Reporting 0 for a provider that said
nothing is indistinguishable from a free run and would be read as one, so
an unreported count stays None all the way to the panel, which prints
"not reported".

Pure by rule: nothing here imports from Rudra. The middleware that feeds
it lives next door in context/middleware.py, because that one needs
langchain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoleUsage:
    """One role's tally for a run."""

    calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    compactions: int = 0
    # How many of this role's `calls` were re-issues of a call that failed
    # transiently (OPEN-45). It has to be counted separately rather than
    # derived, because ModelRetryMiddleware sits OUTSIDE UsageMiddleware by
    # design (each attempt is one recorded call with its own duration, and
    # the backoff sleep is charged to nobody) -- so `calls` already absorbs
    # every retry and nothing distinguishes the absorbed ones. Measured: a
    # call retried twice records calls=4 against three model turns, which a
    # reader cannot tell from a miscount.
    #
    # It is also what makes OPEN-40's `seconds / calls` readable: a retried
    # call inflates the denominator with an attempt that produced no tokens.
    retries: int = 0
    # How many of this role's model calls ran OUT of retries (OPEN-46,
    # reopened). `retries` counts a failed attempt that WAS re-issued; the
    # attempt that exhausts the budget is a failure too and was counted
    # nowhere, so `p = retries / calls` was a lower bound with nothing
    # saying by how much. run14 measured 37/233 = 15.9% that way against
    # the 5.60% the item first closed on, and lost task t8 to an
    # exhaustion that left no number anywhere.
    #
    # Separate from `retries` rather than folded in, for the reason
    # reads_deduped is separate from writes_skipped: they are different
    # claims. A retry cost seconds and recovered; an exhaustion cost the
    # call, and on the subagent path it costs a task attempt
    # (loop/engine.py:463). One number carrying both would be neither.
    exhaustions: int = 0
    # What the injected recall block cost this role, in characters, summed
    # over every agent build (Step 14b, spec 4.6). Characters rather than
    # tokens because that is what render.py actually budgets in, and a
    # second approximation would only add error. It rides inside
    # input_tokens on every call, so nothing else can isolate it -- and
    # RECALL_FRACTION is a constant somebody should be able to revise with
    # evidence rather than argument.
    recall_chars: int = 0
    recall_injections: int = 0
    # What the injected project listing cost this role, in characters,
    # summed over every agent build (OPEN-39). Counted for the reason
    # recall_chars is, and the reason is sharper here: the listing is a
    # FIXED per-call cost that buys model calls with prompt tokens, so if
    # input per call rises by more than the call count falls the change is
    # a loss. Nothing else can isolate it -- it rides inside input_tokens
    # like every other part of the prompt, and CLAUDE.md 5a records that
    # system prompt growth is otherwise unmeasured.
    tree_chars: int = 0
    tree_injections: int = 0
    # Guarded reads this role repeated with identical arguments, and
    # nothing in between that could have changed the answer, which
    # RepeatGuardMiddleware answered instead of running (OPEN-39 Phase 2).
    # Counted for the reason tree_chars is, and it is the same question
    # asked from the other side: the listing buys model calls WITH prompt
    # tokens, and this one takes model calls back for nothing. Run
    # 83f34f50210c made 41 re-reads over 6 distinct files -- 22% of its
    # counted time -- and the only way anyone knew was a script run by hand
    # over a debug log, three separate times.
    reads_deduped: int = 0
    # Writes this role emitted whose bytes were already on disk, which
    # RepeatGuardMiddleware refused instead of performing (OPEN-60 Half A),
    # and what those payloads would have cost.
    #
    # TWO fields, and the pair is the point. `writes_skipped` mirrors
    # reads_deduped -- a call that never happened leaves no mark on tokens,
    # seconds or tool results -- but this item's claim is OUTPUT tokens, the
    # expensive kind, and a bare count of 3 cannot tell 300 characters from
    # 34,000. Measured over five runs before the rule existed: 30 of 127
    # writes were byte-identical rewrites, ~93,280 characters; run9
    # re-emitted 33% of everything it wrote.
    #
    # Characters rather than tokens for the reason recall_chars is: that is
    # what is actually counted here, and a second approximation would only
    # add error.
    writes_skipped: int = 0
    writes_skipped_chars: int = 0
    # Edits whose `old_string` carried `read_file`'s two-space gutter as
    # indentation, repaired against the file's own bytes before the tool ran
    # (OPEN-92, GutterIndentMiddleware).
    #
    # Counted because the fix is invisible in every other number: a repaired
    # edit looks exactly like an edit that was right the first time. Without
    # it, "did this item pay for itself" is answerable only by a hand-written
    # parser over a debug log -- which is the thing CLAUDE.md §8a exists to
    # stop. Before the repair, seven of run fc543fb2b82f's seven coder edits
    # failed and two invocations were killed by the repeat guard.
    edits_reindented: int = 0
    # Task lists refused by add_tasks for cutting one file into several
    # tasks (OPEN-90, loop/decomposition.py). At most one per run.
    #
    # Counted for edits_reindented's reason: what the guard prevents is a
    # coder invocation that never happens, and an invocation that never
    # happens leaves no mark on calls, seconds or tokens -- so without this
    # number "did the guard fire on this run?" is answerable only by
    # grepping a debug log, which is what §8a exists to stop. Read it beside
    # ledger.json: a run with `plans_refused: 1` and no `files_touched: []`
    # task is this item working.
    plans_refused: int = 0
    # Writes carrying a project-absolute path into source a real interpreter
    # later runs, explained in the tool's own result (OPEN-93,
    # ContentPathMiddleware).
    #
    # Counted for edits_reindented's and plans_refused's reason, and read
    # against them: 0 means the `_PATH_RULES` block kept the model off the
    # bad spelling, >=1 means the prompt missed and the middleware caught it.
    # Either is a pass; only this number says which half did the work. In run
    # `2cde3406f7d6` the literal `/src/iphone15.html` cost the entire run --
    # 2,132 s, 0 of 2 tasks -- and left no mark on calls, seconds or tokens.
    content_paths_flagged: int = 0
    # Wall clock this role spent inside model calls, in seconds (C9.6).
    # Measured by Rudra rather than reported by a provider, which makes it
    # the one number that is always there: token counts are frequently
    # absent on local backends, which is what _add's None handling exists
    # for.
    #
    # There is deliberately NO cost field, and S15.2 makes that permanent:
    # Rudra is free, the provider bill is the user's, and a bundled price
    # table would go stale silently and be wrong for OpenRouter, vLLM and
    # every proxy. A wrong number is worse than no number.
    seconds: float = 0.0


def _add(total: int | None, reported: int | None) -> int | None:
    """Sum what was reported, leaving never-reported as None."""
    if reported is None:
        return total
    return reported if total is None else total + reported


# Below this, a wall/monotonic disagreement is a clock adjustment rather
# than a suspended machine, and a line in every panel would be noise
# (OPEN-53). NTP steps in seconds; idle sleep costs minutes -- run10's
# three gaps were 900s, 900s and 1800s against a 0.4s baseline for every
# other task boundary in the run.
SUSPENDED_NOTICE_SECONDS = 60.0


@dataclass
class RunUsage:
    """Every model call this run made, grouped by role.

    It also holds the run's two start clocks, because it is already the
    one object built at run start and shared by reference -- a second
    object for two floats would be a second thing to thread through
    `create_main_agent`.

    `started_wall` and `started_mono` are both captured, and the pair is
    the whole point of OPEN-53. EVERY clock in Rudra is `monotonic` or
    `perf_counter` (trace/stream.py:109, loop/engine.py:398,
    subagents/runner.py:324, context/middleware.py:60, llm/probe.py:127),
    and on macOS both are `mach_absolute_time()`, which does not tick
    while the machine is suspended. So the instruments are honest and
    consistent, and a user comparing them against a stopwatch sees a
    quarter of the run -- run10 was 4888s of clock over 1243s of counted
    time, and three sessions read that as a missing instrument before
    `pmset -g log` put Deep Idle sleep in exactly the three gaps.
    """

    per_role: dict[str, RoleUsage] = field(default_factory=dict)
    started_wall: float = field(default_factory=time.time)
    started_mono: float = field(default_factory=time.monotonic)

    # Which roles' endpoints have actually ANSWERED a call this run
    # (OPEN-61). Here for the reason the two clocks above are here: this is
    # already the one object built at run start and shared by reference,
    # and a second object for one set would be a second thing to thread
    # through `create_main_agent`. It is deliberately NOT in `as_dict` --
    # it is run state a retry policy reads, not an accounting number
    # anything reports, and `usage.json`'s schema is read by scripts in
    # this repo's own ledger.
    #
    # A ROLE and not a model id, because a role is what every construction
    # site already has. Two roles sharing one spec each answer for
    # themselves, which errs toward the old behaviour rather than away
    # from it.
    served_roles: set[str] = field(default_factory=set)

    # What each role believes is on disk at each path, as a digest of the
    # last content it successfully wrote there (OPEN-62 6a). Here for the
    # reason `served_roles` above is here, and it is the same problem: the
    # belief has to outlive the agent, because `subagents/runner.py:263`
    # builds a fresh middleware on every dispatch and run13 spent two whole
    # coder invocations re-emitting files a previous task had written.
    #
    # Keyed by ROLE, because the coder and the tester write different files
    # for different reasons and a shared unkeyed map would let either
    # silence the other. Deliberately NOT in `as_dict`: run state a guard
    # reads, not an accounting number anything reports, and `usage.json`'s
    # schema is read by scripts in this repo's own ledger.
    write_beliefs: dict[str, dict[str, str]] = field(default_factory=dict)

    def beliefs_for(self, role: str) -> dict[str, str]:
        """`role`'s write-belief map, created on first use.

        Returned live and mutated in place by the caller, which is what
        makes one map shared across every middleware instance this run
        builds for that role.
        """
        if role not in self.write_beliefs:
            self.write_beliefs[role] = {}
        return self.write_beliefs[role]

    def record_served(self, role: str) -> None:
        """A model call by `role` returned -- the endpoint serves it."""
        self.served_roles.add(role)

    def has_served(self, role: str) -> bool:
        """Has `role`'s endpoint answered at least one call this run?"""
        return role in self.served_roles

    def _slot(self, role: str) -> RoleUsage:
        if role not in self.per_role:
            self.per_role[role] = RoleUsage()
        return self.per_role[role]

    def record(
        self,
        role: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        seconds: float = 0.0,
    ) -> None:
        """One model call by `role`, with whatever the provider reported.

        `seconds` defaults to 0.0 rather than None because, unlike the
        token counts, it is never "not reported" -- a caller that does not
        measure simply contributes nothing to the total.
        """
        slot = self._slot(role)
        slot.calls += 1
        slot.input_tokens = _add(slot.input_tokens, input_tokens)
        slot.output_tokens = _add(slot.output_tokens, output_tokens)
        slot.seconds += seconds

    def record_recall(self, role: str, chars: int) -> None:
        """One recall block injected into `role`'s prompt."""
        slot = self._slot(role)
        slot.recall_chars += int(chars)
        slot.recall_injections += 1

    def record_tree(self, role: str, chars: int) -> None:
        """One project listing injected into `role`'s prompt.

        Mirrors record_recall, injections included: the average size of one
        listing is what says whether TREE_MAX_ENTRIES is set right, and a
        total alone cannot give it.
        """
        slot = self._slot(role)
        slot.tree_chars += int(chars)
        slot.tree_injections += 1

    def record_dedupe(self, role: str) -> None:
        """One repeated guarded read answered by the guard, not the tool.

        Mirrors record_retry: both count something Rudra did on the run's
        behalf that `calls` cannot show -- and this one is invisible in
        every other number, since a call that never happens leaves no
        trace in tokens, seconds or tool results.
        """
        self._slot(role).reads_deduped += 1

    def record_write_skipped(self, role: str, chars: int) -> None:
        """One no-op rewrite the guard refused, and what it would have cost.

        Deliberately NOT folded into record_dedupe. OPEN-39 Phase 2's
        saving is re-reads avoided and this one is rewrites avoided; the
        two are different claims about different tools, and one number
        carrying both would be neither -- which is the sentence
        record_dedupe's own docstring already makes about retries.
        """
        slot = self._slot(role)
        slot.writes_skipped += 1
        slot.writes_skipped_chars += int(chars)

    def record_edit_reindented(self, role: str) -> None:
        """One `edit_file` whose gutter-indented `old_string` was repaired.

        Mirrors record_dedupe and record_write_skipped: something Rudra did
        on the run's behalf that no other number can show. A repaired edit is
        indistinguishable from a correct one in tokens, seconds and tool
        results -- and the failure it replaces cost a full model round trip
        each time, three of them before the repeat guard killed the
        invocation (OPEN-92).
        """
        self._slot(role).edits_reindented += 1

    def record_plan_refused(self, role: str) -> None:
        """One add_tasks list refused for decomposing below the file level.

        Bounded at one per run by `Ledger.decomposition_refused`, so a
        value above 1 means a second run's planner shares this store --
        which nothing does today, and would be worth knowing if it ever
        did (OPEN-90).
        """
        self._slot(role).plans_refused += 1

    def record_content_path_flagged(self, role: str) -> None:
        """One write whose content named this project with a leading "/".

        Mirrors record_edit_reindented: something Rudra explained on the
        run's behalf that no other number can show. The failure it heads off
        is silent at write time and total at run time -- the gate reports a
        test failure, and nothing in that report says the test's path is what
        is wrong (OPEN-93).
        """
        self._slot(role).content_paths_flagged += 1

    def record_compaction(self, role: str) -> None:
        """One `compact_conversation` call by `role`.

        Tool-driven only. deepagents' automatic summarization fires without
        passing through any Rudra middleware, so it is not counted here and
        this number must not be read as "every time context was shed".
        """
        self._slot(role).compactions += 1

    def record_retry(self, role: str) -> None:
        """One model call by `role` re-issued after a transient failure.

        Mirrors record_compaction, and the mirror is the point: both count
        something Rudra did on the run's behalf that `calls` alone cannot
        show. Called by ModelRetryMiddleware once per retry ACTUALLY MADE;
        the give-up is `record_exhaustion`'s, and the two are separate
        because a retry recovered and an exhaustion did not.
        """
        self._slot(role).retries += 1

    def record_exhaustion(self, role: str) -> None:
        """One model call by `role` that ran out of retries (OPEN-46).

        Mirrors record_retry, and the pair is what makes the run's failure
        rate computable at all: `p = (retries + exhaustions) / calls`,
        where before this the numerator silently dropped every attempt
        that was not re-issued.

        Called by ModelRetryMiddleware on the give-up. That used to report
        nothing, on the reasoning that ProviderUnavailable "reaches the
        user as a sentence" -- true on the planner and single-shot paths,
        and false on the subagent one, where subagents/runner.py:264
        catches it into a result string.
        """
        self._slot(role).exhaustions += 1

    def suspended_seconds(self, *, wall_now: float, mono_now: float) -> float:
        """Seconds of this run the process was not running.

        Not an estimate. The wall clock counts suspended time and the
        monotonic clock does not, so their difference is exactly it --
        which is why there is no threshold here and no platform check.

        Clamped at zero because `time.time()` is not monotonic: an NTP
        step backwards would otherwise render as negative sleep, and a
        panel that prints one is worse than a panel that prints none.

        Both "now" values are arguments rather than read here, so this is
        pure and testable without patching the clock -- the same reason
        budget.py takes a config rather than reading one.
        """
        return max(0.0, (wall_now - self.started_wall) - (mono_now - self.started_mono))

    def roles(self) -> tuple[str, ...]:
        """Roles in the order they first appeared -- the run's own order."""
        return tuple(self.per_role)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """A JSON-writable snapshot. None survives as null, never as 0."""
        return {
            role: {
                "calls": tally.calls,
                "input_tokens": tally.input_tokens,
                "output_tokens": tally.output_tokens,
                "compactions": tally.compactions,
                "retries": tally.retries,
                "exhaustions": tally.exhaustions,
                "recall_chars": tally.recall_chars,
                "recall_injections": tally.recall_injections,
                "tree_chars": tally.tree_chars,
                "tree_injections": tally.tree_injections,
                "reads_deduped": tally.reads_deduped,
                "writes_skipped": tally.writes_skipped,
                "writes_skipped_chars": tally.writes_skipped_chars,
                "edits_reindented": tally.edits_reindented,
                "plans_refused": tally.plans_refused,
                "content_paths_flagged": tally.content_paths_flagged,
                "seconds": round(tally.seconds, 3),
            }
            for role, tally in self.per_role.items()
        }

    def as_log(
        self, *, wall_now: float | None = None, mono_now: float | None = None
    ) -> dict[str, Any]:
        """The shape written to usage.json: the roles, and the run itself.

        The roles are NESTED under `roles` rather than given a `run`
        sibling, and that is deliberate. A top-level `run` beside `coder`
        and `planner` is counted as a fifth role by anything that iterates
        the file -- including the reproduction scripts in this project's
        own ledger. Nesting breaks every reader once, loudly, at the point
        the schema changed; a sibling key would go on quietly producing a
        wrong total for as long as anybody kept summing it.

        `as_dict` is unchanged and still returns the roles alone: the
        panel reads that one, the file reads this one, and neither has to
        carry the other's concern.
        """
        wall_now = time.time() if wall_now is None else wall_now
        mono_now = time.monotonic() if mono_now is None else mono_now
        return {
            "roles": self.as_dict(),
            "run": {
                "wall_seconds": round(wall_now - self.started_wall, 3),
                "counted_seconds": round(mono_now - self.started_mono, 3),
                "suspended_seconds": round(
                    self.suspended_seconds(wall_now=wall_now, mono_now=mono_now), 3
                ),
            },
        }


def _count(value: int | None) -> str:
    return "not reported" if value is None else f"{value:,}"


def render_usage(usage: Any) -> str:
    """The usage block for a completion panel, or "" when there is none.

    Returns Rich markup. Empty string rather than a "no usage" line, on the
    facts_block precedent (facts/render.py): a section with nothing in it
    is noise in a panel the user reads at a glance.
    """
    if usage is None or not usage.roles():
        return ""

    data = usage.as_dict()
    width = max(len(role) for role in usage.roles())
    lines = []
    for role in usage.roles():
        tally = data[role]
        if tally["input_tokens"] is None and tally["output_tokens"] is None:
            # Said once, not twice: "not reported in / not reported out"
            # reads as two separate absences rather than one silent provider.
            counts = "not reported"
        else:
            counts = f"{_count(tally['input_tokens'])} in / {_count(tally['output_tokens'])} out"
        line = (
            f"  {role:<{width}}  {counts}  "
            f"[dim]({tally['calls']} call{'s' if tally['calls'] != 1 else ''}"
        )
        if tally["seconds"]:
            line += f", {tally['seconds']}s"
            # OPEN-40. `calls` and `seconds` were both here and the reader
            # was left to divide them, which is the number that says which
            # role to change -- run6's coder was 55% of every call in the
            # run. Derived here and never stored: `as_dict` is unchanged,
            # because two representations of one fact drift.
            #
            # `calls` is guarded rather than assumed non-zero. `_slot`
            # creates a row for `record_recall`, `record_tree` and
            # `record_retry` without recording a call, so a role can reach
            # this line at zero and a division would raise inside the
            # end-of-run panel -- reporting a finished run as failed over
            # bookkeeping, which C7.5 exists to prevent.
            if tally["calls"]:
                line += f", {tally['seconds'] / tally['calls']:.2f}s/call"
        if tally["compactions"]:
            plural = "s" if tally["compactions"] != 1 else ""
            line += f", {tally['compactions']} compaction{plural}"
        # Guarded exactly as compactions is, so a clean run's panel is
        # byte-identical to the one before OPEN-45 (§5.2). "retries" rather
        # than "retry"+plural: the two words differ by more than an "s".
        if tally["retries"]:
            line += f", {tally['retries']} {'retry' if tally['retries'] == 1 else 'retries'}"
        # OPEN-46. Guarded the same way, so a run whose budget always held
        # prints the panel it printed before this landed. The word shares
        # no prefix with "retry" on purpose: this is the count of calls the
        # provider never served, and a reader skimming the line must not
        # take it for the count of ones it eventually did.
        if tally["exhaustions"]:
            plural = "s" if tally["exhaustions"] != 1 else ""
            line += f", {tally['exhaustions']} exhaustion{plural}"
        lines.append(line + ")[/dim]")

    # The one thing in this panel that is NOT about a model (OPEN-53).
    # Every number above counts only time the process was running, so a
    # suspended machine makes them disagree with the user's stopwatch by
    # however long it slept -- silently, and by a factor of four on run10.
    # Guarded exactly as `compactions` and `retries` are, so a run that
    # never slept prints the panel it printed before this landed.
    suspended = usage.suspended_seconds(wall_now=time.time(), mono_now=time.monotonic())
    if suspended >= SUSPENDED_NOTICE_SECONDS:
        wall = time.time() - usage.started_wall
        lines.append(
            f"  [dim]suspended {suspended:.1f}s of {wall:.1f}s wall clock "
            f"(machine asleep; the seconds above exclude it)[/dim]"
        )

    return "Tokens:\n" + "\n".join(lines)
