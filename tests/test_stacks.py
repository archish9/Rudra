"""Tests for rudra.stacks — deterministic target-stack detection (C11.1).

Rudra's own toolchain is always Python. This module is about the *user's*
project, which may be Python, Rust, Node, React or Angular (TODO.md D18).

Detection reads marker files off disk. It never infers a stack from prose --
that is the model's job for greenfield work, per TODO.md §0.5 -- and it never
executes a subprocess; running the commands it reports belongs to C3.6.
"""

from __future__ import annotations

import dataclasses

import pytest

from rudra.stacks import ALL_SKIP_DIRS, PROFILES


def test_profile_is_immutable():
    """Profiles are shared module-level constants; mutation would leak globally."""
    profile = PROFILES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "mutated"


def test_every_target_stack_has_a_profile():
    assert {p.name for p in PROFILES} == {"python", "rust", "node", "react", "angular"}


def test_profile_names_are_unique():
    names = [p.name for p in PROFILES]
    assert len(names) == len(set(names))


def test_node_stacks_defer_their_test_command_to_the_project():
    """package.json's scripts.test is the project's own declared answer.

    Guessing between jest and vitest from devDependencies would be inference
    where an observation is available.
    """
    for name in ("node", "react", "angular"):
        profile = next(p for p in PROFILES if p.name == name)
        assert profile.test_command is None, f"{name} must read scripts.test, not hardcode"


def test_non_node_stacks_declare_their_test_command():
    commands = {p.name: p.test_command for p in PROFILES if p.test_command is not None}
    assert commands == {"python": ("pytest",), "rust": ("cargo", "test")}


def test_more_specific_stacks_outrank_generic_node():
    node = next(p for p in PROFILES if p.name == "node")
    for name in ("react", "angular"):
        specific = next(p for p in PROFILES if p.name == name)
        assert specific.specificity > node.specificity


def test_all_skip_dirs_covers_every_targets_build_output():
    for expected in (
        "target",
        ".next",
        "out",
        "dist",
        "build",
        ".angular",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    ):
        assert expected in ALL_SKIP_DIRS


def test_all_skip_dirs_is_the_union_of_the_profiles():
    union = frozenset().union(*(p.skip_dirs for p in PROFILES))
    assert ALL_SKIP_DIRS == union
