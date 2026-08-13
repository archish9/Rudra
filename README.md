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
> Rudra is **alpha software under active development**. It asks what it cannot infer, decides how the work should be built, shows you the plan, and only then writes anything. A task is done when a deterministic gate passes it: the code parses, type checks, its tests run, and no placeholders are left behind. If the gate fails, Rudra feeds the exact errors back and tries again, and stops rather than looping when two attempts fail identically. What it does **not** do yet is resume an interrupted run or retry a flaky provider. Review everything it produces.
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

**Running models locally with Ollama** (nothing leaves your machine):

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3:32b"
```

**Using a hosted model** — OpenRouter, vLLM, LM Studio, Groq, Together all work the same way:

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

### What Rudra leaves in your project

```
.rudra/
  config.toml     your settings                     ← worth committing
  facts.json      what Rudra established, and why   ← worth committing
  AGENTS.md       project notes                     ← worth committing
  run/            ledger.json, checkpoints, artifacts,
                  logs/permissions.jsonl,
                  logs/tests.log                      (regenerated every run)
```

`facts.json` is the durable one worth knowing about: it holds what Rudra
established about your project and *why* it believes each thing — so the next
run starts knowing your stack instead of asking again, and you can correct a
wrong assumption by editing one line.

Rudra writes a `.rudra/.gitignore` covering only the throwaway parts, so committing `.rudra/` is safe by default. It never touches your project's own `.gitignore` — whether you commit any of it is your call.

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

New here? Read **[Getting Started](Documentation/01-getting-started.md)**, then **[Choosing a Model](Documentation/03-providers.md)**. Before an unattended run, read **[Permissions](Documentation/09-permissions.md)**.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Copyright 2026 Archish Patel.

<p align="center">
  <i>Built with fire by the storm that purifies code</i>
</p>
