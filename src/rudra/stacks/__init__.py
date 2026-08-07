"""Target-stack detection for the user's project (TODO.md D18, C11.1).

Rudra's own toolchain is always Python. This package is about whatever the
user's project is written in.
"""

from __future__ import annotations

from rudra.stacks.profile import StackProfile
from rudra.stacks.registry import ALL_SKIP_DIRS, PROFILES

__all__ = ["ALL_SKIP_DIRS", "PROFILES", "StackProfile"]
