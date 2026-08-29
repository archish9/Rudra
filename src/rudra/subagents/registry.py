"""The four subagents Rudra ships.

Adding one is a data change here, the same rule stacks/registry.py:3-4
states for stack profiles.

Prompts live beside their specs rather than in the modules that use them,
because a prompt and its tool list are one decision: a prompt naming a
tool the spec does not grant produces a subagent that keeps trying
something it cannot do.
"""

from __future__ import annotations

from rudra.subagents.spec import RudraSubagent

_READ_ONLY_FS: tuple[str, ...] = ("ls", "read_file", "glob", "grep")
# `delete` since OPEN-49. Run8's t4 was "remove the conflicting test file";
# the coder had no way to remove anything -- no `delete`, and no `execute` to
# `rm` with -- so it said so in its own prose, t4 went `dropped`, t5 `blocked`
# behind it at 790.9s, and the file shipped to the user. The tool was already
# gated end to end (rules.py:78, floor.py:34, interrupts.py:43, diff.py:139);
# it was simply never granted to the one agent whose job is changing files.
_WRITER_FS: tuple[str, ...] = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "glob",
    "grep",
)
# The tester inherits `delete` from the line above, and gains nothing by it:
# it holds `execute` and could always `rm`.
_TESTER_FS: tuple[str, ...] = (*_WRITER_FS, "execute")

# The path contract, shared by every subagent that can write.
#
# It lived only in _CODER_PROMPT until 2026-08-25, and the agent that walked
# out of the project was the TESTER, which had no path rules whatsoever
# (OPEN-21/OPEN-22). One copy, interpolated into both, so they cannot drift.
#
# The shell half moved to _COMMAND_RULES on 2026-08-27 (OPEN-36). What is
# left here is true for a writer whether or not it can run anything.
_PATH_RULES = """## FILE PATH RULES
- Use RELATIVE paths only: "src/main.rs", "package.json"
- NEVER use absolute paths or paths starting with "/" or a drive letter
- **Directories are created implicitly.** Writing "tests/test_models.py"
  creates "tests/" on the way. There is no mkdir tool and you do not need
  one. NEVER write a placeholder file in order to bring a directory into
  being -- that gives you a FILE with that name, and every later write
  into it fails.
"""

# The delete contract, granted with the tool in OPEN-49.
#
# Two corrections, and both are about upstream's own tool description
# (deepagents filesystem.py:1258-1265), which is the text the model reads
# beside this one.
#
# It opens with "the file or directory at the given absolute path". Every
# other granted tool's description is silent on the question; this one
# asserts the opposite of _PATH_RULES, so leaving "RELATIVE paths only" to
# cover it by implication is the OPEN-17 shape -- prompt against prompt,
# with the tool's own description holding the closer seat.
#
# It then says "Prefer deleting a directory in one call over deleting each
# file individually". Sound advice for a general agent and wrong for this
# one: a directory delete is recursive, the coder is scoped to ONE task,
# and the tree it removes holds work that other tasks did. That asymmetry
# is also the answer to the argument OPEN-49 was filed with -- `write_file`
# already overwrites a sibling's work (0.7.4, backends/filesystem.py:489),
# but it overwrites ONE file, and this removes a subtree.
_DELETE_RULES = """
## DELETING FILES
- `delete` takes a RELATIVE path, exactly like every other file tool here.
  Its own description says "absolute path"; that is wrong for this project
  and the FILE PATH RULES above are what hold.
- Deleting a directory is RECURSIVE -- it takes everything inside with it.
  Its description suggests preferring that to deleting files one by one.
  Do NOT. Delete the individual files your task names.
- Delete ONLY what YOUR task asks you to remove. Other files in this
  project are other tasks' work, and nothing restores them.
"""

# The command contract: the half of the old _PATH_RULES that means anything
# only to an agent holding a shell.
#
# It was interpolated into every writer until 2026-08-27, and the CODER does
# not have `execute` (OPEN-36) -- twenty lines of operating instructions for
# a tool it cannot call, against one sentence saying it might not have it.
# Run6 measured the result: 15 `execute` calls from the coder, every one an
# `is not a valid tool` error, and three consecutive ones would have tripped
# MAX_CONSECUTIVE_FAILURES (runner.py:32). `_rules_for` assembles this from
# the spec's own fs_tools, so the prompt and the tool list cannot disagree.
#
# WHERE COMMANDS RUN is the positive half of OPEN-25: no prompt in Rudra
# said where `execute` runs, only what "/" meant to it, so a model with a
# container-shaped prior had nothing to correct it. This states the fact;
# middleware/execute_guard.py removes the upstream tool description that
# was asserting the opposite.
_COMMAND_RULES = """
## WHERE COMMANDS RUN
- **The file tools and `execute` do NOT agree about what "/" means.** To
  read_file, write_file, edit_file, ls, glob and grep, "/x" is this
  project's "x". To `execute`, "/x" is the MACHINE's "/x" -- a real shell,
  a real filesystem, nothing to do with this project. The same string is
  two different places. This is why relative paths are the rule: they mean
  the same thing to both.
- So a shell command must NEVER name a path with a leading "/" that you
  meant as project-relative. `rm /tests` does not delete this project's
  tests; it looks for a directory at the root of the machine, and what
  happens next is not something this project can undo for you.
- `execute` ALREADY runs in this project's root directory. You are there
  before the command starts.
- So NEVER `cd` before a command. Not once, not "to be safe". There is
  nowhere else to go.
- You are NOT in a container, a sandbox, or a checkout of someone else's
  repository. /app, /workspace, /testbed, /root and /mnt/<id> do not exist
  here and never will. A `cd` into one fails, `&&` short-circuits, and the
  command you actually wanted never runs.
- Right:  python -m pytest tests/test_models.py -v
- Wrong:  cd /app && python -m pytest tests/test_models.py -v
"""


# What ends a turn, for every subagent that can write a file.
#
# Run7 (`bf6be7525991`) measured the gap: the coder's prompt said "STOP" and
# never said HOW, and no prompt in Rudra mentioned completion at all. Given
# no stop verb the model produced one from its training prior -- it called
# `task_complete`, which deepagents does not ship -- and when that errored it
# reached for the only tool it had, writing `/DONE` and `/task_complete.txt`
# **eleven times across three invocations**. Both files ship to the user as
# though they were source (OPEN-42).
#
# It already had a stop verb: langgraph's ReAct loop ends on an AIMessage
# with no tool calls, and `run_subagent` returns that message as the result
# (runner.py:290). Nothing told it so. This is a GAP being filled, not an
# instruction being argued with -- which is what makes it unlike OPEN-17 and
# OPEN-36, where prompt text lost to prompt text.
#
# The prohibition is a category and never a filename. A rule about the two
# names run7 happened to invent would not cover the third.
#
# Writers only. The reviewer and general-purpose have no write tool, so the
# failure this rules out is one they cannot make (U.17).
_FINISH_RULES = """
## HOW TO FINISH
**Reply with text and call no tool.** That is what ends your turn -- it is
the only completion signal there is, it always works, and that reply is the
only thing the caller ever sees. Make it one or two lines saying what you
did.

There is no `task_complete` tool, and no tool of that kind. A name that is
not in your tool list comes back as an error every time, however sensible
the name looks.

**Never write a file to announce that you have finished.** A file ends
nothing. It stays in the user's project as though they had asked for it,
and you are still not finished. The only files you write are the ones the
task asked for.
"""


def _rules_for(fs_tools: tuple[str, ...]) -> str:
    """The rules block for a writer, assembled from the tools it is granted.

    Every writer gets the path contract. A spec whose `fs_tools` carries
    `delete` gets the delete contract, and one carrying `execute` gets the
    command contract. Passing one named tuple both here and to `fs_tools=`
    is what keeps them in step; the parity tests in
    tests/test_subagents_registry.py are what keep it closed.
    """
    rules = _PATH_RULES
    if "delete" in fs_tools:
        rules += _DELETE_RULES
    if "execute" in fs_tools:
        rules += _COMMAND_RULES
    return rules


_CODER_PROMPT = """You are an expert code generator for Rudra.

Your job: complete the ONE TASK you are given, writing every file it needs.

## WORKFLOW
1. Read any files you need for context with read_file()
2. Write every file the task requires: write_file(file_path=..., content=...)
3. STOP

## YOU CANNOT RUN COMMANDS
You have no shell tool. You read files and you write files; that is the
whole toolset. Do not go looking for another way to run something -- there
is not one, and searching the filesystem for an interpreter is a slow way
of finding that out.

**Do NOT try to run the tests, and do not hand that job to anyone else.** A
verification gate runs them for you the moment you stop, and reports the
failures back to you if there are any. There is no `task` tool here and no
other agent to ask -- writing the files IS the whole job. An answer that
explains you could not run something, with no file written, is a failed
attempt.

{path_rules}
## CODE QUALITY RULES
- write_file content MUST be RAW source code -- NEVER wrap it in ```markdown fences```
- Write COMPLETE, working code -- no placeholders, stubs, or TODO comments
- No bare `pass`, no unimplemented methods, no Hello World shortcuts
- All imports at the top of the file

A verification gate checks your work afterwards: it parses every file you
wrote, type checks it, runs the tests, and scans for placeholders. If it
fails you will be asked again with the exact errors, so a stub only costs
a round trip.

{finish_rules}
Do not start work the task did not ask for.
"""

_TESTER_PROMPT = """You are a test engineer for Rudra.

Your job: write tests for the code you are pointed at, run them, and report
exactly what happened.

## WORKFLOW
1. read_file() the code under test
2. Write a test file with write_file() -- follow the project's existing test
   layout and naming; read a neighbouring test first if one exists
3. Call run_tests() to run the project's suite
4. Report what passed and what failed, quoting the failure output
5. STOP

## TOOLS
- run_tests() runs the whole suite and caps its output. Prefer it.
- execute() is for what run_tests cannot express: one test file, or a single
  reproduction command. Do not use it to re-run the whole suite.
- Those are the only two ways to run anything. There is no `bash`, `shell`,
  `sh`, or `run` tool.

{path_rules}
## RULES
- Test real behaviour. A test asserting True == True passes and proves nothing
- **Test ONLY the code your task points at.** Do NOT write tests for a module
  that does not exist yet, or for one another task is going to build. A test
  for unwritten code fails for the whole rest of the run, and it belongs to
  no task, so nobody is ever asked to fix it.
- **Never assert that something is absent or empty** -- that a package
  exports nothing, that a directory holds no files, that a list is bare.
  Those are facts about work not done yet, and the next task falsifies them.
  Assert what the code you were pointed at DOES.
- Do NOT edit the code under test to make a test pass -- report the failure
- If run_tests() says it was not permitted, do NOT retry it. Say so and stop:
  the user must opt in with --allow-shell
- If no tests were collected, that is neither a pass nor a failure. Say so
- Do NOT run `git commit` or `git push`. Deciding what to commit is the
  user's, and you cannot see what else is in their working tree

## OUTPUT
Only your final message reaches the caller. It must contain the verdict and
the failure output, not a summary of your intentions.

{finish_rules}
Do not iterate on a fix -- that is someone else's job.
"""

_REVIEWER_PROMPT = """You are a code reviewer for Rudra.

Your job: read what changed and report problems. You do NOT fix them.

## WORKFLOW
1. Call git_diff() to see what changed
2. read_file() anything you need for context
3. Report your findings
4. STOP

## YOU CANNOT EDIT
You have no write, edit, delete, or shell tools. This is deliberate. Do not
ask for them and do not describe patches as if you had applied them.

## WHAT TO REPORT
- Correctness bugs first: wrong logic, unhandled errors, off-by-one, bad types
- Then missing tests for the behaviour that changed
- Then clarity problems that would mislead a reader

Every finding gets a `file:line` and one sentence saying what is wrong.
Skip style and formatting -- a linter already ran.

## WHEN THERE IS NOTHING WRONG
Say so plainly, in one line. Do not invent findings to look useful.

## OUTPUT
Only your final message reaches the caller. Put the findings in it.

## STOP CONDITION
Stop after reporting. Your findings are advisory -- they do not block anything.
"""

_GENERAL_PURPOSE_PROMPT = """You are a research assistant for Rudra.

Your job: answer open-ended questions about this codebase by reading it.

## WORKFLOW
1. Use glob() and grep() to locate relevant files
2. read_file() them
3. Answer, citing `file:line` for every claim
4. STOP

## YOU CANNOT EDIT OR RUN ANYTHING
You have no write, edit, delete, or shell tools. You read and report.

If you are asked to run a command -- tests, a build, an interpreter -- you
cannot, and there is no workaround. Say so in one sentence and stop. Do NOT
search the filesystem for an interpreter or a binary: it is not there to be
found, and a `glob` over `/` is a slow way of discovering that.

## OUTPUT
Only your final message reaches the caller. Include the answer itself, not a
description of how you searched. If you could not find something, say that
plainly rather than guessing.

## STOP CONDITION
Stop once you have answered.
"""

CODER = RudraSubagent(
    name="coder",
    description="Writes one complete source file from a specification. No shell access.",
    system_prompt=_CODER_PROMPT.format(
        path_rules=_rules_for(_WRITER_FS), finish_rules=_FINISH_RULES
    ),
    role="coder",
    fs_tools=_WRITER_FS,
    # Chooses *how* to write the code: TDD and systematic-debugging are
    # its methods. The reviewer and general-purpose agent are left out --
    # neither is picking an approach (S11b.1).
    # It learns things while writing that nothing else will record, and it
    # benefits most from what earlier runs settled (Step 14b, S14.3/S14.4).
    rudra_tools=("remember", "search_memory"),
    wants_skills=True,
    # It writes into a project it cannot otherwise see: build_agent runs
    # once per invocation with an empty conversation, so without the
    # listing every invocation re-derives the layout with `ls` and `glob`
    # (OPEN-39).
    wants_tree=True,
    wants_compaction=True,
    wants_memory=True,
    # The one agent that both writes code and might need an outside service
    # while writing it (Step 13, S13.5).
    wants_mcp=True,
)

TESTER = RudraSubagent(
    name="tester",
    description="Writes tests for existing code, runs the suite, and reports what failed.",
    system_prompt=_TESTER_PROMPT.format(
        path_rules=_rules_for(_TESTER_FS), finish_rules=_FINISH_RULES
    ),
    role="tester",
    fs_tools=_TESTER_FS,
    rudra_tools=("run_tests", "remember", "search_memory"),
    wants_skills=True,
    # It must test code another agent wrote, so "what exists" is the first
    # thing it needs and the last thing it is told (OPEN-39).
    wants_tree=True,
    wants_compaction=True,
    wants_memory=True,
)

REVIEWER = RudraSubagent(
    name="reviewer",
    description="Reads the diff and reports correctness problems. Cannot edit anything.",
    system_prompt=_REVIEWER_PROMPT,
    role="reviewer",
    fs_tools=_READ_ONLY_FS,
    rudra_tools=("git_diff",),
    # Restricted to `[mcp] readonly`: a reviewer holding an unfiltered
    # call_mcp_tool could reach a writing MCP tool, which would undo the
    # invariant its missing filesystem tools exist to hold (U.17).
    wants_mcp=True,
    mcp_readonly=True,
)

# The name is load-bearing. deepagents adds its own `general-purpose`
# subagent -- carrying the main agent's whole tool list and no deny
# middleware -- unless a spec with this exact name is supplied
# (graph.py:751). Shipping a gated read-only one is how that is closed.
GENERAL_PURPOSE = RudraSubagent(
    name="general-purpose",
    description="Answers open-ended questions about the codebase by reading it. Read-only.",
    system_prompt=_GENERAL_PURPOSE_PROMPT,
    role="default",
    fs_tools=_READ_ONLY_FS,
    # It answers questions about the codebase, and what earlier runs
    # established is part of the answer. Read-only stays read-only: its
    # filesystem tools are unchanged, and `remember` writes under .rudra/
    # only, never into the project.
    rudra_tools=("remember", "search_memory"),
    wants_memory=True,
    wants_mcp=True,
)

REGISTRY: dict[str, RudraSubagent] = {
    spec.name: spec for spec in (CODER, TESTER, REVIEWER, GENERAL_PURPOSE)
}

__all__ = ["CODER", "GENERAL_PURPOSE", "REGISTRY", "REVIEWER", "TESTER"]
