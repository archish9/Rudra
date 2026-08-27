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
    # Memory only. It writes source through its filesystem tools and
    # records what it learned through .rudra/ -- neither is a shell.
    assert spec.rudra_tools == ("remember", "search_memory")


def test_the_reviewer_reads_the_diff():
    assert REGISTRY["reviewer"].rudra_tools == ("git_diff",)


def test_the_tester_gets_both_run_tests_and_execute():
    # Both deliberately: run_tests caps output for a 32B window, execute
    # covers what it cannot express. Spec §3.
    spec = REGISTRY["tester"]
    assert spec.rudra_tools == ("run_tests", "remember", "search_memory")
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


def test_the_agents_that_can_run_commands_name_the_real_tool():
    """A1.92: three separate runs saw a subagent call a `bash` tool that
    does not exist, once escalating to 204 filesystem globs hunting for an
    interpreter. The tool is `execute`, and nothing said so.

    Derived from the specs rather than listed by hand, because the hand-written
    list drifted once already: it named the coder, which has never had
    `execute` at all (OPEN-36).
    """
    from rudra.subagents.registry import REGISTRY

    with_shell = [name for name, spec in REGISTRY.items() if "execute" in spec.fs_tools]
    assert with_shell, "vacuous if no shipped spec can run anything"
    for name in with_shell:
        prompt = REGISTRY[name].system_prompt
        assert "execute" in prompt
        assert "bash" in prompt, "the wrong name has to be named to be ruled out"


def test_the_read_only_agent_is_told_what_to_do_when_asked_to_run_something():
    """It has no shell tool at all, and the coder delegates "run the tests"
    to it anyway. Without this it searches for a Python interpreter."""
    prompt = REGISTRY["general-purpose"].system_prompt

    assert "cannot" in prompt.lower()
    assert "glob" in prompt, "the specific wrong move is worth naming"


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_a_prompt_names_execute_only_if_its_spec_grants_it(name):
    """OPEN-36. The coder's prompt opened with `## RUNNING COMMANDS` and
    carried `## WHERE COMMANDS RUN`, twenty lines of operating instructions
    for a tool `fs_tools` does not grant -- and run6 measured 15 `execute`
    calls from the coder, every one of them `execute is not a valid tool`.
    Three in a row trips MAX_CONSECUTIVE_FAILURES (runner.py:32).

    One sentence saying "you may not have it" lost to twenty explaining how
    to use it, which is OPEN-17's lesson again: a prompt cannot outrank a
    prompt. So the parity is exact in BOTH directions -- an agent that has
    the tool must be told the tool's name (A1.92), and one that does not
    must never see the word.
    """
    spec = REGISTRY[name]
    mentions = "execute" in spec.system_prompt
    grants = "execute" in spec.fs_tools
    assert mentions == grants, (
        f"{name}: prompt mentions execute={mentions}, spec grants it={grants}"
    )


def test_only_a_spec_with_execute_gets_the_command_contract():
    """The other half of the same split. `_PATH_RULES` is the contract for
    every writer; `_COMMAND_RULES` is the half that only means anything to
    an agent holding a shell, so it is assembled from the spec rather than
    interpolated unconditionally."""
    from rudra.subagents.registry import _COMMAND_RULES, REGISTRY

    assert _COMMAND_RULES in REGISTRY["tester"].system_prompt
    for name in ("coder", "reviewer", "general-purpose"):
        assert _COMMAND_RULES not in REGISTRY[name].system_prompt, name


def test_the_coder_is_told_it_cannot_run_commands():
    """Removing the instructions is not enough on its own -- A1.92 watched
    agents with no shell hunt the filesystem for an interpreter. The
    reviewer and general-purpose prompts already answer this by stating the
    absence and naming no tool; the coder now does too."""
    prompt = REGISTRY["coder"].system_prompt

    assert "YOU CANNOT RUN COMMANDS" in prompt
    assert "no shell tool" in prompt


# --- OPEN-42: the writers are told how to end a turn ----------------------


def test_only_a_writer_gets_the_completion_contract():
    """Parity in both directions, the shape OPEN-36's test established.

    A writer is the only agent that can turn "I am finished" into a file in
    the user's project, which is what run7 measured: eleven marker writes
    across three coder invocations (OPEN-42). The reviewer and
    general-purpose cannot write at all, so they pay nothing for a contract
    that could only rule out a move they cannot make.
    """
    from rudra.subagents.registry import _FINISH_RULES, REGISTRY

    for name, spec in REGISTRY.items():
        assert (_FINISH_RULES in spec.system_prompt) == spec.can_write, name


def test_the_completion_contract_states_the_signal():
    """The positive half, and it is the point of the item.

    Run7's coder had no stop verb at all -- nothing in its prompt said how a
    turn ends -- so it reached for `task_complete` from its training prior,
    and when that errored it used the one tool it had. Telling it the signal
    fills a gap; it does not argue with a competing instruction, which is
    why this is not the OPEN-17 shape.
    """
    from rudra.subagents.registry import _FINISH_RULES

    assert "call no tool" in _FINISH_RULES.lower()


def test_the_completion_contract_names_no_marker_filename():
    """The negative half is a CATEGORY, never a list of filenames.

    A rule about `DONE` would not have covered `task_complete.txt`, and
    neither would cover `COMPLETE.md` next run -- the model invents the name.
    Rejected explicitly in OPEN-42 §10.
    """
    from rudra.subagents.registry import _FINISH_RULES

    assert "never write a file" in _FINISH_RULES.lower()
    for enumerated in ("DONE", ".txt", ".md"):
        assert enumerated not in _FINISH_RULES, enumerated


@pytest.mark.parametrize("name", ["coder", "tester"])
def test_a_writer_has_exactly_one_section_about_stopping(name):
    """A second stop section is the OPEN-17 shape: a prompt outranking a
    prompt. The vague `## STOP CONDITION` that said "Stop when the task is
    complete" without saying HOW is what `_FINISH_RULES` replaces, not
    something it sits beside."""
    prompt = REGISTRY[name].system_prompt

    assert "## STOP CONDITION" not in prompt
    assert prompt.count("## HOW TO FINISH") == 1
