"""Memories as a prompt section.

Pure: takes hits and a budget, returns a string, touches nothing. The
shape mirrors facts/render.py because the two blocks sit next to each
other in the same prompt and should read as one voice.

Nothing here escapes Rich markup. The block goes into a system prompt,
where brackets are ordinary characters; escaping belongs where a tool
result is printed to a console (A1.67).

Why a character budget rather than a real tokenizer: this runs on every
agent build, a tokenizer would need the model, and the four-chars-per-
token approximation errs toward a smaller block. Erring small is the
safe direction -- an over-large recall block crowds out the task itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

CHARS_PER_TOKEN = 4

_HEADING = "## WHAT THIS PROJECT HAS LEARNED"
_PREAMBLE = (
    "Recorded during earlier runs on this project, most relevant first. "
    "Each line reads: [room] content (recorded by). `rudra` means Rudra "
    "recorded it deterministically from a finished task; `agent` means a "
    "model judged it worth keeping, so weigh it accordingly."
)


def recall_block(hits: Sequence[Any], token_budget: int | None) -> str:
    """The recalled memories as a markdown block, or "" when there are none.

    `token_budget` of None means the role declared no context window, and
    nothing is injected -- S12.9's rule, because guessing a budget for an
    unknown window is how this crowds out the task.
    """
    if token_budget is None or not hits:
        return ""

    header = f"{_HEADING}\n\n{_PREAMBLE}\n\n"
    remaining = token_budget * CHARS_PER_TOKEN - len(header)

    lines: list[str] = []
    for hit in hits:
        line = f"- [{hit.room}] {hit.content}  ({hit.added_by})"
        # The first entry goes in regardless. A heading with nothing under
        # it reads as "this project has no history", which is a different
        # and worse claim than "here is one thing".
        if lines and len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line) + 1

    return header + "\n".join(lines) + "\n"


__all__ = ["CHARS_PER_TOKEN", "recall_block"]
