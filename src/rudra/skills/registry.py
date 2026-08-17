"""The one list of vendored skill bundles that ship with Rudra.

Adding an upstream corpus is a data change here, not a design change --
the same shape stacks/registry.py already uses for stack profiles.
"""

from __future__ import annotations

from pathlib import Path

from rudra.skills.bundle import Bundle, load_bundle

_BUNDLES_DIR = Path(__file__).parent / "bundles"

BUNDLES: tuple[Bundle, ...] = (load_bundle(_BUNDLES_DIR / "superpowers"),)

# The 8 skills D11 enables by default, plus the bootstrap.
#
# `using-superpowers` is not one of the 8 and is not optional: it is the
# instruction block that makes an agent reach for skills at all (C5.3).
# Listing it here rather than special-casing it in the transform keeps
# render() dumb -- `enabled` is exactly the set copied into active/.
#
# The 5 remaining skills stay off, and as of Step 11c that is a *measured*
# decision rather than D11's 2026-08-05 estimate (C5.8 / C5.9).
#
# Eight runs, four tasks, two arms -- this set versus this set plus four --
# against a 550B model. The nine-skill arm produced usable output on 4/4 and
# never tripped a loop guard. The thirteen-skill arm managed 2/4 and failed
# three different ways: it read all fourteen skills for a roman-numeral
# function, declared no tasks at all on an open-ended request, and on the
# fourth task span until the planner loop guard fired six times, ending by
# regressing a test its own earlier run had fixed.
#
# Two honest limits on that. One observation per cell, so it is directional,
# not statistical -- what lifts it above a single anomaly is that the failures
# recurred in different forms on different tasks. And every run was 550B,
# while D6 sets the supported floor at 32B, so the floor question C5.8 was
# written to answer is still open.
#
# All fourteen stay vendored either way. Enabling one is a `[skills] enabled`
# edit, not a rebuild.
DEFAULT_ENABLED: frozenset[str] = frozenset(
    {
        "brainstorming",
        "writing-plans",
        "executing-plans",
        "systematic-debugging",
        "test-driven-development",
        "verification-before-completion",
        "requesting-code-review",
        "receiving-code-review",
        "using-superpowers",
    }
)

__all__ = ["BUNDLES", "DEFAULT_ENABLED", "Bundle"]
