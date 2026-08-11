# Rudra

> **Rudra** (रुद्र) — the fierce, storm-like form of Shiva; the howler, the roarer.
>
> *The roaring storm that hammers and purifies code.*

Rudra is a **local-first autonomous coding agent for your terminal**. Tell it what you want in plain English, and it plans the work and writes the files.

```bash
rudra "write a Python CLI that reverses a string"
```

Point it at **any model you like** — a local Ollama model on your own machine, or a hosted one from OpenRouter, Anthropic, OpenAI, or Google. Rudra does not care which, and switching takes one line of configuration.

> ### 🚧 Early days — please read
>
> Rudra is **alpha software under active development**. It plans a set of files, writes them, runs commands, and can run your test suite and tell you what failed. What it does **not** do yet is act on that: nothing loops back to fix a failing test, and a file still counts as done when it exists, not when it works. Review everything it produces.
>
> **It asks before it writes.** By default every write, edit, delete and command stops for your approval and shows you a diff first. `--auto` skips the prompts for unattended runs.
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

Rudra writes a plan to `.rudra/run/PLAN.md`, then works through it. Before each file lands, it stops and shows you what's about to change:

```
╭─ approval required ────────────────────────────────╮
│ write_file  wordcount.py  (new file, 24 lines)     │
╰────────────────────────────────────────────────────╯
  import argparse
  …

[a]pprove  [r]eject  [A]lways (write_file:wordcount.py)  [d]iff (full)
```

`a` approves once, `r` rejects it, `A` stops asking about that file for the rest of the run, `d` shows the whole diff. On an existing file you get a real unified diff, not just a filename.

When it's finished:

```bash
python wordcount.py some-file.txt
```

Want to chat instead of firing one-off tasks? Run `rudra` with no arguments for an interactive session.

### Running it unattended

```bash
rudra --auto "add type hints to utils.py"
```

`--auto` approves everything without asking, for CI or a long run you don't want to babysit. Two things still hold:

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

**It reports, it doesn't yet repair.** The agent can be told the suite failed and still finish the run reporting success — nothing connects that answer back to the checklist. Closing that loop is the next milestone.

Prefer it worked on its own branch?

```toml
[tools]
auto_branch = true      # each run gets rudra/<slug>
```

Only fires from a clean tree with a branch checked out; otherwise it says why and carries on where you are, without failing the run.

### What Rudra leaves in your project

```
.rudra/
  config.toml     your settings                ← worth committing
  AGENTS.md       project notes                ← worth committing
  run/            plan, checkpoints, artifacts,
                  logs/permissions.jsonl,
                  logs/tests.log                 (regenerated every run)
```

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

New here? Read **[Getting Started](Documentation/01-getting-started.md)**, then **[Choosing a Model](Documentation/03-providers.md)**. Before an unattended run, read **[Permissions](Documentation/09-permissions.md)**.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Copyright 2026 Archish Patel.

<p align="center">
  <i>Built with fire by the storm that purifies code</i>
</p>
