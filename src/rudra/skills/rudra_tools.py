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

PLATFORM_REF_LINE = "- Rudra: `references/rudra-tools.md`"

RUDRA_TOOLS_MD = """# Rudra Tool Mapping

Skills speak in actions ("dispatch a subagent", "read a file", "create a
todo"). On Rudra these resolve to the tools below.

| Action skills request | Rudra equivalent |
| --- | --- |
| Read a file | `read_file(file_path=..., limit=1000)` |
| Write a new file | `write_file` |
| Change an existing file | `edit_file` |
| List or search files | `ls`, `glob`, `grep` |
| Run a command | `execute` |
| Dispatch a subagent | `task`, with `subagent_type` |
| Create or track todos | `add_tasks`, `drop_task` |
| Run the tests | `run_tests` |
| Git operations | `git_status`, `git_diff`, `git_log` |
| Record a decision | `record_fact` |
| Ask the human a question | `ask_user` |
| Invoke a skill | no such tool -- see below |

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

## Subagents

`task` takes a `subagent_type`:

- `coder` -- writes and edits code
- `tester` -- writes and runs tests
- `reviewer` -- reads and reports; it has no write tools at all
- `general-purpose` -- anything else

The reviewer cannot write because its tools are never registered, not
because its prompt asks it not to. Do not ask it to apply a fix.

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

## Not available on Rudra

- **Browser and visual companions.** No browser, and no Node runtime. The
  visual companion described in `brainstorming` cannot start; work in text.
- **Git worktrees and branch workflows** are available only through the
  `git_*` tools and `execute`, both gated as above.
- **Web search and fetch.** Rudra is local-first and ships no web tools.
"""
