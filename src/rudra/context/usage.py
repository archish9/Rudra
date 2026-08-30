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


@dataclass
class RunUsage:
    """Every model call this run made, grouped by role."""

    per_role: dict[str, RoleUsage] = field(default_factory=dict)

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
        show. Called by ModelRetryMiddleware once per retry ACTUALLY MADE
        -- never on the give-up, which raises ProviderUnavailable and is
        already reported as a sentence.
        """
        self._slot(role).retries += 1

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
                "recall_chars": tally.recall_chars,
                "recall_injections": tally.recall_injections,
                "tree_chars": tally.tree_chars,
                "tree_injections": tally.tree_injections,
                "seconds": round(tally.seconds, 3),
            }
            for role, tally in self.per_role.items()
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
        lines.append(line + ")[/dim]")

    return "Tokens:\n" + "\n".join(lines)
