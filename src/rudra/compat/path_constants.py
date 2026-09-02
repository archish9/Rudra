"""Shared path constants for sandbox prefix stripping.

LLMs trained on coding datasets (SWE-bench, Codespaces, Docker containers)
frequently hallucinate these prefixes. They have no meaning on the user's
actual machine, and stripping them is OPT-IN -- ``[compat] sandbox_paths``,
off by default -- because every entry below is also an ordinary directory a
real project has. See the SANDBOX PREFIXES ARE OPT-IN section of
``deepagents_path.py`` for what leaving it on cost (OPEN-31).

Order matters: longest-match-first to avoid "/home/user/" eating "/home/user/repos/".

This module used to carry a second constant, ``BOGUS_DIRS`` -- a frozenset
of container-owned directory names (``home``, ``var``, ``workspace``, ...)
used by one caller, ``deepagents_path.py``'s Step 2d, to decide whether an
unmatched absolute path was "shaped like" a hallucination and could be
trimmed to its last two components. Step 2d was deleted by OPEN-82 and the
set went with it. **Do not reintroduce it.** Its whole premise was that the
shape of a path is evidence about the caller's intent, and the measured
answer is that it is not: a hallucinating model produces container-shaped
paths, so the set selected precisely the calls that would be silently
rewritten. The reasoning is preserved at the Step 2d comment.
"""

SANDBOX_PREFIXES: tuple[str, ...] = (
    "/testbed/",  # SWE-bench docker containers
    "/workspace/",  # GitHub Codespaces, Gitpod, Docker
    "/home/user/repos/",  # Generic Linux training data
    "/home/user/",  # Generic Linux home
    "/root/",  # Docker root user
    "/app/",  # Docker app pattern
    "/code/",  # Generic code mount
    "/src/",  # Some containers mount source here
    "/tmp/",  # Temporary directory
)
