# 5. How It Works

What actually happens between typing a prompt and finding files on disk.

- [The short story](#the-short-story)
- [The planner](#the-planner)
- [The subagents](#the-subagents)
- [Step by step](#step-by-step)
- [The `.rudra/` folder](#the-rudra-folder)
- [How Rudra decides it's finished](#how-rudra-decides-its-finished)
- [The model layer](#the-model-layer)
- [What's built on](#whats-built-on)
- [Honest limitations](#honest-limitations)

---

## The short story

```
you ──▶ planner ──▶ ledger of tasks ──▶ for each task:
                                              │
                                    ┌─────────▼──────────┐
                                    │ coder writes it    │
                                    │ gate verifies it   │
                                    │ fix loop retries   │
                                    └─────────┬──────────┘
                                              ▼
                                    gate passed? task done
```

The planner decides *what work* the request needs and records it as tasks — plain language, not filenames. For each task a coder writes whatever files it needs, then a deterministic gate checks the result. If the gate fails, the coder gets the exact errors back and tries again.

**The planner cannot mark anything done.** There is no tool for it. Only the gate grants that, which is why "it says it finished" and "it finished" are the same statement here.

Every write, edit, delete and command passes the permission gate first. In the default `ask` mode that means it stops and shows you a diff; in `auto` it proceeds; in either, a small built-in floor still blocks the things nobody wants. See [Permissions](09-permissions.md).

---

## The planner

Runs at the start, and again only when the work stalls. It:

- reads your request and the project's file tree
- calls `add_tasks` with the work it foresees
- may call `drop_task` later, with a reason, if something turns out unnecessary

It never writes project code, and it has no tool that can mark a task finished.

Tasks are work, not filenames:

```
t1  implement a CSV parser that handles quoted commas
t2  write tests for the parser
```

One task may touch several files. The planner is consulted again when a task is given up on — so it can propose a different approach — and once when the list empties, in case something is missing. It is **not** consulted after an ordinary success, which is what keeps a run from spending a model call per task on bookkeeping.

---

## The subagents

Rudra ships four subagents. Each has its own model role and its own tool set, and **the tool set is the enforcement** — a subagent cannot call a tool it was never given.

| Subagent | Model role | Can write? | Can run commands? |
|---|---|---|---|
| `coder` | `coder` | yes | no |
| `tester` | `tester` | yes | yes |
| `reviewer` | `reviewer` | **no** | **no** |
| `general-purpose` | `default` | **no** | **no** |

The reviewer has no `write_file`, `edit_file`, `delete` or `execute` tool at all. It reads the diff, reports what it finds, and cannot act on it. That split is deliberate: the deterministic gate (`rudra verify`) decides done-or-not, and the reviewer only comments on quality — its findings never block anything.

Every subagent goes through the same permission gate as the main agent, so the same approval prompts, allow/deny rules and deny floor apply inside them.

**They run every task.** `loop/engine.py` calls the coder for each task, the tester when the gate reports no test judgement, and the reviewer once at the end. (This paragraph used to say they were not wired into a run yet — that was true when they were built and stopped being true one step later.)

### Watching them work

Each of them prints what it is doing, tagged with its name:

```
[coder] → [4] CALL write_file  {'file_path': 'parser.py'}
[coder] ✓ [5] write_file: Wrote parser.py
[tester] → [2] CALL execute  {'command': 'pytest -q'}
[tester] ✗ [3] ERROR from execute:
Error: pytest exited 1
```

A tag with a colon in it — `[coder:task:1]` — means the line came from a subagent that agent delegated to, not from the agent itself.

How much you see is `--verbose` / `--no-verbose`; the levels are in the [CLI reference](04-cli-reference.md#what-the-trace-shows).

---

## Step by step

**1. You run a command**

```bash
rudra "build a CSV parser with tests"
```

**2. Rudra works out where "here" is**

It resolves the project directory (current folder, or `--project-dir`), then loads configuration from that directory's `.env`. Configuration is resolved once and cached for the run.

**3. It looks around**

Rudra builds a file tree of your project, skipping `.git`, `node_modules`, `target`, `.next`, `__pycache__`, and friends, and honouring your `.gitignore`. It reads names only, never file contents, so a large repository costs almost nothing to survey.

**4. It works out your stack**

From the files present — `Cargo.toml` means Rust, `package.json` means Node, and so on. Recognised stacks: Python, Rust, Node, React/Next.js, Angular. That detection feeds the verification gate, which needs your project's real lint, type-check and test commands. Anything the agent works out for itself — the framework, a version floor, a constraint you stated — is recorded separately as a project fact in `.rudra/facts.json`, together with why it believes it.

**5. Planning runs in three stages**

Before any code is written, Rudra consults the planner three times, and each
stage is a separate agent that holds only the tools its own job needs:

- **clarify** — settles what the work depends on. It asks you a batch of related
  questions (up to `[agent] max_questions` for the whole run) and infers the
  rest, recording each fact with *why* it believes it. It cannot create tasks.
- **architect** — decides the shape: which file holds what, module boundaries,
  how errors surface, what is worth testing. Each decision is recorded as a fact
  with its reason. It cannot ask you anything; the questions are settled.
- **breakdown** — turns the request and those facts into tasks, then stops. It
  cannot ask questions or record facts.

A stage cannot do another stage's job, because the tool simply is not there. The
facts are the only thing passed between them, which is also how the coder, the
tester and the reviewer come to know the architecture: every one of them gets
those facts in its prompt.

**6. You approve the plan**

Rudra prints what it established and what it intends to do — each fact with
whether you told it, it inferred it, or it read it off your project — and waits:

```
[a]pprove  [r]evise  [c]ancel
```

Revise takes a sentence ("drop the tests task, I have my own") and re-plans the
task list around it, up to three times. Cancel stops without touching anything.
Pressing Ctrl-D or Ctrl-C counts as cancel, never as approval.

`rudra --plan "..."` stops here every time: it shows the plan and exits. `--auto`
skips the gate entirely, because nobody is there to answer.

If a task later blocks, only the **breakdown** stage is consulted again — the
architecture is not rewritten under a coder that already built on it.

**6. The loop runs**

For each pending task:

- a coder writes every file the task needs
- the gate verifies the result: syntax, lint, type check, tests, placeholders
- if it fails, the coder gets the exact errors back and tries again
- if two attempts fail *identically*, Rudra stops rather than burning the budget
- when the gate passes, the task is done

If the gate can't run at all — a denied command, a missing tool — the whole run stops there, because every remaining task would hit the same wall.

**7. It reports**

```
Tasks: 3 requested · 2 done · 1 blocked · 0 dropped

  ✓ t1  implement a CSV parser that handles quoted commas
  ✓ t2  write tests for the parser
  ✗ t3  handle escaped quotes          no progress: the same failure twice
```

Every task you were told about appears here with a status, and anything not done says why.

---

## The `.rudra/` folder

Rudra keeps its state inside your project:

| File | Written by | Purpose |
|---|---|---|
| `run/ledger.json` | planner + Rudra | The tasks, their status, and why anything stopped. **`rudra --continue` reads this** |
| `run/logs/verify.log` | Rudra | The gate's full output from the last check |
| `run/logs/usage.json` | Rudra | What the last run cost, per role |
| `run/logs/meta.json` | Rudra | Which Rudra, Python, platform, mode and models produced this run. Written at the start, so a run still going has one |
| `AGENTS.md` | Rudra | Project notes fed into the planner's prompt — a line per completed task, plus an architecture summary rewritten each run |
| `facts.json` | planner | What this project's agents established, and why |
| `checkpoints.db` | LangGraph | Conversation checkpoints. Written, but never replayed — see [Context and Memory](13-context-and-memory.md) |
| `memory/palace/` | Rudra + the agents | Long-term memory: decisions, finished tasks, blockers, preferences — searchable, and read back into every prompt. See [Memory](15-memory.md) |
| `memory/export/` | `rudra memory export` | The markdown copy of that store — the one worth committing |

Deleting the folder is safe — Rudra recreates what it needs. You do lose everything
that carries forward: the facts it established about your project, the notes in
`AGENTS.md`, and its long-term memory. You also lose the task list, so a run interrupted
before you delete it can no longer be continued.

**Committing it is your call.** Rudra never touches your `.gitignore`. Add `.rudra/` if you'd rather keep it local.

---

## Where a Ctrl-C lands

Between tasks, never mid-write. The signal cancels the asyncio task running
the agent; the loop catches that at the point where it would otherwise pick
up the next task, puts the interrupted one back to `pending`, and saves.

That boundary is chosen rather than incidental. Cancelling mid-write could
leave a half-written file the gate would then judge; cancelling between
tasks leaves exactly the state a run that stopped early leaves — which is
the state `rudra --continue` already knows how to work. One path, not two.

---

## How Rudra decides it's finished

> **A task is done when the verification gate passes.**

Not when a file exists. The gate runs five stages in order and stops at the first blocking failure:

| Stage | Blocks? | Asks |
|---|---|---|
| syntax | yes | Does every changed file parse? |
| lint | **no — advisory** | Reported in full, never fails a task |
| typecheck | yes | Do the types hold? |
| test | yes | Does your suite pass? |
| stubs | yes | Any placeholders left in the files this run touched? |

The stub scan is why this is more than "run the tests": an agent can pass a suite and still leave `pass` where an implementation belongs.

No model decides any of this. There is no tool that marks a task done — only the gate grants it.

**When it can't finish**, it says so rather than looping:

- **Two identical failures in a row** → the task is blocked, and the run moves on
- **The attempt budget runs out** (`[agent] max_fix_attempts`, default 3) → same
- **The gate itself can't run** — denied command, missing tool → the whole run stops

Run `rudra verify` yourself any time to see the same gate's verdict. Details in [Verification](10-verification.md).

---

## The model layer

Everything model-related goes through one function:

```python
from rudra.llm import build_model

model = build_model("planner")   # or "coder"
```

`build_model` reads that role's configuration, picks the right provider, and constructs the model. Nothing else in Rudra knows which provider you're using — a rule enforced by a test that parses every module and fails if any of them imports a provider package directly.

That's what makes switching providers a config edit rather than a code change.

Each provider declares exactly which options it accepts, because they disagree in ways that fail silently otherwise. Ollama, for instance, accepts a `timeout` argument and quietly discards it, having no such setting — so Rudra doesn't send one. It sends `num_predict` where Ollama expects it and `max_tokens` where the others do.

Rudra also refuses, before making any network call, to run a model whose provider reports it cannot call tools.

---

## What's built on

| Piece | Used for |
|---|---|
| [deepagents](https://github.com/langchain-ai/deepagents) | Agent runtime, file tools, conversation compaction |
| [LangChain](https://python.langchain.com) / LangGraph | Model interfaces and checkpointing |
| [Typer](https://typer.tiangolo.com) + [Rich](https://rich.readthedocs.io) | Command line and terminal output |

The file tools the agents use — `read_file`, `write_file`, `edit_file`, `delete`, `ls`, `glob`, `grep` — come from deepagents, rooted at your project directory so nothing outside it can be touched. `execute` runs commands there too.

Rudra adds a few of its own:

| Tool | What it does |
|---|---|
| `add_tasks` / `drop_task` / `read_ledger` | The task ledger in `.rudra/run/ledger.json` |
| `ask_user` | Asks you a batch of related questions mid-run, and records each answer as a project fact. Absent entirely when nobody can answer |
| `record_fact` | Records something established about the project — value, why, and whether it was asked, inferred or detected |
| `run_tests` | Works out your project's test command, runs it, reports counts and the failure tail |
| `git_diff` | The working-tree diff, capped so a big one can't fill the context window |
| `remember` / `search_memory` | This project's long-term memory — write one thing worth knowing next run, or search what earlier runs recorded |

`run_tests` is a thin wrapper: underneath, it resolves a real command and hands it to the same permission gate as any other shell call. That's why an `execute:` rule covers it without naming it, and why the audit log records the command itself — `/home/you/todo/.venv/bin/python -m pytest`, at its full path — on an `execute` line of its own, after the tool's. `git_diff` shells out too, but inside the project it only reads, so it runs ungated — like `read_file` — and no `execute:` rule reaches it.

---

## Honest limitations

Things worth knowing before you rely on it.

**Review is advisory, and only the deterministic gate decides anything.** The reviewer
runs once at the end, prints what it found, and blocks nothing. That is deliberate — an
LLM verdict does not decide completion here — but it means its findings are yours to
read and act on, not something Rudra has already handled.

**The reviewer sees nothing on a brand-new project.** Its pass reads `git diff`, which
shows changes to *tracked* files — so on a fresh repo, where everything is new and
untracked, it reports nothing, and the summary gives no hint that it did.

**Under `--auto` alone, Rudra writes code it cannot check.** Every verification stage
except the syntax check and the stub scan is a command, and commands are denied in
unattended runs unless you pass `--allow-shell`. So `--auto` on its own produces code
that parses and has no placeholders, with nothing having run its tests. Pair the flags
when you want the loop to actually verify itself.

**A shell command is not confined.** Writes are held inside your project by the backend;
an approved command runs with your user's full access. In `ask` mode you read each one
first, which is the real protection. Real containment would need OS-level isolation, and
Rudra does not do that.

**A mid-stream model failure ends the run.** Transient provider errors — `429`, `502`, a
dropped connection — are retried three times with backoff, but only *before* the model
starts answering; once the first chunk has arrived, a failure surfaces and the run stops.
Files already written stay, tasks marked `done` genuinely passed the gate, and
`rudra --continue` picks up the rest.

**An empty diff counts as a failed attempt.** With no changed files the gate has nothing
to judge, so a task that changed nothing is treated as an attempt that produced nothing.
The common way to hit this is a plan with two tasks covering one edit: the coder finishes
both under the first, and the second legitimately changes nothing and ends up `blocked`
while the feature works. Revise the plan when you see the overlap; under `--auto`,
re-running usually gives a cleaner breakdown.

**The live trace is truncated, not streamed.** Output arrives per message rather than per
token, and each line is cut at a few hundred characters. Long model replies are readable
in outline only. `Ctrl-C` in the REPL interrupts the current input and returns you to the
prompt; it does not cancel an agent turn already in flight.

**Memory between runs is narrow, and it is not the conversation.** What carries forward
is `facts.json`, `AGENTS.md`, and the long-term store in `.rudra/memory/` — decisions,
finished tasks, blockers, preferences. Checkpoints are written but never replayed, so
anything you explained in a dead run that never became a fact or a memory is gone. See
[Memory](15-memory.md).

Everything above is tracked. See [Project Status](08-project-status.md).

---

**Next:** [Project Status](08-project-status.md) · [Troubleshooting](06-troubleshooting.md)
