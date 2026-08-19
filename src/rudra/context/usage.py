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

    def record(self, role: str, *, input_tokens: int | None, output_tokens: int | None) -> None:
        """One model call by `role`, with whatever the provider reported."""
        slot = self._slot(role)
        slot.calls += 1
        slot.input_tokens = _add(slot.input_tokens, input_tokens)
        slot.output_tokens = _add(slot.output_tokens, output_tokens)

    def record_compaction(self, role: str) -> None:
        """One `compact_conversation` call by `role`.

        Tool-driven only. deepagents' automatic summarization fires without
        passing through any Rudra middleware, so it is not counted here and
        this number must not be read as "every time context was shed".
        """
        self._slot(role).compactions += 1

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
        if tally["compactions"]:
            plural = "s" if tally["compactions"] != 1 else ""
            line += f", {tally['compactions']} compaction{plural}"
        lines.append(line + ")[/dim]")

    return "Tokens:\n" + "\n".join(lines)
