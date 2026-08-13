"""Facts as a prompt section.

Pure: takes a store, returns a string, touches nothing. Rendered at build
time rather than baked into a spec because build_agent runs per
invocation (subagents/runner.py:124), so a fact recorded during one task
reaches the next dispatch without any plumbing.

Nothing here escapes Rich markup. The block goes into a system prompt,
where brackets are ordinary characters; the console printing of any tool
result is where escaping belongs (A1.67).
"""

from __future__ import annotations

from rudra.facts.store import FactStore

_HEADING = "## PROJECT FACTS"
_PREAMBLE = (
    "Established for this project. Treat these as binding unless the user "
    "says otherwise. Each line reads: key = value (source: why)."
)


def facts_block(store: FactStore | None) -> str:
    """The facts as a markdown block, or "" when there are none."""
    if store is None or not store.facts:
        return ""
    lines = [_HEADING, "", _PREAMBLE, ""]
    lines.extend(
        f"- {key} = {fact.value}  ({fact.source}: {fact.why})" for key, fact in store.items()
    )
    return "\n".join(lines) + "\n"


__all__ = ["facts_block"]
