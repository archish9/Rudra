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
# The 5 remaining skills stay off. The rewrite cost that originally deferred
# 4 of them is gone (spec section 1) and their prerequisites shipped in
# Steps 8 and 9b, but selection quality across a longer index at 32B is what
# C5.8 exists to measure, so D11 stands until 11c produces evidence.
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
