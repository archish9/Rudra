# Rudra

> **Rudra** (रुद्र) — the fierce, storm-like form of Shiva; the howler, the roarer.
>
> *The roaring storm that hammers and purifies code.*

Rudra is a **local-first autonomous coding agent for your terminal**. Tell it what you want in plain English. It asks what it can't work out, shows you the plan, then writes the files and checks its own work.

```bash
rudra "write a Python CLI that reverses a string"
```

Point it at **any model you like** — a local Ollama model on your own machine, or a hosted one from OpenRouter, Anthropic, OpenAI, or Google. Rudra does not care which, and switching takes one line of configuration.

> ### 🚧 Early days — please read
>
> Rudra is **alpha software under active development**. It asks what it cannot infer, decides how the work should be built, shows you the plan, and only then writes anything. A task is done when a deterministic gate passes it: the code parses, type checks, its tests run, and no placeholders are left behind. If the gate fails, Rudra feeds the exact errors back and tries again, and stops rather than looping when two attempts fail identically. A transient provider error is retried with backoff, and if a run dies anyway, `rudra --continue` picks up the tasks it hadn't reached. Review everything it produces.
>
> **It asks twice: once about the plan, then about every file.** You approve the task list before any code is written, and by default each write, edit, delete and command stops for approval with a diff. `--auto` skips both for unattended runs; `--plan` stops after showing you the plan.
>
> See **[Project Status](Documentation/08-project-status.md)** for an honest, up-to-date list of what works and what doesn't.

---

## Install

**You need:** Python 3.12+, Git, and a model to talk to (see [Choosing a Model](Documentation/03-providers.md)).

```bash
git clone https://github.com/archish9/Rudra.git
cd Rudra
```

Then pick one:

**With [uv](https://docs.astral.sh/uv/) — fastest**

```bash
uv sync
uv run rudra --version
```

**With pipx — puts `rudra` on your PATH everywhere**

```bash
pipx install -e .
rudra --version
```

**With a plain virtualenv**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
rudra --version
```

You should see `Rudra v0.2.0`.

Full walkthrough, including OS-specific notes: **[Installation](Documentation/01-getting-started.md)**.

---

## Connect a model

Scaffold a config file in your project:

```bash
rudra init
```

That writes a commented `.rudra/config.toml`. Edit the bits you care about.

`rudra init` also fetches the embedding model Rudra's [memory](Documentation/15-memory.md) runs on — **~167 MB, once per machine, shared by every project**. It tells you the size before it starts, and it is the only thing Rudra ever downloads at run time.

**Running models locally with Ollama** (nothing leaves your machine):

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3:32b"
```

**Using a hosted model** — OpenRouter, vLLM, LM Studio, Groq, Together, NVIDIA all work the same way:

```toml
[model.default]
provider    = "openai_compatible"
base_url    = "https://openrouter.ai/api/v1"
model       = "nvidia/nemotron-3-ultra-550b-a55b:free"
api_key_env = "OPENROUTER_API_KEY"    # the NAME of the variable, not the key
```

Put the key itself in the environment or in `.env` (gitignored):

```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> .env
```

Environment variables still work and override the file, so `RUDRA_MODEL=...` is a fine way to try something without editing anything.

Then check it actually works before trusting it with a task:

```bash
rudra models test
```

```
                          Model check
┏━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Role    ┃ Provider ┃ Model            ┃ Construct ┃ Reach ┃ Tools ┃ Ctx    ┃
┡━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ planner │ ollama   │ qwen3:32b        │ ok        │ ok    │ ok    │ 131072 │
│ coder   │ ollama   │ qwen3:32b        │ ok        │ ok    │ ok    │ 131072 │
└─────────┴──────────┴──────────────────┴───────────┴───────┴───────┴────────┘
```

All four columns `ok` means you're ready. Anything else tells you exactly which part to fix — details in **[Configuration](Documentation/02-configuration.md)**.

For everything else — which config files were found, which `.env` was read, whether your install is intact:

```bash
rudra doctor
```

To check the code itself rather than the setup — does it parse, do the types hold, do the tests pass, are there placeholders left behind:

```bash
rudra verify                 # deterministic gate: syntax, lint, typecheck, tests, stubs
```

No model is involved. Details in **[Verification](Documentation/10-verification.md)**.

> **Your API key never goes in a config file.** `api_key_env` holds the *name* of an environment variable; the key itself lives in your environment or in `.env`, which is gitignored. Rudra reads the value at run time and never stores, logs, or prints it.

### Where settings come from

Five layers, each overriding the one above:

```
built-in defaults  →  ~/.config/rudra/config.toml  →  .rudra/config.toml  →  RUDRA_* env vars  →  CLI flags
```

You don't have to track that. `rudra config list` prints every effective value **and the layer that set it**, so a setting that isn't taking effect is traceable rather than mysterious.

---

## First run

```bash
mkdir ~/rudra-playground && cd ~/rudra-playground
rudra init
rudra "write wordcount.py: an argparse CLI that counts lines, words and characters in a file"
```

Rudra plans in three stages before writing anything — it settles what the work depends on (asking you only what it cannot work out), decides how it should be built, then breaks it into tasks. Then it shows you the result and waits:

```
Plan
  facts:
    language = Python (asked)
    cli_framework = argparse (asked)
    layout = wordcount.py holds parsing and counting (inferred)
  tasks:
    t1  implement an argparse CLI that counts lines, words and characters
    t2  write tests for the counting logic

Proceed? [a]pprove [r]evise [c]ancel [a/r/c] (a):
```

Each fact says where it came from — `asked` means you said so, `inferred` means Rudra worked it out — so the guesses are the ones that catch your eye. `r` takes a sentence like *"drop the tests task, I'll write those myself"* and re-plans around it, up to three times. `c` stops without touching anything.

Then, before each file lands, it stops again:

```
╭─ approval required ────────────────────────────────╮
│ write_file  wordcount.py  (new file, 24 lines)     │
╰────────────────────────────────────────────────────╯
  import argparse
  …

[a]pprove  [r]eject  [A]lways (write_file:wordcount.py)  [d]iff (full)
```

`a` approves once, `r` rejects it, `A` stops asking about that file for the rest of the run, `d` shows the whole diff. On an existing file you get a real unified diff, not just a filename.

Want the plan without the work? `rudra --plan "..."` stops after showing it and changes nothing.

When it's finished:

```bash
python wordcount.py some-file.txt
```

Want to chat instead of firing one-off tasks? Run `rudra` with no arguments for an interactive session.

### Running it unattended

```bash
rudra --auto "add type hints to utils.py"
```

`--auto` approves everything without asking — the plan and every file — for CI or a long run you don't want to babysit. It also never asks you a clarifying question: with nobody there to answer, Rudra infers what it needs and records each inference as such. Two things still hold:

- **Commands stay off** unless you add `--allow-shell`. Writes are confined to your project; a shell command isn't, and nobody is reading it before it runs. Since running your tests *is* a command, `--auto` on its own writes code it cannot check — pair the flags if you want it verified.
- **A small deny floor always applies**, in every mode — no writing into `.git/`, no `rm -rf /`.

Every gated decision lands in `.rudra/run/logs/permissions.jsonl`, so an unattended run leaves a record of what it was allowed to do.

### Letting it check its own work

```bash
rudra --auto --allow-shell "add a CSV parser and make its tests pass"
```

Rudra works out your project's test command from the files present — your virtualenv's `pytest`, `cargo test`, whatever `package.json` declares — runs it, and reports what happened:

```
Tests failed. 12 run, 1 failed, 0 skipped.

Output:
…
FAILED tests/test_parser.py::test_quoted_commas - AssertionError
```

Full output goes to `.rudra/run/logs/tests.log`; only the tail comes back to the model.

**It repairs, within bounds.** A failing gate sends the exact errors back to the coder for another attempt, up to `[agent] max_fix_attempts` (default 3). Two identical failures in a row stop the task rather than burning the budget, and the summary says which tasks were blocked and why.

Prefer it worked on its own branch?

```toml
[tools]
auto_branch = true      # each run gets rudra/<slug>
```

Only fires from a clean tree with a branch checked out; otherwise it says why and carries on where you are, without failing the run.

### If a run stops early

Runs die: a provider rate-limits you, a connection drops, you hit Ctrl-C. The
work already on disk stays, and so does the task list.

```bash
rudra --continue
```

That picks up the tasks Rudra never reached — no re-planning, no re-asking, no
replaying the old conversation. It reuses the request from last time, so you
don't retype it, shows you what's left, and waits for the same approval a fresh
plan gets.

```
Resuming: 4 done · 3 pending · 0 blocked
```

Two things it deliberately won't do. Give it a **different** request and it
refuses rather than working an old plan against a new intention:

```
Cannot continue: that is a different request from the one this plan was built for.
  planned for: build a small JSON config loader with tests
  you asked:   build a YAML parser instead
```

And it won't retry a **blocked** task — one that failed the same way twice
already. Repeating it would just spend your tokens reaching the same place, so
those are listed and left alone; change the request instead.

`--resume` is the same flag under a different name.

### What a run cost you

Every run reports its own token usage, per role:

```
✅ Complete
Files created:  3
Files modified: 1
Tokens: planner  41,204 in / 3,118 out   (7 calls)
        coder    88,930 in / 12,455 out  (19 calls, 2 compactions)
        tester   14,002 in /  1,203 out  (4 calls)
```

The same numbers land in `.rudra/run/logs/usage.json` if you want to track them
over time. If your provider doesn't report usage — some local endpoints don't —
you'll see `not reported` rather than a `0`, because a zero would look like a
free run.

*Compactions* are the coder or tester deciding its own context is getting long
and summarising it mid-task, so a long fix loop doesn't run out of room.

### What Rudra leaves in your project

```
.rudra/
  config.toml     your settings                     ← worth committing
  facts.json      what Rudra established, and why   ← worth committing
  AGENTS.md       project notes, updated as it works ← worth committing
  memory/
    export/       markdown copy of long-term memory ← worth committing
    palace/       the searchable store itself         (binary, ignored)
  run/            ledger.json, checkpoints, artifacts,
                  logs/permissions.jsonl,
                  logs/tests.log,
                  logs/usage.json                     (regenerated every run)
```

Three of those are worth knowing about:

**`facts.json`** holds what Rudra established about your project and *why* it
believes each thing — so the next run starts knowing your stack instead of
asking again, and you can correct a wrong assumption by editing one line.

**`AGENTS.md`** is the project's running notebook. Every completed task adds a
line naming what was done and which files changed (taken from git, not from the
model), and at the end of each run Rudra rewrites the *Architecture Notes*
section into a short description of how the project is built. The planner reads
it at the start of every run, so it is how yesterday's decisions reach
tomorrow's. The log keeps the most recent 20 entries — it is re-read on every
planner call, so it is not allowed to grow forever.

**`memory/`** is the long-term store: everything this project has decided,
finished, been blocked by, or been told you prefer, searchable by meaning. The
20-entry cap above is a prompt budget rather than data loss precisely because
nothing ages out of here. Section below, full guide in
**[Memory](Documentation/15-memory.md)**.

Rudra writes a `.rudra/.gitignore` covering only the throwaway parts, so committing `.rudra/` is safe by default. It never touches your project's own `.gitignore` — whether you commit any of it is your call.

---

## What the agent can actually do

Everything Rudra does to your machine goes through a small, fixed set of tools. Worth knowing before an unattended run:

| | |
|---|---|
| **Reads** | `read_file` · `ls` · `glob` · `grep` — never gated. Prompting on every read teaches you to stop reading the prompts |
| **Writes** | `write_file` · `edit_file` · `delete` — always gated, and confined to your project by the backend |
| **Commands** | `execute` — always gated, and **denied under `--auto`** unless you pass `--allow-shell`. A write is confined; a shell command is not |
| **Delegation** | `task` — hands work to `coder`, `tester`, `reviewer` or `general-purpose` |
| **Checking** | `run_tests` · `git_diff` — these shell out, so they are judged as the command they really run |
| **Memory** | `remember` · `search_memory` — write and read this project's long-term memory. Never gated: they touch `.rudra/memory/` and nothing else |
| **Bookkeeping** | `record_fact` · `ask_user` · `add_tasks` · `drop_task` · `read_ledger` — these touch `.rudra/` only |

The differences between subagents are structural, not instructional: the **coder has no `execute`**, and the **reviewer has no write tools at all** — they are never registered, so there is nothing to deny and no prompt to talk it out of.

**Nothing in that list can mark a task complete.** There is no `finish_task` and no status argument anywhere. The model decides what work exists; Python decides when it is done, and only when the deterministic gate passes.

Every tool, with signatures, examples and the per-agent grant table: **[Tools](Documentation/11-tools.md)**.

---

## Batteries included: superpowers

Rudra vendors [**superpowers**](https://github.com/obra/superpowers) by Jesse Vincent (MIT) — a library of written procedures the model can consult: `brainstorming` before designing, `test-driven-development` before implementing, `systematic-debugging` before guessing at a fix, `verification-before-completion` before claiming something works, and ten more.

They cost almost nothing to carry. Only each skill's name and one-line description sit in the prompt; the full text is fetched on demand when one actually matches the task.

**The copy is frozen.** No version check, no update fetch, no network call at run time — ever. A methodology library that changed under you would change how your agent behaves between two runs of the same command, with nothing in your project explaining why. The freeze is enforced rather than promised: a hash manifest covers all 52 vendored files and a test verifies it on every run.

The planner, coder and tester draw on it; the reviewer doesn't. Narrow the set with `[skills] enabled` in your config, or switch it off with `enabled = []`.

**You can write your own too** — drop a `SKILL.md` in `.rudra/skills/<name>/` and it joins the library, overriding a bundled skill of the same name. `rudra skills validate` tells you whether it will actually load, which matters because the loader skips a malformed one in silence.

Details, the full skill list, and what Rudra adapts: **[Skills](Documentation/12-skills.md)**.

---

## Memory that outlives the run

An agent with no memory meets your project fresh every morning. You explain the same
constraint twice, it re-derives a decision you already made, and it walks into the wall
that stopped it last week.

Rudra keeps a **searchable memory per project**, powered by
[**MemPalace**](https://github.com/MemPalace/mempalace) (MIT). It lives at
`.rudra/memory/palace/`, runs entirely on your machine, needs no API key, and is on for
every project — there is no switch to forget to flip.

**Four kinds of thing, and nothing else:**

| Room | Holds |
|---|---|
| `decisions` | a choice and why — `language = Python (inferred: the request names a .py file)` |
| `tasks` | work that passed the gate, with the files **git** says changed |
| `blockers` | what stopped a task, and why it stopped |
| `preferences` | how you want work done |

**Most of it is written by Python, not by a model.** A plan you approve files its facts;
a task that passes the verification gate files itself; a blocked task files its blocker.
Those are tagged `rudra`. On top of that, the coder, tester and general-purpose agents
have a `remember` tool for what nothing else would record — a preference you stated, an
API that behaves unlike its docs — and everything it writes is tagged `agent`, so you
can always tell a proven record from a model's opinion, and prune one without the other:

```bash
rudra memory list                       # Room · By · Recorded · Memory
rudra memory search "package manager"   # by meaning, not keyword
rudra memory forget --added-by agent    # drop the opinions, keep the record
```

**It comes back without anyone asking for it.** Before each agent starts, Rudra searches
this project's memory with the task text and pastes the best matches into the prompt
under *"What this project has learned"* — because a model only calls a search tool when
it thinks to, and recall cannot depend on that. The block is capped at a fiftieth of the
model's context window and truncated by whole entries. The reviewer is the one agent that
doesn't get it: it reads a diff, and history doesn't change what the diff says.

**Nothing leaves your machine.** Embedding is local. No transcripts are ever stored — no
dialogue, no reasoning, no file contents. Each project's memory is invisible to every
other project. The single network access is the one-time model download that `rudra init`
does up front.

**Keep it in git without committing a binary.** `rudra memory export` writes plain
markdown to `.rudra/memory/export/`, one file per room, which the shipped
`.rudra/.gitignore` keeps while ignoring the store itself. `rudra memory import` restores
it exactly, and repeating an import is safe.

Everything above in detail — the write points, the recall budget, the CLI, privacy, the
internals, and what happens when it breaks: **[Memory](Documentation/15-memory.md)**.

---

## Plugging in outside tools: MCP

Rudra's own tools read files, write files, run commands and run your tests. **MCP** lets
you add tools it doesn't ship with — a design-system checker, a database browser, your
company's internal API — by pointing it at a program that offers them.

Rudra reads **`.mcp.json`, the same file Claude Code uses**, so a server config you
already have works here unchanged:

```bash
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp
rudra mcp test
```

```
┏━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Server ┃ Status ┃ Detail                                                     ┃
┡━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ kala   │ ok     │ 8 tools: system_status, verify, explain, surface_brief,    │
│        │        │ guide, inspect, critique, system_bootstrap                 │
└────────┴────────┴────────────────────────────────────────────────────────────┘
```

Then just ask for what you want — you never name a tool yourself:

```bash
rudra "set up a design system for a calm invoicing tool, then build a settings page that follows it"
```

**Adding servers doesn't bloat the prompt.** Most clients load every tool from every
server into the model's context, used or not. Rudra gives the model three tools instead
— *what's available*, *how do I call this one*, *run it* — so ten servers cost the same
as one. Tools are named `server__tool`, and a server's process starts only when it is
actually used.

**An MCP server is a separate program, so it's gated like a shell command.** By default
you approve each call, seeing the server, the tool and its arguments. `--auto` refuses
MCP outright unless you add `--allow-mcp`; `--plan` never calls one. Rules use the tool
name, so `deny = ["call_mcp_tool:*__system_bootstrap"]` blocks a tool that writes while
leaving the read-only ones available — and the reviewer subagent only ever sees the
tools you list in `[mcp] readonly`, so it cannot change what it is reviewing.

Nothing ships enabled, and with no `.mcp.json` Rudra behaves exactly as it does today.

Setup, settings, a full worked example, and what to do when a server misbehaves:
**[MCP](Documentation/14-mcp.md)**.

---

## Documentation

| Guide | What's inside |
|---|---|
| **[1. Getting Started](Documentation/01-getting-started.md)** | Install step by step, set up a model, run your first task |
| **[2. Configuration](Documentation/02-configuration.md)** | `config.toml`, the five layers, per-role models |
| **[3. Choosing a Model](Documentation/03-providers.md)** | Ollama, OpenRouter, vLLM, Anthropic, OpenAI, Google — with working examples |
| **[4. CLI Reference](Documentation/04-cli-reference.md)** | `init`, `config`, `doctor`, `models test`, flags, interactive mode |
| **[5. How It Works](Documentation/05-how-it-works.md)** | What happens between your prompt and the files on disk |
| **[6. Troubleshooting](Documentation/06-troubleshooting.md)** | Error messages, what they mean, how to fix them |
| **[7. Development](Documentation/07-development.md)** | Running tests, project layout, contributing |
| **[8. Project Status](Documentation/08-project-status.md)** | What works today, what doesn't, what's coming |
| **[9. Permissions](Documentation/09-permissions.md)** | Approval prompts, allow/deny rules, the deny floor, the audit log |
| **[10. Verification](Documentation/10-verification.md)** | `rudra verify`: the gate that decides a task is done, and how to run it yourself |
| **[11. Tools](Documentation/11-tools.md)** | Every tool the agent can call — what each does, examples, and which subagent gets it |
| **[12. Skills](Documentation/12-skills.md)** | The vendored superpowers library: what ships, why it's frozen, what Rudra adapts |
| **[13. Context and Memory](Documentation/13-context-and-memory.md)** | The one setting that matters (`context_tokens`), what a run costs, and what Rudra remembers between runs |
| **[14. MCP](Documentation/14-mcp.md)** | Attach an outside tool server, control what it may do, and see a real one working |
| **[15. Memory](Documentation/15-memory.md)** | MemPalace: what Rudra remembers about your project, how it comes back, and how to read, prune, export and restore it |

New here? Read **[Getting Started](Documentation/01-getting-started.md)**, then **[Choosing a Model](Documentation/03-providers.md)**. Before an unattended run, read **[Permissions](Documentation/09-permissions.md)** and **[Tools](Documentation/11-tools.md)**. If the agent seems to lose track of what it was doing, read **[Context and Memory](Documentation/13-context-and-memory.md)** — it is almost always one unset setting — the same setting decides whether it recalls anything from earlier runs, which is **[Memory](Documentation/15-memory.md)**. Want it to reach tools Rudra doesn't ship? **[MCP](Documentation/14-mcp.md)**.

---

## Contributing

Rudra is free and open source, and developed by one person. **Code
contributions are not accepted right now** — but bug reports and feature
requests are genuinely welcome, and they are read.

What to include in a report, and how to report a security problem instead:
**[CONTRIBUTING.md](CONTRIBUTING.md)** and **[SECURITY.md](SECURITY.md)**.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Copyright 2026 Archish Patel.

Rudra vendors third-party code under its own terms — currently [superpowers](https://github.com/obra/superpowers) (MIT, Copyright © 2025 Jesse Vincent). Attribution for everything bundled is in [NOTICE](NOTICE).

<p align="center">
  <i>Built with fire by the storm that purifies code</i>
</p>
