"""The built-in stack profiles.

Adding a language is a data change here, not a design change. Deliberately
absent: Go, Java, Ruby, C# -- not requested (TODO.md D18).
"""

from __future__ import annotations

from rudra.stacks.profile import StackProfile

PYTHON = StackProfile(
    name="python",
    markers=("pyproject.toml", "setup.py", "requirements.txt"),
    test_command=("pytest",),
    skip_dirs=frozenset(
        {
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".venv",
            "venv",
            ".tox",
            "build",
            "dist",
        }
    ),
    specificity=10,
)

RUST = StackProfile(
    name="rust",
    markers=("Cargo.toml",),
    test_command=("cargo", "test"),
    skip_dirs=frozenset({"target"}),
    specificity=10,
)

NODE = StackProfile(
    name="node",
    markers=("package.json",),
    test_command=None,  # read from scripts.test
    skip_dirs=frozenset({"node_modules", "dist", "build", "coverage"}),
    specificity=10,
)

REACT = StackProfile(
    name="react",
    markers=("package.json",),
    dependency="react",
    test_command=None,
    skip_dirs=frozenset({"node_modules", "dist", "build", ".next", "out", "coverage"}),
    specificity=20,
)

ANGULAR = StackProfile(
    name="angular",
    markers=("angular.json",),
    requires=("package.json",),
    test_command=None,
    skip_dirs=frozenset({"node_modules", "dist", ".angular", "coverage"}),
    specificity=30,
)

PROFILES: tuple[StackProfile, ...] = (PYTHON, RUST, NODE, REACT, ANGULAR)

ALL_SKIP_DIRS: frozenset[str] = frozenset().union(*(p.skip_dirs for p in PROFILES))
