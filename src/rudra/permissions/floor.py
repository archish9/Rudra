"""The deny floor: three named rules that apply in every permission mode.

On by default, including under `--yolo`, and disabled per rule via
`[permissions] floor_disable`. This module does not know about that setting
-- `floor_hit` reports a violation and `rules.PermissionEngine` decides what
to do with it, so a disabled rule can still be audited as
`source: "floor-disabled"` rather than vanishing.

Not a sandbox, and does not pretend to be one. What the floor buys is that
the obvious catastrophes cannot happen by accident, which is the realistic
failure mode for a 32B model (D6).

**The path rules cover write_file / edit_file / delete, NOT execute.** A
shell command can write anywhere the user can, and the Step 7 acceptance
run measured a model doing exactly that: denied twice on write_file, it ran
`echo "hello" > /abs/path` and succeeded. That is TODO.md A1.49. It is not
fixable here — redirections, tee, cp, mv, `python -c`, and any script the
agent writes then runs are all equivalent, which is why real isolation is
an OS-level concern rather than a regex. `ask` mode is unaffected: the user
sees the command before it runs. The exposure is `--auto` plus shell, and
every execute is recorded in the audit log even there.
"""

from __future__ import annotations

import re
from pathlib import Path

from rudra.state.paths import is_rudra_state

FLOOR_RULE_NAMES = ("outside-root", "git-dir", "rudra-state", "catastrophic-command")

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
        # OPEN-117. `.rudra/config.toml` decides the NEXT run's permissions
        # and may hold an api_key, so under --auto an agent that can write it
        # can grant itself anything. Nothing Rudra writes there goes through
        # a file tool -- facts, the ledger, AGENTS.md and the logs are all
        # Python -- so this refuses no legitimate write. Any depth, like
        # `.git`, and case-folded: `.RUDRA` is the same directory on the
        # default macOS and Windows filesystems.
        if is_rudra_state(Path(path).relative_to(root)):
            return "rudra-state"

    if tool == "execute" and command is not None:
        if any(pattern.search(command) for pattern in _CATASTROPHIC):
            return "catastrophic-command"

    return None


__all__ = ["FLOOR_RULE_NAMES", "floor_hit"]
