"""What the four subagents are allowed to be."""

from __future__ import annotations

import pytest

from rudra.config.schema import BUILTIN_ROLES
from rudra.subagents.registry import REGISTRY

WRITE_TOOLS = {"write_file", "edit_file", "delete"}


def test_the_four_expected_entries_exist():
    assert set(REGISTRY) == {"coder", "tester", "reviewer", "general-purpose"}


def test_general_purpose_is_named_exactly_as_upstream_expects():
    # graph.py:751 suppresses the auto-added ungated subagent only on an
    # exact name match. A typo here silently reopens the hole.
    assert "general-purpose" in REGISTRY


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_key_matches_its_spec_name(name):
    assert REGISTRY[name].name == name


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_role_is_a_builtin_role(name):
    assert REGISTRY[name].role in BUILTIN_ROLES


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_read_only_agents_have_no_write_tool(name):
    spec = REGISTRY[name]
    assert not WRITE_TOOLS & set(spec.fs_tools)
    assert "execute" not in spec.fs_tools
    assert spec.can_write is False


def test_the_tester_is_the_only_entry_with_execute():
    with_execute = [name for name, spec in REGISTRY.items() if "execute" in spec.fs_tools]
    assert with_execute == ["tester"]


def test_the_coder_writes_but_does_not_execute():
    spec = REGISTRY["coder"]
    assert spec.can_write is True
    assert "execute" not in spec.fs_tools
    assert spec.rudra_tools == ()


def test_the_reviewer_reads_the_diff():
    assert REGISTRY["reviewer"].rudra_tools == ("git_diff",)


def test_the_tester_gets_both_run_tests_and_execute():
    # Both deliberately: run_tests caps output for a 32B window, execute
    # covers what it cannot express. Spec §3.
    spec = REGISTRY["tester"]
    assert spec.rudra_tools == ("run_tests",)
    assert "execute" in spec.fs_tools


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_prompt_states_a_stop_condition(name):
    # Only the final assistant message reaches a caller (subagents.py:117),
    # so a prompt with no stop condition produces a subagent that trails off.
    prompt = REGISTRY[name].system_prompt.lower()
    assert "stop" in prompt or "finish" in prompt


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_spec_can_carry_middleware(name):
    # RudraSubagent has no middleware field on purpose: build.py owns
    # assembly so nothing can be declared without the gate (S9b.2).
    assert not hasattr(REGISTRY[name], "middleware")
