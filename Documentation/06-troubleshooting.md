# 6. Troubleshooting

Real error messages and what to do about them.

- [Start here](#start-here)
- [Installation problems](#installation-problems)
- [Configuration problems](#configuration-problems)
- [Connection problems](#connection-problems)
- [Model behaviour problems](#model-behaviour-problems)
- [Problems during a run](#problems-during-a-run)
- [Still stuck?](#still-stuck)

---

## Start here

Nine times out of ten this identifies the problem in one step:

```bash
rudra models test
```

Read the row left to right. The first column that isn't `ok` is your answer:

| First failing column | Problem is in |
|---|---|
| **Construct** | Your configuration — no network involved yet |
| **Reach** | Connection, URL, key, or model name |
| **Tools** | The model itself — it can't call tools |

---

## Installation problems

### `command not found: rudra`

Rudra isn't on your PATH.

**pipx:**
```bash
pipx ensurepath
source ~/.bashrc          # or restart the terminal
pipx list                 # rudra should be listed
```

**venv** — you need to activate it in each new terminal:
```bash
source /path/to/Rudra/.venv/bin/activate
```

**uv** — either activate, or prefix commands:
```bash
uv run rudra --version
```

### `rudra --version` prints `0.0.0+unknown`

The package isn't properly installed — Rudra reads its version from installed metadata. Reinstall:

```bash
pipx install -e . --force     # or: pip install -e .   /   uv sync
```

### `error: externally-managed-environment`

Debian, Ubuntu, and recent Fedora block `pip install` into system Python. Use pipx:

```bash
sudo apt install pipx
pipx ensurepath
source ~/.bashrc
cd /path/to/Rudra
pipx install -e .
```

Or use a virtualenv, which needs no `sudo` at all.

**Never run `sudo pip install`** — it can break system packages that depend on your OS Python.

### Code edits aren't taking effect

You installed non-editable. Check:

```bash
pip show rudra | grep -i editable      # want: Editable project location: ...
pipx list --verbose | grep -i editable
```

Fix by reinstalling with `-e`:

```bash
pipx install -e . --force
```

---

## Configuration problems

### `Unknown provider 'xyz'. Valid providers: anthropic, google, ollama, openai, openai_compatible.`

Typo in `RUDRA_PROVIDER`. It must be exactly one of those five.

Note that most services speaking OpenAI's API — OpenRouter, vLLM, LM Studio, Groq, Together — want `openai_compatible`, not `openai`. Only OpenAI itself uses `openai`.

### `Role 'planner' needs an API key, but environment variable 'OPENROUTER_API_KEY' is not set.`

Rudra found the variable *name* in your config but no value in the environment.

```bash
echo $OPENROUTER_API_KEY        # probably empty
```

Add it to `.env` in your project:

```bash
OPENROUTER_API_KEY=sk-or-v1-your-actual-key
```

Or export it from your shell profile.

**The classic mix-up** — putting the key where the name goes:

```bash
# WRONG — this is the key, not a variable name
RUDRA_API_KEY_ENV=sk-or-v1-abc123

# RIGHT — two separate lines
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY
OPENROUTER_API_KEY=sk-or-v1-abc123
```

With the wrong form, Rudra looks up an environment variable literally named `sk-or-v1-abc123`, finds nothing, and reports it missing.

### A setting isn't taking effect

Stop guessing — ask:

```bash
rudra config list
```

The `Source` column names the layer that set each value: `builtin`, `user`, `project`, `env`, or `cli`. If it says `env` and you expected your config file, an environment variable is winning. That's by design; later layers override earlier ones.

```
built-in  →  ~/.config/rudra/config.toml  →  .rudra/config.toml  →  RUDRA_* env  →  CLI flags
```

Find a stray variable with:

```bash
env | grep RUDRA_
```

### My `.env` or `config.toml` is being ignored

Both are read from the **project directory** — the folder you're in, or whatever `--project-dir` names. Not your home directory, not the Rudra source folder.

```bash
rudra doctor --offline
```

It prints which config files it found and which `.env` it read. If a path there isn't the one you edited, that's the answer.

### `Configuration error: ...` on startup

Rudra refuses to run on a config it can't trust, and names the problem:

| Message | Cause |
|---|---|
| `invalid TOML — Expected ']' (at line 2...)` | Syntax error; the line number is real |
| `Unknown key 'tempreature' ... Did you mean 'temperature'?` | Typo. Unknown keys are fatal on purpose — silently ignoring one means your setting never applies and nothing says so |
| `Unknown provider 'banana'` | Check the spelling against the five valid names it lists |
| `[skills] is not supported yet — arrives in Step 11` | A section that's designed but not built. Remove it for now |

### Settings from an old guide do nothing

`MAX_AGENTS`, `MAX_ITERATIONS`, `CHECKPOINT_INTERVAL`, `TAVILY_API_KEY`, `USE_DUCKDUCKGO` were removed and are ignored. See [Configuration](02-configuration.md).

`OLLAMA_*` variables still work but print a deprecation warning naming their `RUDRA_*` replacement. Same for a bare `VERBOSE`, which is now `RUDRA_VERBOSE` — unprefixed, it collided with CI systems and build tools that set it for unrelated reasons.

### Rudra overwrote a file without asking

It will. There is no approval gate yet, and `[permissions] mode` is parsed but **not enforced** — `rudra doctor` says so in as many words. Work on a branch or a clean tree until shell support and permissions land together.

---

## Connection problems

### `Connection refused` on Ollama

The server isn't running:

```bash
ollama serve                          # separate terminal
curl http://localhost:11434/api/tags  # should return JSON
```

Check your URL has no trailing slash and no `/v1` — Ollama wants a bare `http://localhost:11434`.

### `model "qwen3:32b" not found, try pulling it first`

```bash
ollama pull qwen3:32b
ollama list                # names must match exactly, tag included
```

### `Error code: 401` / `Incorrect API key provided`

The key is wrong, expired, or revoked. Regenerate it at your provider and update `.env`.

Also check you're pointing at the right service — an OpenRouter key sent to OpenAI's endpoint gives exactly this.

### `Error code: 404` on a hosted provider

Usually the `base_url`. These need the `/v1` suffix:

| Service | Correct `base_url` |
|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` |
| vLLM | `http://localhost:8000/v1` |
| LM Studio | `http://localhost:1234/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Ollama | `http://localhost:11434` — **no** `/v1` |

Or the model name is wrong. Copy it exactly from the provider's list.

### `Error code: 429 - Rate limit exceeded: free-models-per-day`

You've spent the day's free allowance on OpenRouter. Options: wait for the reset, add a small credit balance (the message says how much unlocks a larger daily cap), or switch to a paid model.

Not a Rudra problem — your configuration is fine, which is why **Construct** still shows `ok`.

### `502 - Upstream error: ResourceExhausted: Worker local total request limit reached`

The upstream provider behind a free model is momentarily saturated. Usually transient — retry, often successfully on the second attempt.

Full agent runs make far more calls than `rudra models test` does, so you may see `models test` pass and a real run fail. For dependable work, use a paid model or a local one.

### `404 ... /responses` on a self-hosted server

You set `RUDRA_PROVIDER=openai`, which enables OpenAI's Responses API. vLLM, LM Studio, Groq, and Together only serve `/chat/completions`.

```bash
RUDRA_PROVIDER=openai_compatible
```

---

## Model behaviour problems

### `Model 'x' reports tool_calling=False`

The model can't call tools, and Rudra can't work without that. Pick a different one — `rudra models test` will confirm.

### Tools column fails, everything else passes

The model answered but never emitted a tool call. Usually a base or instruct-tuned model without tool support, or one below the 32B floor. Try `qwen3:32b` locally, or a known tool-capable hosted model.

### Output is poor, plans are nonsense, files are half-written

Almost always model size. **32B is the minimum.** Below that, plans come out malformed and files land incomplete — Rudra removed the workarounds that used to paper over this.

Also try lowering the temperature:

```bash
RUDRA_TEMPERATURE=0.1
```

### The agent forgets its own plan mid-run

Context truncation. On Ollama especially:

```bash
RUDRA_CONTEXT_TOKENS=32768
```

Ollama allocates **4096 tokens** by default and silently truncates past that — no error, just an agent that loses the thread. Setting this raises the allocation, and it also tells Rudra when to compact its history.

Confirm it took effect in the `Ctx` column of `rudra models test`.

---

## Problems during a run

### A traceback appears and Rudra exits, but files were written

A model call failed partway through — rate limit, dropped connection, upstream error. Rudra has no retry yet, so any such error ends the run and exits `1` even though earlier work succeeded.

Check before assuming nothing happened:

```bash
cat .rudra/run/PLAN.md      # ticked items were genuinely written
ls
```

Re-running continues from the unticked items.

### `2/2 files generated` but a file I asked for is missing

Rudra plans by filename, and `PLAN.md` entries that don't parse as file paths are silently dropped — and the count reports only what survived. A plan item written as prose rather than a filename disappears without a trace.

Check the plan, then ask again naming the file explicitly:

```bash
rudra "also write tests/test_parser.py covering the CSV edge cases"
```

### The generated code doesn't run

Expected, unfortunately. Rudra currently counts a file as done **when it exists**, not when it works — there's no test or review step yet. Always review the output.

This is the next major piece of work. See [Project Status](08-project-status.md).

### `--dry-run` did nothing

Correct — it isn't implemented. It exits immediately without planning or previewing.

### First run is slow

Rudra imports a large dependency tree, and your model may need loading into memory. Later runs are quicker.

---

## Still stuck?

Gather this before opening an issue:

```bash
rudra --version
rudra models test
python3 --version
env | grep RUDRA_ | sed -E 's/(KEY=).*/\1***/'      # masks any key values
```

Then open an issue at [github.com/archish9/Rudra/issues](https://github.com/archish9/Rudra/issues).

**Never paste an API key** into an issue, log, or screenshot. The command above masks anything ending in `KEY=`, but check the output before posting.

---

**Next:** [Project Status](08-project-status.md) · [Configuration](02-configuration.md)
