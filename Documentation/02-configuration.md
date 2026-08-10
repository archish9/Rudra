# 2. Configuration

Rudra is configured entirely through environment variables, usually written into a `.env` file in your project.

- [The short version](#the-short-version)
- [Where settings come from](#where-settings-come-from)
- [Every setting](#every-setting)
- [Different models for different jobs](#different-models-for-different-jobs)
- [API keys](#api-keys)
- [Context window](#context-window)
- [Which `.env` gets read](#which-env-gets-read)
- [Older `OLLAMA_*` variables](#older-ollama-variables)
- [Checking what Rudra sees](#checking-what-rudra-sees)

---

## The short version

```bash
cp .env.example .env
```

Edit three lines and you're done:

```bash
RUDRA_PROVIDER=ollama
RUDRA_BASE_URL=http://localhost:11434
RUDRA_MODEL=qwen3:32b
```

Confirm with `rudra models test`.

> TOML configuration files (`.rudra/config.toml`, `~/.config/rudra/config.toml`) are designed but **not implemented yet**. Environment variables are the only configuration surface today. See [Project Status](08-project-status.md).

---

## Where settings come from

Later wins:

1. **Built-in defaults** — Ollama on `localhost:11434` with `qwen3:32b`
2. **`.env`** in your project directory
3. **Real environment variables** — `export RUDRA_MODEL=...` beats anything in `.env`

That last rule is deliberate. A `.env` is a convenience, and you can always override it for one run without editing anything:

```bash
RUDRA_MODEL=qwen3-coder:32b rudra "refactor the parser"
```

---

## Every setting

| Variable | Default | What it does |
|---|---|---|
| `RUDRA_PROVIDER` | `ollama` | Which kind of backend: `ollama`, `openai_compatible`, `openai`, `anthropic`, `google` |
| `RUDRA_MODEL` | `qwen3:32b` | The model name, exactly as your provider spells it |
| `RUDRA_BASE_URL` | `http://localhost:11434` | Where the server lives. Leave unset for hosted providers with a fixed endpoint |
| `RUDRA_API_KEY_ENV` | *(unset)* | **Name** of the environment variable holding your key — never the key itself |
| `RUDRA_TEMPERATURE` | `0.3` | Creativity. Low is better for code |
| `RUDRA_MAX_OUTPUT_TOKENS` | `131072` | Cap on how much the model may write in one reply |
| `RUDRA_CONTEXT_TOKENS` | *(unset)* | Size of the context window — see [below](#context-window) |
| `RUDRA_TIMEOUT` | `300` | Seconds to wait for a reply. Ignored on Ollama, which has no timeout setting |
| `VERBOSE` | `true` | Show detailed agent output. `false` to quieten it |

Each of the model settings can also be set **per role** — read on.

---

## Different models for different jobs

Rudra uses two roles:

- **planner** — reads your request, decides which files are needed, writes the instructions
- **coder** — takes one instruction and writes one file

By default both use the same model. Prefix any setting with `PLANNER_` or `CODER_` to split them:

```bash
# shared defaults
RUDRA_PROVIDER=ollama
RUDRA_BASE_URL=http://localhost:11434
RUDRA_TEMPERATURE=0.3

# a reasoning model to plan with
RUDRA_PLANNER_MODEL=qwen3:32b

# a code-specialised model to write with
RUDRA_CODER_MODEL=qwen3-coder:32b
RUDRA_CODER_TEMPERATURE=0.1
```

Roles can even use different providers — plan on a hosted model, write locally:

```bash
RUDRA_PLANNER_PROVIDER=openai_compatible
RUDRA_PLANNER_BASE_URL=https://openrouter.ai/api/v1
RUDRA_PLANNER_MODEL=anthropic/claude-sonnet-4-5
RUDRA_PLANNER_API_KEY_ENV=OPENROUTER_API_KEY

RUDRA_CODER_PROVIDER=ollama
RUDRA_CODER_BASE_URL=http://localhost:11434
RUDRA_CODER_MODEL=qwen3-coder:32b

OPENROUTER_API_KEY=sk-or-v1-...
```

The full per-role set: `RUDRA_PLANNER_PROVIDER`, `_MODEL`, `_BASE_URL`, `_API_KEY_ENV`, `_TEMPERATURE`, `_CONTEXT_TOKENS`, `_MAX_OUTPUT_TOKENS`, `_TIMEOUT` — and the same with `CODER_`.

A role with nothing set falls back to the bare `RUDRA_*` value. Role names Rudra doesn't know yet also fall back, so future roles work without configuration changes.

---

## API keys

**Rudra never stores an API key in configuration.** Config names a variable; the value is read from your environment at the moment it's needed.

```bash
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY     # the name
OPENROUTER_API_KEY=sk-or-v1-abc123...    # the value
```

Why bother? Because config files get committed, pasted into issues, and shared in screenshots. A variable name is harmless in all three.

The name is yours to choose. Several providers at once is fine:

```bash
RUDRA_PLANNER_API_KEY_ENV=ANTHROPIC_API_KEY
RUDRA_CODER_API_KEY_ENV=OPENROUTER_API_KEY

ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-v1-...
```

You can also keep keys out of `.env` entirely and export them from your shell profile or a secret manager — Rudra reads the environment either way.

`.env` is listed in `.gitignore`, so it is not committed. If you're using Ollama locally, you need no key at all.

---

## Context window

`RUDRA_CONTEXT_TOKENS` tells Rudra how much context your model can hold. It does **two** things, which is why it's worth setting:

1. **Tells Rudra when to compact.** As a conversation grows, Rudra summarises older parts to stay within budget. Without this setting it assumes a large default and may never compact at all — so a long session can overflow.
2. **On Ollama, sizes the server's window too.** Ollama allocates **4096 tokens by default** and silently truncates anything beyond that, no error, no warning. Setting this raises the allocation to match.

```bash
RUDRA_CONTEXT_TOKENS=131072
```

Use the real number for your model. Hosted providers usually report their own, so you can leave it unset; local models generally don't, so setting it is worthwhile.

Check what took effect with the `Ctx` column of `rudra models test`.

---

## Which `.env` gets read

Rudra reads `.env` from the **project directory** — the folder you're in, or whatever `--project-dir` points at.

```bash
cd ~/projects/api && rudra "add tests"       # reads ~/projects/api/.env
rudra "add tests" -d ~/projects/api          # also reads ~/projects/api/.env
```

That second case matters: run from your home directory with `-d`, and Rudra still reads the *project's* `.env`, not your home folder's.

Different projects can therefore use different models with no global state.

---

## Older `OLLAMA_*` variables

Earlier versions used `OLLAMA_*` names. They still work for one more release and print a deprecation warning naming the replacement.

| Old | New |
|---|---|
| `OLLAMA_BASE_URL` | `RUDRA_BASE_URL` |
| `OLLAMA_MODEL` | `RUDRA_MODEL` |
| `OLLAMA_MODEL_PLANNER` | `RUDRA_PLANNER_MODEL` |
| `OLLAMA_MODEL_CODER` | `RUDRA_CODER_MODEL` |
| `OLLAMA_TEMPERATURE` | `RUDRA_TEMPERATURE` |
| `OLLAMA_TIMEOUT` | `RUDRA_TIMEOUT` |
| `OLLAMA_NUM_PREDICT` | `RUDRA_MAX_OUTPUT_TOKENS` |

If both are set, the `RUDRA_*` one wins. Migrate when convenient; they'll be removed.

Settings from even older versions — `MAX_AGENTS`, `MAX_ITERATIONS`, `CHECKPOINT_INTERVAL`, `TAVILY_API_KEY`, `USE_DUCKDUCKGO` — no longer exist and are ignored.

---

## Checking what Rudra sees

Fastest answer:

```bash
rudra models test
```

The table shows the provider, model, and context window actually in effect for each role. If it doesn't match what you expected, something is overriding it — most often a real environment variable beating your `.env`:

```bash
env | grep RUDRA_
```

---

**Next:** [Choosing a Model](03-providers.md) for provider-specific setup, or [Troubleshooting](06-troubleshooting.md) if something isn't connecting.
