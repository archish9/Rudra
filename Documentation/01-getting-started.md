# 1. Getting Started

Everything you need to go from an empty terminal to Rudra writing its first file. Should take about ten minutes, most of which is downloading a model.

- [Before you start](#before-you-start)
- [Install Rudra](#install-rudra)
- [Give Rudra a model](#give-rudra-a-model)
- [Check the connection](#check-the-connection)
- [Your first task](#your-first-task)
- [Interactive mode](#interactive-mode)
- [What Rudra leaves behind](#what-rudra-leaves-behind)
- [Where next](#where-next)

---

## Before you start

You need three things:

| | Why | Check with |
|---|---|---|
| **Python 3.12 or newer** | Rudra is a Python package | `python3 --version` |
| **Git** | To clone the repo | `git --version` |
| **A model** | Rudra thinks with an LLM | see [Give Rudra a model](#give-rudra-a-model) |

If `python3 --version` shows 3.11 or older, install a newer Python first. On macOS, `brew install python@3.13`; on Ubuntu, `sudo apt install python3.13`.

---

## Install Rudra

```bash
git clone https://github.com/archish9/Rudra.git
cd Rudra
```

Now pick whichever of these three suits you. They all end up in the same place.

### Option A — uv (fastest, recommended for contributors)

[uv](https://docs.astral.sh/uv/) handles the virtualenv and dependencies in one step.

```bash
uv sync
uv run rudra --version
```

Everything else in these docs shows plain `rudra`. With uv, prefix commands with `uv run`, or activate the environment once:

```bash
source .venv/bin/activate
rudra --version
```

### Option B — pipx (best if you want `rudra` available everywhere)

[pipx](https://pypa.github.io/pipx/) installs command-line tools into isolated environments while keeping them on your PATH. You never activate anything.

```bash
# Ubuntu / Debian / Mint
sudo apt install pipx

# macOS
brew install pipx

# Anywhere else
python3 -m pip install --user pipx
```

Then:

```bash
pipx ensurepath
# restart your terminal, or:  source ~/.bashrc

cd /path/to/Rudra
pipx install -e .
rudra --version
```

The `-e` means *editable*: edit the source, and the change takes effect immediately with no reinstall.

### Option C — plain virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
rudra --version
```

Remember to activate `.venv` in every new terminal.

### Confirm it worked

```bash
rudra --version
```

You want `Rudra v0.2.0`. If you get `0.0.0+unknown`, the package isn't properly installed — re-run your install step. If you get `command not found`, see [Troubleshooting](06-troubleshooting.md#command-not-found-rudra).

---

## Give Rudra a model

Rudra doesn't ship a model. You point it at one. Two broad choices:

**Local** — runs on your own hardware, nothing leaves your machine, free, needs a decent GPU or a lot of RAM.

**Hosted** — someone else's hardware, works on any laptop, usually costs money, needs an API key.

> **Minimum model size: 32B parameters.** Rudra used to carry a pile of workarounds for smaller models; those were removed. A 7B or 14B model will produce disappointing results and is not supported.

### Local, with Ollama

Install [Ollama](https://ollama.com/download), then:

```bash
ollama serve                # leave running in its own terminal
ollama pull qwen3:32b       # ~20GB download, one time
ollama list                 # confirm it's there
```

Create your config:

```bash
cd ~/your-project
rudra init
```

That writes a commented `.rudra/config.toml`. Edit the model block:

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3:32b"
```

### Hosted, with OpenRouter

[OpenRouter](https://openrouter.ai) fronts many providers behind one API, including some free models. Sign up, create a key, then edit `.rudra/config.toml`:

```toml
[model.default]
provider    = "openai_compatible"
base_url    = "https://openrouter.ai/api/v1"
model       = "nvidia/nemotron-3-ultra-550b-a55b:free"
api_key_env = "OPENROUTER_API_KEY"
```

And put the key itself in `.env`:

```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key-here' >> .env
```

Two things, easy to mix up:

- `api_key_env` is the **name** of the variable holding your key — it goes in the config file
- `OPENROUTER_API_KEY` is the **key itself** — it goes in the environment, never in the config file

Rudra reads the value at run time and never stores, logs, or prints it. `.env` is gitignored; `config.toml` is safe to commit precisely because the key isn't in it.

Anthropic, OpenAI, Google, vLLM, LM Studio, Groq, and Together all work too — see **[Choosing a Model](03-providers.md)**.

> **Prefer environment variables?** They still work, and they override both config files: `RUDRA_PROVIDER`, `RUDRA_MODEL`, `RUDRA_BASE_URL`, `RUDRA_API_KEY_ENV`. Handy for trying something without editing a file. Full precedence rules in **[Configuration](02-configuration.md)**.

---

## Check the connection

Don't discover a broken setup halfway through a task. Ask Rudra directly:

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

Four separate checks, because they break for different reasons:

| Column | What it proves | If it fails |
|---|---|---|
| **Construct** | Your config makes sense; no network involved | Bad provider name, missing API key variable, missing package |
| **Reach** | Rudra can actually talk to the model | Wrong `base_url`, bad key, model name typo, server down |
| **Tools** | The model can call tools | Model can't do tool calling — pick another one |
| **Ctx** | The context window in effect | Blank means the provider's default applies |

**Tools is the one that matters most.** Rudra needs tool calling for everything it does, and a model can answer a question perfectly while never emitting a single tool call. Reach passing tells you nothing about Tools.

Exit code is `0` if everything passed, `1` otherwise — handy in scripts.

For the rest of the setup — which config files were found and in what order, which `.env` was read, whether the `.rudra/` layout is intact — run:

```bash
rudra doctor
```

It also reports what your permission mode will actually do, and whether
commands are enabled. The default is `ask`: Rudra stops and shows you a diff
before every write, edit, delete and command. See
[Permissions](09-permissions.md).

---

## Your first task

Work in a throwaway folder for your first run:

```bash
mkdir ~/rudra-playground
cd ~/rudra-playground

rudra "write wordcount.py: an argparse CLI that counts lines, words and characters in a text file"
```

You'll see the banner, the task, and then live progress as Rudra establishes
what it needs, plans, and — once you approve — writes:

```
 ✓ [1] record_fact: Recorded language = "Python" (inferred).
 ✓ [3] add_tasks: Added 1 task(s): t1 (write an argparse CLI that counts lines, words and characters)

Plan
  facts:
    language = Python (inferred)
  tasks:
    t1  write an argparse CLI that counts lines, words and characters

Proceed? [a]pprove [r]evise [c]ancel [a/r/c] (a): a

 ✓ [5] write_file: Updated file /wordcount.py

Tasks: 1 requested · 1 done · 0 blocked · 0 dropped

  ✓ t1  write an argparse CLI that counts lines, words and characters
```

Press Enter to approve — that is the default. `r` lets you correct the plan in a
sentence; `c` stops without writing anything. On a request this small there is
little to correct, but on a real one the plan is where you catch a wrong
assumption before it costs you a run.

Try what it made:

```bash
printf 'one two three\nfour five\n' > sample.txt
python wordcount.py sample.txt
```

```
Lines: 2
Words: 5
Characters: 24
```

### Useful flags

| Flag | Does |
|---|---|
| `--project-dir PATH`, `-d` | Work in another folder instead of the current one |
| `--verbose`, `-V` | Show every tool call in detail |
| `--no-verbose` | Quieter output |
| `--version`, `-v` | Print the version and exit |

```bash
rudra "add type hints to wordcount.py" -d ~/rudra-playground
```

> **A note on `--dry-run`:** the flag exists, but right now it exits immediately without doing anything or showing a preview. It is not a working preview mode yet. See [Project Status](08-project-status.md).

---

## Interactive mode

Run `rudra` with no task for a session you can talk to:

```bash
rudra
```

```
rudra> add a --json flag to wordcount.py
→ Planning...
✓ Done

rudra> /tree
wordcount.py
sample.txt

rudra> /exit
```

| Command | Does |
|---|---|
| `/help` | List the available commands |
| `/tree` | Print the project file tree |
| `/exit`, `/quit` | Leave |

`Esc` three times also exits.

---

## What Rudra leaves behind

Rudra keeps its working state in a `.rudra/` folder inside your project, split by how long it lasts.

**Durable — text, safe to commit:**

| File | What it is |
|---|---|
| `config.toml` | Your project settings, written by `rudra init` |
| `AGENTS.md` | Long-lived project notes fed back into the planner's prompt |
| `facts.json` | What this project's agents have established, and why they believe it |

**Volatile — regenerated every run, and gitignored for you:**

| File | What it is |
|---|---|
| `run/ledger.json` | The tasks, their status, and why anything stopped |
| `run/logs/verify.log` | The verification gate's full output from the last check |
| `run/checkpoints.db` | Conversation checkpoints |
| `run/logs/permissions.jsonl` | One line per gated decision — what was allowed, denied, or approved |
| `run/logs/tests.log` | The full output of the last test run, in case the summary cut something you need |
| `run/artifacts/` | Oversized tool results and offloaded conversation history |

Rudra writes a `.rudra/.gitignore` covering only the volatile half, so committing `.rudra/` is safe by default. It never edits your project's own `.gitignore` — whether you commit any of it is your call.

The whole folder is safe to delete; Rudra recreates what it needs.

---

## Where next

- **[Configuration](02-configuration.md)** — different models for planning and coding, and every other setting
- **[Choosing a Model](03-providers.md)** — which model to use and how to connect it
- **[How It Works](05-how-it-works.md)** — what's happening under the hood
- **[Project Status](08-project-status.md)** — what to expect and what not to
