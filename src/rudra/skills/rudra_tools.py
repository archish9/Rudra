"""Rudra's entry in superpowers' platform-adaptation scheme.

Upstream 6.3.0 speaks in actions -- "dispatch a subagent", "read a file" --
and resolves them per harness through references/<harness>-tools.md, listed
under "## Platform Adaptation" in the bootstrap skill. Rudra adds one file
and one list entry instead of forking 14 skills (spec section 1); this is
what replaces the obsolete D10 tool-name rewrite.

The file is not a lookup table. Several enabled skills instruct behavior
Rudra structurally forbids -- verification-before-completion and
executing-plans both tell the agent to mark work complete, while only
loop/engine.py writes DONE and only on VerifyReport.passed. Reconciling
that is the point.
"""

from __future__ import annotations

REFERENCE_FILENAME = "references/rudra-tools.md"


def platform_ref_line(bundle_name: str, bootstrap_skill: str) -> str:
    """The Platform Adaptation bullet that points at Rudra's mapping file.

    Absolute, and that is the whole point of the function. It was the
    literal ``- Rudra: `references/rudra-tools.md` `` until OPEN-18: the model
    has no working directory, so a relative path in an injected prompt
    anchors against nothing. Measured on run `a5aa4552c9ab` -- the planner
    read `/skills/active/brainstorming/SKILL.md` six times and this file
    zero times, which left every reconciliation in it inert, including the
    section this module calls "the most important difference".

    Spelled against `library/` rather than `active/` for the reason
    `_rewrite_cross_refs` targets library paths (transform.py, spec S11a.4):
    library/ holds every skill regardless of enablement, so the path stays
    valid when the enabled set changes.
    """
    return f"- Rudra: `/skills/library/{bundle_name}/{bootstrap_skill}/{REFERENCE_FILENAME}`"


# Index-line overrides, applied to the RENDERED copy only.
#
# deepagents renders every skill's index entry verbatim from its frontmatter
# -- `- **{name}**: {description}` (middleware/skills.py:870) -- so a
# `description` is not documentation here. It is an instruction sitting in
# the system prompt of every agent that indexes the corpus, and the agent
# never asked for it.
#
# Two of the nine enabled skills issue an unconditional imperative there,
# and measurement says the imperative wins over any prompt text arguing
# with it. Runs `a5aa4552c9ab` and `298dd9ac5f2c`, both
# nvidia/nemotron-3-ultra-550b-a55b: ALL THREE planner stages opened by
# reading brainstorming before doing anything their own stage asked for.
# The first run emitted that skill's workflow as the task list (OPEN-17);
# the second, with the reconciliation already added to the stage prompts,
# emitted no tasks at all.
#
# Rewritten so the skill stays enabled, indexed and readable -- the owner's
# requirement -- while its index line stops ordering a planner stage to
# re-derive a workflow Rudra already implements in Python. Bodies are never
# touched, and neither is the source bundle: this edits the rendered copy,
# exactly as the cross-reference rewrite does.
DESCRIPTION_OVERRIDES: dict[str, str] = {
    "brainstorming": (
        "Read this for HOW to explore intent and weigh approaches. Do not "
        "re-derive its workflow on Rudra: the planner's clarify, architect "
        "and breakdown stages already implement it, each a separate agent "
        "with its own tools, and you are only one of them. Its steps are "
        "not units of work -- never pass them to add_tasks."
    ),
    "using-superpowers": (
        "Reference for how skills are reached on Rudra. Its content is "
        "already in your system prompt, so do not read it again, and do "
        "not read a skill before answering -- do your stage's job, and "
        "consult a skill only when it bears on the work in front of you."
    ),
}


RUDRA_TOOLS_MD = """# Rudra Tool Mapping

Skills speak in actions ("dispatch a subagent", "read a file", "create a
todo"). On Rudra these resolve to the tools below.

| Action skills request | Rudra equivalent |
| --- | --- |
| Read a file | `read_file(file_path=..., limit=1000)` |
| Write a new file | `write_file` |
| Change an existing file | `edit_file` |
| Remove a file | `delete` |
| List or search files | `ls`, `glob`, `grep` |
| Run a command | `execute` -- REAL shell, real paths; see below |
| Dispatch a subagent | `task` -- pass a subagent_type, listed below |
| Create or track todos | `add_tasks`, `drop_task`, `read_ledger` |
| Run the tests | `run_tests` |
| Inspect your own changes | `git_diff` -- the only git tool there is |
| Record a decision | `record_fact` |
| Ask the human a question | `ask_user` |
| Invoke a skill | no such tool -- see below |

Not every agent gets every tool, and a tool you were not granted does not
exist for you rather than refusing when called. The coder has no `execute`;
the reviewer has no way to write at all. Ask for what you have.

## Invoking a skill

Rudra has no skill-invoke tool. Skills are reached by reading them:
`read_file(file_path="/skills/library/superpowers/<name>/SKILL.md",
limit=1000)`. The default read limit of 100 lines truncates most skills, so
pass `limit`.

Cross-references in these skills are already written as
`/skills/library/...` paths. Read the path.

## You cannot mark work done

This is the most important difference, and it overrides any skill that says
otherwise.

`add_tasks` and `drop_task` add and remove work. **Neither can mark a task
complete, and no other tool can either.** Rudra's Python loop decides that,
and only when a deterministic gate passes. A skill instructing you to "mark
the task complete", "check it off" or "declare the work finished" is
describing a harness you are not running on. Do the work; the gate decides.

The gate is `rudra verify`: syntax, lint, typecheck, tests, and a scan for
stubbed-out code. It is deterministic and it is not your judgment. When it
fails, its output comes back to you verbatim and you fix what it named.

Code review is advisory here. A reviewer subagent runs once at the end and
prints its findings; it gates nothing.

## Planning happens in three stages, and you are one of them

This overrides any skill that describes planning as one continuous process.

Rudra plans in three separate agents, run in order, each with its own tools.
Your system prompt names which one you are. You do not run the other two,
and you cannot: the tools for their work were never registered for you.

- Clarifying questions, settling intent -- the **clarify** stage.
- Approaches, trade-offs, design decisions -- the **architect** stage.
- Turning the design into work -- the **breakdown** stage.

So a skill telling you to classify the request, then ask questions, then
propose approaches, then write it up is not wrong -- it is describing all
three stages at once. Do your stage. The facts you record are how the next
one receives your work.

**Planning ends at `add_tasks`, not at a document.** There is no design doc,
no spec file, no path to write one to and nothing to commit. The planner
holds `ls`, `read_file`, `glob` and `grep` and no write tool at all. A skill
that finishes by saving a plan or a spec finishes differently here: it
finishes at the ledger.

**A task names work that changes files.** Your own planning steps are not
tasks. This is a real failure, quoted from a run that produced nothing:

    BAD:  "Understand requirements through clarifying questions"
    BAD:  "Propose 2-3 architectural approaches with trade-offs"
    BAD:  "Present detailed design for approval"
    BAD:  "Write design specification document"

    GOOD: "write a Todo model with add, complete and delete"
    GOOD: "write tests for the Todo model"
    GOOD: "add a --format flag to the CLI"

Those four BAD entries are a planning workflow, not work. The coder that was
handed the first one had no file to write and spent the run searching for
directories that do not exist.

**The user's approval is real, and it is not yours to collect.** Rudra shows
the user the plan and takes their decision after the breakdown stage
returns -- outside the planner, in Python, before any code is written. You
have no tool for it. Do not ask for approval and do not wait for it: calling
`add_tasks` is how the plan reaches the user.

## Subagents

`task` takes a `subagent_type`:

- `coder` -- writes and edits code
- `tester` -- writes and runs tests
- `reviewer` -- reads and reports; it has no write tools at all
- `general-purpose` -- anything else

The reviewer cannot write because its tools are never registered, not
because its prompt asks it not to. Do not ask it to apply a fix.

## `execute` does not share the file tools' idea of "/"

The file tools are rooted at this project: `read_file("/src/app.py")` means
this project's `src/app.py`, and they cannot reach anything outside it.

`execute` is a real shell on a real machine. Its `/` is the machine's root.
The same string means two different places depending on which tool you hand
it to, and only the file tools are confined.

    write_file("/tests", ...)   -> creates  <project>/tests
    execute("rm /tests")        -> looks at the MACHINE's /tests

Use **relative** paths in shell commands -- they mean the same thing to
both, because `execute` already runs in the project directory.
`pytest tests/` is right; `pytest /tests` is a different filesystem.

Absolute paths for real system things are fine and normal: `/usr/bin/env`,
`/bin/sh`. What is never right is an absolute path you meant as
project-relative.

Directories need no command: writing `tests/test_x.py` creates `tests/`.
Do not `mkdir` one, and do not write a placeholder file to make one.

## Commands may be denied

`execute` passes through a permission gate before it runs. Under `ask` mode
the human approves each command. Under `--auto` commands are denied unless
the human opted in with `--allow-shell`, so an unattended run may be unable
to execute anything at all -- including tests.

A denial is an answer, not an error to route around. Do not rewrite a
denied command as a shell redirect, an editor invocation, or a script that
performs the same action. Report what was denied and continue with what you
can do.

## Asking questions

`ask_user` is budgeted by `[agent] max_questions` and may be zero. Ask only
what you cannot infer from the project itself. Record what you learn with
`record_fact`, including *why* -- facts reach every later agent, questions
do not.

When a question has likely answers, put them in that question's `options`
and set `multi_select` if more than one may apply. Never write the choices
into your reply instead: the user cannot select prose, so a question you
only narrate is one nobody was asked.

## Not available on Rudra

- **Browser and visual companions.** No browser, and no Node runtime. The
  visual companion described in `brainstorming` cannot start; work in text.
- **Git worktrees and branch workflows** are available only through the
  `git_*` tools and `execute`, both gated as above.
- **Web search and fetch.** Rudra is local-first and ships no web tools.
"""
