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


# --- OPEN-49: the writers have a verb for "delete" -------------------------


def test_the_coder_can_delete():
    """Run8's t4 was "remove the conflicting test file"; the coder had no
    `delete` and no `execute` to `rm` with, said so in its own prose, and
    the task was `dropped` while t5 `blocked` at 790.9s behind it.

    Absence is the enforcement in this registry (spec.py:41-43), so the
    absence has to be deliberate -- and for the one agent whose job is
    changing files on disk it was not. `delete` is a fully gated tool
    already (rules.py:78, floor.py:34, interrupts.py:43): granting it adds
    a verb, not a mechanism.
    """
    assert "delete" in REGISTRY["coder"].fs_tools


def test_the_tester_inherits_delete_from_the_writer_set():
    """`_TESTER_FS` is `_WRITER_FS` plus a shell, so this follows from the
    line above rather than being decided separately -- and it grants the
    tester nothing new: it holds `execute` and could always `rm`.
    """
    assert "delete" in REGISTRY["tester"].fs_tools


def test_only_a_spec_with_delete_gets_the_delete_contract():
    """The parity shape OPEN-36 established, applied to a third block.

    `_PATH_RULES` is every writer's contract, `_COMMAND_RULES` belongs to
    an agent holding a shell, and `_DELETE_RULES` belongs to one holding
    `delete`. Assembled from the spec's own `fs_tools` so a prompt cannot
    describe a tool the spec withholds.
    """
    from rudra.subagents.registry import _DELETE_RULES, REGISTRY

    for name, spec in REGISTRY.items():
        assert (_DELETE_RULES in spec.system_prompt) == ("delete" in spec.fs_tools), name


def test_the_delete_contract_overrides_upstreams_absolute_path_claim():
    """deepagents' DELETE_TOOL_DESCRIPTION (filesystem.py:1258-1265) opens
    with "the given absolute path", so the contract has to name it rather
    than assume the block above is read as covering a tool that says
    otherwise.

    This docstring used to add that every other granted tool's description
    was silent on the question. It is not -- all five file tools' field
    schemas say `Must be absolute, not relative.` (filesystem.py:1092-1155)
    -- and OPEN-81 is what that gap cost. `_PATH_RULES` answers them there;
    this stays because `delete`'s PROSE says it too, and prose is what a
    model reading the tool list meets first.
    """
    from rudra.subagents.registry import _DELETE_RULES

    lowered = _DELETE_RULES.lower()
    assert "relative" in lowered
    assert "absolute" in lowered


def test_the_delete_contract_states_that_a_directory_delete_is_recursive():
    """The one fact that makes `delete` unlike `write_file`.

    OPEN-49's own argument for granting it was that `write_file` already
    overwrites a sibling task's work (0.7.4, backends/filesystem.py:489).
    That holds for a file and not for a tree: upstream's description
    actively *recommends* deleting a directory in one call, and a
    task-scoped coder that takes the advice removes work no task asked it
    to touch.
    """
    from rudra.subagents.registry import _DELETE_RULES

    lowered = _DELETE_RULES.lower()
    assert "recursive" in lowered or "everything inside" in lowered


# --- OPEN-81: where the project is -----------------------------------------


def test_the_path_contract_states_where_the_project_is():
    """OPEN-81. Nothing in any writer's prompt named the project, in any
    spelling, and run `689f0ea263be`'s coder answered the question itself:
    13 of its 13 `read_file` errors were paths beginning
    `/home/user/Rudra/`, a Linux container home and a project name it was
    never given.

    The anchor is a placeholder here because a spec is built at import and
    the root is known per run; `build.py::_prompt_for` renders it, and
    tests/test_subagents_build.py holds that half.
    """
    from rudra.subagents.registry import _PATH_RULES, PROJECT_PATH_TOKEN

    assert PROJECT_PATH_TOKEN in _PATH_RULES


def test_the_path_contract_names_the_roots_a_model_invents():
    """`_COMMAND_RULES` already names them for `execute` -- "/app,
    /workspace, /testbed, /root and /mnt/<id> do not exist here and never
    will" -- and in run `689f0ea263be` the coder produced ZERO bad shell
    paths and 13 bad file paths. The half that had the sentence did not
    fail; this is that sentence for the file tools.

    `/home/user` is included because it is the one the run actually
    produced, and it is the one `_COMMAND_RULES` does not list.
    """
    from rudra.subagents.registry import _PATH_RULES

    lowered = _PATH_RULES.lower()
    for invented in ("/home/user", "/workspace", "/testbed", "/app", "/root"):
        assert invented in lowered, invented


def test_the_path_contract_answers_upstreams_must_be_absolute_claim():
    """The `_DELETE_RULES` precedent (registry.py:60-63), applied to the
    tool descriptions that actually caused this.

    Every file tool deepagents builds carries `file_path: str = Field(
    description="Absolute path ... Must be absolute, not relative.")` --
    `ls`, `read_file`, `write_file`, `edit_file` and `delete`, at
    filesystem.py:1092-1155. A prompt cannot outrank a prompt (OPEN-17),
    so the contract names the other text and says which holds rather than
    leaving "NEVER use absolute paths" to win an argument it lost 13 times.
    """
    from rudra.subagents.registry import _PATH_RULES

    lowered = _PATH_RULES.lower()
    assert "must be absolute" in lowered


def test_the_path_contract_legitimises_the_spelling_ls_answers_in():
    """The prompt used to contradict the tool output the model reads three
    lines later: `ls` and `glob` return `/src/config/settings.py`, and
    those calls WORK -- `virtual_mode=True` makes them correct. This run's
    tester copied two straight out of a listing, in the spelling the
    prompt forbade.
    """
    from rudra.subagents.registry import _PATH_RULES

    assert "ls" in _PATH_RULES
    assert "/src/" in _PATH_RULES


def test_the_path_contract_scopes_the_virtual_spelling_to_tool_arguments():
    """OPEN-93. `_PATH_RULES` called `/src/x` "correct" without qualification,
    and it is -- as a TOOL ARGUMENT. Run `2cde3406f7d6`'s tester read that,
    wrote `HTML_PATH = "/src/iphone15.html"` into a test file, and the gate
    ran it with the project's own python3, to which `/src` is the machine's
    root. 8 of 8 tests failed forever against 480 lines of correct HTML.
    """
    from rudra.subagents.registry import _PATH_RULES

    assert "PATHS INSIDE THE FILES YOU WRITE" in _PATH_RULES
    assert "TOOL ARGUMENTS" in _PATH_RULES
    assert '"src/index.html"' in _PATH_RULES
    assert '"/src/index.html"' in _PATH_RULES


@pytest.mark.parametrize("name", ["coder", "tester"])
def test_every_writer_is_told_about_paths_inside_written_files(name):
    """It lives in `_PATH_RULES` and not `_COMMAND_RULES` on purpose: the
    coder has no `execute` (OPEN-36) and still writes conftest.py, Makefiles
    and shell scripts, every one of which a real process later runs."""
    from rudra.subagents.registry import _rules_for

    rules = _rules_for(REGISTRY[name].fs_tools)
    assert "PATHS INSIDE THE FILES YOU WRITE" in rules


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_a_non_writer_gets_the_block_too_and_it_costs_nothing(name):
    """The block rides `_PATH_RULES`, which every spec gets. Pinned so the
    parity is a decision on record rather than an accident: a reader who
    wants it scoped to writers has to change this test to do it."""
    from rudra.subagents.registry import _rules_for

    rules = _rules_for(REGISTRY[name].fs_tools)
    assert "PATHS INSIDE THE FILES YOU WRITE" in rules


def test_the_finish_rules_cover_overwriting_a_file_you_produced():
    """OPEN-97 Option C. `_FINISH_RULES`' existing sentence reads as a
    prohibition on CREATING a `DONE`-shaped file, and run `2cde3406f7d6`'s
    coder was not creating one -- it was updating a file it owned, with a
    summary of what it contained, over 24,800 bytes of finished HTML.

    Prose is the belt, never the fix: `FixWriteParamsMiddleware` refuses the
    call, and this text was already in the prompt during that run
    (OPEN-97 §5 Option C, TODO.md lesson one)."""
    from rudra.subagents.registry import _FINISH_RULES

    # Line-wrapped in the source, so normalise before matching.
    flat = " ".join(_FINISH_RULES.split())
    assert "never write your summary into a file you produced" in flat
    assert "a file you wrote yourself" in flat
    # Still a category and never a filename -- the pin above still holds.
    for enumerated in ("DONE", ".txt", ".md"):
        assert enumerated not in _FINISH_RULES, enumerated
