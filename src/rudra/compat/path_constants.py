"""Shared path constants for sandbox prefix stripping.

LLMs trained on coding datasets (SWE-bench, Codespaces, Docker containers)
frequently hallucinate these prefixes. They have no meaning on the user's
actual machine and must be stripped before deepagents sees the path.

Order matters: longest-match-first to avoid "/home/user/" eating "/home/user/repos/".
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

BOGUS_DIRS: frozenset[str] = frozenset(
    {
        "home",
        "user",
        "users",
        "root",
        "tmp",
        "var",
        "opt",
        "repos",
        "projects",
        "workspace",
        "testbed",
        "code",
    }
)
