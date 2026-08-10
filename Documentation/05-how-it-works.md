# 5. How It Works

What actually happens between typing a prompt and finding files on disk.

- [The short story](#the-short-story)
- [The two agents](#the-two-agents)
- [Step by step](#step-by-step)
- [The `.rudra/` folder](#the-rudra-folder)
- [How Rudra decides it's finished](#how-rudra-decides-its-finished)
- [The model layer](#the-model-layer)
- [What's built on](#whats-built-on)
- [Honest limitations](#honest-limitations)

---

## The short story

```
you ──▶ planner ──▶ PLAN.md ──▶ for each file: planner writes instructions
                                                        │
                                                        ▼
                                              a fresh coder writes it
                                                        │
                                                        ▼
                                        ┌───── permission gate ─────┐
                                        │ allow · deny · ask you    │
                                        └───────────┬───────────────┘
                                                    ▼
                                              file exists? tick it off
```

One planner decides *what* to build. A new coder is created for *each* file and writes exactly that one file. Rudra loops until the list is done.

Every write, edit, delete and command passes the permission gate first. In the default `ask` mode that means it stops and shows you a diff; in `auto` it proceeds; in either, a small built-in floor still blocks the things nobody wants. See [Permissions](09-permissions.md).

---

## The two agents

### Planner

Runs once at the start, then again before each file. It:

- reads your request and the project's file tree
- decides the complete list of files
- writes that list to `.rudra/run/PLAN.md` as a checklist of bare filenames
- writes detailed instructions for one file at a time into `.rudra/run/current_task.md`

It never writes project code itself.

`PLAN.md` looks like this:

```markdown
- [ ] main.py
- [ ] parser.py
- [ ] tests/test_parser.py
```

Filenames only, no prose — the orchestrator parses this file, so entries that don't look like paths are skipped.

### Coder

Created fresh for every single file, with a clean conversation each time. It:

- reads `.rudra/run/current_task.md` for its assignment
- reads `.rudra/run/tech_stack.md` for language and framework constraints
- reads any files it was told to look at for context
- writes exactly one file
- stops

Starting fresh each time keeps the context small and stops earlier files' details from bleeding into later ones.

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

From the files present — `Cargo.toml` means Rust, `package.json` means Node, and so on — writing the result to `.rudra/run/tech_stack.md`. Recognised stacks: Python, Rust, Node, React/Next.js, Angular.

**5. The planner plans**

It produces `PLAN.md` and the first task assignment, then stops.

**6. The loop runs**

For each unticked file:

- the planner writes instructions into `.rudra/run/current_task.md`
- a brand-new coder reads them and writes the file
- Rudra checks the file exists; if not, it retries, up to three attempts
- on success the item is ticked off in `PLAN.md`

**7. It reports**

```
🏁 Complete: 3/3 files generated
```

---

## The `.rudra/` folder

Rudra keeps its state inside your project:

| File | Written by | Purpose |
|---|---|---|
| `PLAN.md` | planner | The checklist, ticked off as work completes |
| `current_task.md` | planner | Instructions for the file being written now |
| `tech_stack.md` | Rudra | Detected language and frameworks |
| `AGENTS.md` | Rudra | Project notes fed into the planner's prompt |
| `project.json` | Rudra | Saved project context |
| `checkpoints.db` | LangGraph | Conversation checkpoints |

Deleting the folder is safe — Rudra recreates what it needs, though it forgets any plan in progress.

**Committing it is your call.** Rudra never touches your `.gitignore`. Add `.rudra/` if you'd rather keep it local.

---

## How Rudra decides it's finished

Worth understanding clearly, because it's the biggest current limitation:

> **A file counts as done when it exists on disk.**

Not when it compiles. Not when tests pass. Not when it's correct. Just present.

So Rudra will happily report `3/3 files generated` for three files that don't run. Always review what it produces.

This is a known gap and the next major piece of work — a real completion gate that runs your tests and only ticks an item off when they pass. See [Project Status](08-project-status.md).

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

The file tools the agents use — `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep` — come from deepagents, rooted at your project directory so nothing outside it can be touched.

---

## Honest limitations

Things worth knowing before you rely on it:

**No review step.** Nothing checks the generated code for correctness before reporting success.

**Shell exists, but the loop doesn't use it.** Rudra's agents can run commands, and do. What's missing is the step that runs *your test suite* and acts on the result — so a failing test doesn't yet cause a retry.

**Existence is the only success test.** As above — a file that exists counts as done.

**Silently skipped plan items.** If a `PLAN.md` line doesn't parse as a filename it's dropped, and the summary counts only what survived. `2/2 files generated` can hide a third item nobody wrote.

**A failed model call ends the run.** A dropped connection or rate limit raises an error and exits `1`, even if files were already written. Check your directory before assuming nothing happened.

**No memory between runs.** Each invocation starts fresh apart from what's on disk.

Everything above is tracked, and several items are next in line. See [Project Status](08-project-status.md).

---

**Next:** [Project Status](08-project-status.md) · [Troubleshooting](06-troubleshooting.md)
