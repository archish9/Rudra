# 11. Tools

Every tool a Rudra agent can call, what it does, and how it behaves when it runs.

This is about the tools **the model** uses — the things it calls while working on your task. For the commands **you** type, see [CLI Reference](04-cli-reference.md).

You do not call these yourself. You read this page for three reasons: to understand what Rudra can do to your machine, to read an approval prompt or an audit line and know what it means, and to write allow/deny rules that name real tools.

---

## The short version

| Tool | Does | Gated? |
|---|---|---|
| [`read_file`](#read_file) | Read a file | never |
| [`ls`](#ls) | List a directory | never |
| [`glob`](#glob) | Find files by pattern | never |
| [`grep`](#grep) | Search file contents | never |
| [`write_file`](#write_file) | Create or replace a file | **yes** |
| [`edit_file`](#edit_file) | Change part of a file | **yes** |
| [`delete`](#delete) | Remove a file | **yes** |
| [`execute`](#execute) | Run a shell command | **yes** |
| [`task`](#task) | Hand work to a subagent | no — the subagent's own calls are gated |
| [`run_tests`](#run_tests) | Run your test suite | **yes**, as the command it runs |
| [`list_mcp_tools`](#list_mcp_tools) | See what MCP servers offer | never |
| [`describe_mcp_tool`](#describe_mcp_tool) | One MCP tool's arguments | never |
| [`call_mcp_tool`](#call_mcp_tool) | Run an MCP tool | **yes**, by tool id |
| [`git_diff`](#git_diff) | Show working-tree changes | **yes**, as the command it runs |
| [`remember`](#remember) | Record something for future runs | never — control plane |
| [`search_memory`](#search_memory) | Search what earlier runs recorded | never |
| [`record_fact`](#record_fact) | Record something established | never — control plane |
| [`ask_user`](#ask_user) | Ask you a question | never — control plane |
| [`add_tasks`](#add_tasks) | Add work to the ledger | never — control plane |
| [`drop_task`](#drop_task) | Retract a task | never — control plane |
| [`read_ledger`](#read_ledger) | Read the current task list | never |

Four groups, and the grouping is the security model rather than a filing convention:

- **Reads are never gated.** Prompting for every `read_file` would train you to press `a` without looking, which is worse than not asking. `READ_ONLY_TOOLS` in `permissions/rules.py`.
- **Writes and commands always are.** `MUTATING_TOOLS` — `write_file`, `edit_file`, `delete`, `execute`.
- **Two tools are a command wearing another name.** `run_tests` and `git_diff` shell out, so they are decided as the `execute` they actually perform, using the real command string. They are not gated twice.
- **Rudra's own bookkeeping is never gated.** `add_tasks`, `drop_task`, `ask_user`, `record_fact` change nothing outside `.rudra/`, and gating them once broke `--plan` badly enough to earn a ledger row.

---

## Reading

### `read_file`

Read a file from your project.

```
read_file(file_path="src/parser.py", limit=1000)
```

`limit` is lines and defaults to 100, which truncates most real files. Rudra's agents are told to pass a larger one.

Never gated, and confined to your project by the backend.

### `ls`

List a directory.

```
ls(path="src/")
```

### `glob`

Find files by shell pattern.

```
glob(pattern="**/*.test.ts")
```

### `grep`

Search file contents.

```
grep(pattern="def parse_csv", path="src/")
```

`glob` matches **names**, `grep` matches **contents**. An agent looking for "where is this implemented" wants `grep`; one looking for "what tests exist" wants `glob`.

---

## Writing

All three stop for approval under the default `ask` mode, and all three are confined to your project — `virtual_mode=True` on the backend is what enforces that, not a rule you could switch off.

### `write_file`

Create a file, or replace one entirely.

```
write_file(file_path="wordcount.py", content="import argparse\n...")
```

Your approval prompt shows the path, whether it is new, and the content:

```
╭─ approval required ────────────────────────────────╮
│ write_file  wordcount.py  (new file, 24 lines)     │
╰────────────────────────────────────────────────────╯
```

On an existing file you get a real unified diff instead of a line count.

### `edit_file`

Replace one exact string inside a file, leaving the rest alone.

```
edit_file(file_path="src/parser.py", old_string="delimiter=';'", new_string="delimiter=','")
```

Preferred over `write_file` for changes to existing files: the diff you approve is the actual change, not a whole file you have to re-read.

### `delete`

Remove a file.

```
delete(file_path="src/old_parser.py")
```

Gated like any write. The deny floor additionally blocks anything under `.git/` in **every** mode, `--auto` included.

---

## Running things

### `execute`

Run a shell command.

```
execute(command="python -m pytest tests/ -q")
```

**This is the tool worth understanding before an unattended run.** Writes are confined to your project by the backend; a shell command is not confined by anything. `echo hi > /somewhere/else` is a write that no filesystem rule sees.

That is not hypothetical. During Step 7's acceptance run, a model denied twice on `write_file` found that exact route by itself and succeeded. So:

- Under `ask`, you approve each command with its full resolved argv and working directory in front of you.
- Under `--auto`, `execute` is **denied** unless you opt in with `--allow-shell` or `[tools] shell_in_auto = true`.
- `[tools] shell = false` removes the tool from the agent altogether, rather than merely discouraging it.

Naming a command in an allow rule counts as opting in for that command:

```toml
[permissions]
allow = ["execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *"]
```

### `run_tests`

Run your project's test suite and report what happened.

```
run_tests()
```

No arguments. Rudra works the command out from your project's own layout — your virtualenv's `pytest`, `cargo test`, whatever `package.json` declares — so the agent does not have to guess and cannot get it wrong.

```
Tests failed. 12 run, 1 failed, 0 skipped.

Output:
…
FAILED tests/test_parser.py::test_quoted_commas - AssertionError
```

Full output goes to `.rudra/run/logs/tests.log`; only the tail returns to the model.

**Gated as the command it runs**, which has a consequence worth stating plainly: under bare `--auto`, `run_tests` is denied, so an unattended run writes code it cannot check. Pair the flags when that matters:

```bash
rudra --auto --allow-shell "add a CSV parser and make its tests pass"
```

The audit log shows both halves — the tool allowed at the middleware, and the inner command that was actually decided.

### `git_diff`

Show what has changed in the working tree, as a unified diff.

```
git_diff()                          # everything
git_diff(path="src/parser.py")      # one file
git_diff(staged=True)               # staged instead of unstaged
```

Output is capped; a very large diff is truncated with a note. New files that `git diff` cannot show are named separately, so a reviewer is not blind to them.

**This is the only git tool the model has.** No `git_status`, no `git_log`, no committing, no branching. Rudra's own git work — the `auto_branch` option — happens in Python, outside the model's reach.

Not a git repository? It says so rather than failing.

---

## MCP: tools Rudra doesn't ship

These three appear only when you have configured an MCP server. With no
`.mcp.json`, they do not exist. See [MCP](14-mcp.md) for setup.

Three tools carry **every** MCP call, however many servers you configure. That is
deliberate: registering each server's tools individually would put every server's
argument schemas in the prompt on every call, and ten servers would fill a 32B
context before the task started.

MCP tools are named `server__tool` — your server name from `.mcp.json`, two
underscores, the tool's own name.

### `list_mcp_tools`

```
list_mcp_tools(server="")
```

Lists tool ids and one-line descriptions. Leave `server` empty for everything.

```
kala__system_status — Report this project design system: spacing scale, type scale, …
kala__verify — Check frontend source files against the project design system. …
```

**No argument schemas.** That is what keeps the listing cheap; the model asks for
one schema at a time with `describe_mcp_tool`.

Never gated — it only reads a server's description of itself.

### `describe_mcp_tool`

```
describe_mcp_tool(tool_id="kala__system_bootstrap")
```

Returns that one tool's description and its full JSON argument schema. Never gated.

### `call_mcp_tool`

```
call_mcp_tool(tool_id="kala__verify", arguments={"dir": "/p", "paths": ["src/app.tsx"]})
```

Runs the tool and returns its output as text.

**Always gated, on the tool id.** An MCP server is a separate program Rudra does not
confine, so this is treated exactly like `execute`:

| Mode | Result |
|---|---|
| `ask` | prompts, showing server, tool and arguments |
| `--auto` | **denied** unless `--allow-mcp` or `[mcp] mcp_in_auto = true` |
| `--plan` | denied |

Rules name the id:

```toml
[permissions]
allow = ["call_mcp_tool:kala__system_status"]
deny  = ["call_mcp_tool:*__system_bootstrap"]
```

The parameter is `arguments`, not `args` — the tool layer mangles a parameter
literally named `args`.

A failing or missing server returns a readable sentence rather than raising, and the
run continues.

---

## Delegating

### `task`

Hand a piece of work to a subagent.

```
task(subagent_type="coder", description="implement the CSV parser")
```

Four subagents ship, and their differences are structural rather than instructional:

| `subagent_type` | Can | Cannot |
|---|---|---|
| `coder` | read, write, edit, glob, grep, remember and search memory | **run commands** — no `execute` |
| `tester` | everything the coder can, plus `execute` and `run_tests` | — |
| `reviewer` | read, glob, grep, `git_diff` | **write anything at all**, and it sees no memory |
| `general-purpose` | read, glob, grep, remember and search memory | write, execute |

The reviewer cannot edit your code because the write tools are never registered for it — there is no prompt telling it to behave, and nothing to deny. If you ask the reviewer to apply a fix, it will tell you it cannot.

`task` itself is not gated: the subagent's own calls go through the same permission engine, so gating the spawn would prompt you twice for one action.

---

## Rudra's bookkeeping

These never touch your project — they write to `.rudra/`, or to the agent's own context — and are never gated.

### `record_fact`

Record one thing the agent has established about your project, **and why**.

```
record_fact(
    key="cli_framework",
    value="argparse",
    why="stdlib only; the user asked for no dependencies",
    source="asked",
)
```

`source` is `asked` (you said so) or `inferred` (Rudra worked it out). That distinction is what makes `facts.json` reviewable — the inferences are the ones worth your eye.

Facts reach **every** agent: the planner records one, and the coder, tester and reviewer all see it. Before this existed the coder was never told what language the project was in.

Keys are not a fixed list. There is no enum of valid fact names anywhere in Rudra — validation covers types and size, never names — so a project with an unusual constraint can record it.

### `ask_user`

Ask you a batch of related questions and record the answers as facts.

```
ask_user(
    questions=["Which language?", "Which CLI framework?"],
    keys=["language", "cli_framework"],
)
```

One key per question, same order. Every answer is recorded automatically, so the agent does not also call `record_fact`.

Budgeted by `[agent] max_questions` (default 5) for the whole run. Set it to `0` and Rudra never asks — it infers instead, and marks each inference as such.

Under `--auto` the tool is **not registered at all**, rather than registered and refusing. Nobody is there to answer, so the agent is not offered a door that does not open.

### `add_tasks`

Add work to the run's ledger.

```
add_tasks(descriptions=[
    "write a CSV parser that handles quoted commas",
    "write tests for the parser",
])
```

Each entry is a unit of **work in plain language, not a filename**. `"parser.py"` is a bad task; `"write a CSV parser that handles quoted commas"` is a good one.

### `drop_task`

Retract a task that should not be done after all.

```
drop_task(task_id="t2", reason="the user will write these tests themselves")
```

A reason is required, and it is reported to you. Use it when a task is unnecessary or wrong — not when it is merely hard. A finished task cannot be dropped.

### `read_ledger`

Read the current task list back.

```
read_ledger()
```

Counted as a read, not as control plane: it only reads `.rudra/run/ledger.json` and changes nothing.

### `remember`

Record one thing worth knowing on a **future** run of this project.

```
remember(
    content="The user runs everything through uv, never pip — pip installs "
            "into the wrong environment on their machine.",
    room="preferences",
)
```

`room` is one of `decisions`, `tasks`, `blockers`, `preferences`. An unknown room comes
back as a `REJECTED:` sentence naming the valid four, so the model can fix the call
rather than fail.

This is for what **nothing else will record**: a preference you stated, a constraint the
agent discovered, an API that behaves unlike its documentation. Completed and blocked
tasks are already recorded automatically by Python, and the tool's own instructions say
not to repeat them — models sometimes do anyway, which is what
`rudra memory forget --added-by agent` is for.

Everything this tool writes is tagged `added_by=agent`, so a model's judgement is always
distinguishable from Rudra's deterministic record.

Never gated: it writes under `.rudra/memory/` only, never into your project. If the store
is unreachable it says so and tells the model to carry on — a memory failure never fails
a task.

### `search_memory`

Search what earlier runs on this project recorded, by meaning.

```
search_memory(query="why did we pick this parser", room="decisions")
```

Returns up to five hits, each labelled with its room and who recorded it. `room` is
optional and narrows the search.

Agents already receive the most relevant memories in their prompt without asking
([Memory](15-memory.md#6-how-a-memory-comes-back-recall)). This tool covers the case that
block structurally cannot: history the model turns out to need mid-task.

Counted as a read, not as control plane — it reads `.rudra/memory/` and changes nothing.

### The one thing no tool can do

**Nothing in this list can mark a task complete.** There is no `finish_task`, no `mark_done`, no status argument anywhere.

That is deliberate and it is the load-bearing design decision of Rudra's loop. The model decides what work exists; Python decides when it is done. A task is complete only when the deterministic gate passes it — the code parses, types hold, tests run, no placeholders remain — and only `loop/engine.py` writes that verdict.

The ledger tools cannot express `DONE`, so an over-eager model cannot declare victory. It is not a prompt asking nicely.

### `compact_conversation`

Summarises the agent's own conversation so far and offloads the detail, freeing
room in the context window.

```
compact_conversation()
```

**Only the coder and tester get it.** They are the agents that retry, re-read
failing test output, and can run long enough to fill a window; the planner
stages are short and the reviewer runs once.

It is never gated — it writes no file and runs no command, and a prompt asking
you to approve an agent tidying its own notes is a prompt with no decision in
it. A call shows up in your run's usage summary as a *compaction*.

You will rarely see it fire. Rudra also compacts automatically at 85% of the
window, and the tool refuses to run below roughly half that threshold, so an
eager model cannot compact a nearly-empty conversation.

> This is separate from Rudra's other context defence: any single tool result
> larger than a tenth of your window is written to `.rudra/run/artifacts/`
> and replaced with a preview plus a pointer, so one enormous `pytest`
> transcript cannot swallow the conversation. Both thresholds come from
> `context_tokens` — see [Context and Memory](13-context-and-memory.md).

---

## Which agent gets which tool

| | planner: clarify | planner: architect | planner: breakdown | coder | tester | reviewer | general-purpose |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `read_file` `ls` `glob` `grep` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `write_file` `edit_file` | — | — | — | ✅ | ✅ | — | — |
| `execute` | — | — | — | — | ✅ | — | — |
| `run_tests` | — | — | — | — | ✅ | — | — |
| `git_diff` | — | — | — | — | — | ✅ | — |
| `ask_user` | ✅ | — | — | — | — | — | — |
| `record_fact` | ✅ | ✅ | — | — | — | — | — |
| `add_tasks` `drop_task` | — | — | ✅ | — | — | — | — |
| `compact_conversation` | — | — | — | ✅ | ✅ | — | — |
| `remember` `search_memory` | — | — | — | ✅ | ✅ | — | ✅ |
| `list_mcp_tools` `describe_mcp_tool` `call_mcp_tool` | — | — | — | ✅ | — | ⚠️ | ✅ |

⚠️ The reviewer gets the MCP tools **filtered to `[mcp] readonly`**: a tool outside that
list is not merely denied to it, it is never listed and cannot be named. An MCP server
can write files, so an unfiltered `call_mcp_tool` would undo the invariant its missing
`write_file` exists to hold. The tester and planner stages get no MCP at all.

Read the planner columns left to right and the three-stage plan falls out of the tool grants alone: **clarify** can ask and record but cannot declare work; **architect** can record but not ask, because re-interrogating you mid-plan is what the staging exists to prevent; **breakdown** can only add tasks, and by then the facts are settled.

The coder and the tester also carry a **skill index** — a short list of methodology documents they can read on demand. The planner stages, the reviewer and the general-purpose agent do not; the planner's methodology is built into its stage prompts instead, for a measured reason. See [Skills](12-skills.md).

A stage cannot do another stage's job because it has no tool for it. Absence is the enforcement — a prompt saying "clarify before planning" is a hint, and Step 7 measured what a model does with hints it finds inconvenient.

---

## Reading the audit log

Every gated decision, in every mode, lands one line in `.rudra/run/logs/permissions.jsonl`:

```json
{"ts": "…", "tool": "execute", "arg": "pytest test_add.py", "rule": "<auto:shell-not-opted-in>", "mode": "auto", "decision": "deny", "source": "auto-shell"}
{"ts": "…", "tool": "run_tests", "arg": null, "rule": null, "mode": "auto", "decision": "allow", "source": "wrapped-execute"}
{"ts": "…", "tool": "execute", "arg": "/home/you/todo/.venv/bin/python -m pytest", "rule": null, "mode": "auto", "decision": "allow", "source": "mode-default"}
{"ts": "…", "tool": "write_file", "arg": "wordcount.py", "rule": null, "mode": "ask", "decision": "approve", "source": "prompt"}
```

`arg` is the path for a write, or the real command string for anything that shells out. `run_tests`
records no `arg` of its own: the command it runs is judged, and recorded, on the `execute` line after
it — for a Python project at its full path, as the gate resolved it.

`source` tells you **why** a decision went the way it did — `prompt` means you decided it, `auto-shell` means `--auto` refused a command you had not opted into, `wrapped-execute` means the inner command is what was really judged.

`decision` is what actually happened, which is not always what the engine asked for: an engine effect of `ask` with a recorded decision of `reject` is you turning something down, and that gap is the interesting case.

Full rule syntax and precedence: [Permissions](09-permissions.md).

---

## Related

- **[Permissions](09-permissions.md)** — allow/deny rules, the deny floor, approval prompts
- **[Verification](10-verification.md)** — the gate that decides a task is done
- **[How It Works](05-how-it-works.md)** — the loop these tools run inside
- **[Skills](12-skills.md)** — the vendored superpowers methodology and how it maps onto these tools
- **[MCP](14-mcp.md)** — attaching outside tool servers, and controlling what they may do
- **[Memory](15-memory.md)** — what `remember` and `search_memory` write to and read from
- **[CLI Reference](04-cli-reference.md)** — the commands *you* type
