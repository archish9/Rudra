"""The deny floor: three named rules that apply in every permission mode.

On by default, including under `--yolo`, and disabled per rule via
`[permissions] floor_disable`. This module does not know about that setting
-- `floor_hit` reports a violation and `rules.PermissionEngine` decides what
to do with it, so a disabled rule can still be audited as
`source: "floor-disabled"` rather than vanishing.

Not a sandbox, and does not pretend to be one: a model can still write a
destructive script and run it. What the floor buys is that the obvious
catastrophes cannot happen by accident, which is the realistic failure mode
for a 32B model (D6).
"""

from __future__ import annotations

import re
from pathlib import Path

FLOOR_RULE_NAMES = ("outside-root", "git-dir", "catastrophic-command")

# Tools whose effect is destructive. Reads are never a floor violation --
# the floor governs destruction, and gating reads would fire on the dozens
# of read_file calls a normal run makes (spec §4.3).
_DESTRUCTIVE_PATH_TOOLS = frozenset({"write_file", "edit_file", "delete"})

# Anchored at the start of the command, tolerating a leading `sudo` and
# arbitrary whitespace. Deliberately narrow: these are the unrecoverable
# ones, not everything dangerous.
_CATASTROPHIC = (
    re.compile(r"^\s*(?:sudo\s+)?rm\s+(?:-\S+\s+)*/\s*\*?\s*$"),
    re.compile(r"^\s*(?:sudo\s+)?mkfs(\.\w+)?\b"),
    re.compile(r"^\s*(?:sudo\s+)?dd\b.*\bof=/dev/"),
)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def floor_hit(
    tool: str,
    path: Path | None,
    command: str | None,
    project_root: Path,
) -> str | None:
    """Name the floor rule this call violates, or None.

    `path` must already be resolved by the caller. Resolution before
    matching is what makes `../../etc/hosts` and a symlink into /etc both
    land on their real location instead of being matched as strings the
    model chose (spec §4.4).
    """
    if tool in _DESTRUCTIVE_PATH_TOOLS and path is not None:
        root = Path(project_root).resolve()
        if not _is_inside(Path(path), root):
            return "outside-root"
        if ".git" in Path(path).parts:
            return "git-dir"

    if tool == "execute" and command is not None:
        if any(pattern.search(command) for pattern in _CATASTROPHIC):
            return "catastrophic-command"

    return None


__all__ = ["FLOOR_RULE_NAMES", "floor_hit"]
