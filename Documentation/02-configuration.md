# Configuration

Rudra reads configuration from five places. Later ones override earlier ones:

| # | Layer | Where |
|---|---|---|
| 1 | Built-in defaults | shipped in the package |
| 2 | User config | `~/.config/rudra/config.toml` (or `$XDG_CONFIG_HOME/rudra/config.toml`) |
| 3 | Project config | `<project>/.rudra/config.toml` |
| 4 | Environment | `RUDRA_*` variables, including anything in the project's `.env` |
| 5 | Command-line flags | `--verbose`, `--auto`, … |

You never have to remember that table, because Rudra will tell you:

```bash
rudra config list
```

Every value comes back with the layer that set it. When a setting isn't doing
what you expect, that column is the answer.

## Getting started

```bash
rudra init          # writes a commented .rudra/config.toml
```

Edit the file, then check it:

```bash
rudra models test   # is the model reachable, and can it call tools?
rudra doctor        # everything else
```

`rudra init --global` writes the user-level file instead. `--force` overwrites
an existing one.

## The file

```toml
[model.default]
# ollama | openai_compatible | anthropic | openai | google
provider    = "ollama"
base_url    = "http://localhost:11434"
model       = "qwen3:32b"
temperature = 0.3

# The NAME of an environment variable holding your key — never the key itself.
# api_key_env = "OPENROUTER_API_KEY"

# Your model's real context window. Without it, history compaction never
# triggers on local models, and Ollama silently truncates at 4096.
# context_tokens = 32768

[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
verbose = true

[permissions]
mode  = "ask"    # ask | auto | plan
allow = []
deny  = []

[compat]
task_anchor   = false
sandbox_paths = false
```

### Roles inherit

Anything a role omits comes from `[model.default]`. So this:

```toml
[model.default]
provider = "openai_compatible"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
temperature = 0.3

[model.coder]
model = "qwen/qwen3-coder-32b"
```

gives the coder its own model and the shared provider, URL, key, and
temperature — no repetition.

Role names are not a fixed list. You can write `[model.reviewer]` today; it
will be picked up when the reviewer role ships. Unknown roles fall back to
`[model.default]`, so nothing breaks in the meantime.

### API keys are never in the file

`api_key_env` names an environment variable. Rudra reads that variable at run
time and never stores, logs, or prints its value. Put the key in your
environment or in `.env` (which is gitignored), not in `config.toml`.

## Environment variables

Every setting has an environment equivalent, and environment beats both config
files:

| Variable | Sets |
|---|---|
| `RUDRA_MODEL` | `model.default.model` |
| `RUDRA_PROVIDER` | `model.default.provider` |
| `RUDRA_BASE_URL` | `model.default.base_url` |
| `RUDRA_API_KEY_ENV` | `model.default.api_key_env` |
| `RUDRA_TEMPERATURE` | `model.default.temperature` |
| `RUDRA_CONTEXT_TOKENS` | `model.default.context_tokens` |
| `RUDRA_MAX_OUTPUT_TOKENS` | `model.default.max_output_tokens` |
| `RUDRA_TIMEOUT` | `model.default.timeout` |
| `RUDRA_VERBOSE` | `agent.verbose` |
| `RUDRA_PERMISSIONS_MODE` | `permissions.mode` |

Prefix with a role for a per-role override: `RUDRA_PLANNER_MODEL`,
`RUDRA_CODER_BASE_URL`. A role you declared in TOML works too —
`[model.reviewer]` makes `RUDRA_REVIEWER_MODEL` live.

### Which `.env` gets read

The one in the project directory. If you pass `--project-dir /path/to/proj`,
Rudra reads `/path/to/proj/.env` and `/path/to/proj/.rudra/config.toml` — not
whatever happens to sit in the directory you ran the command from.

A real environment variable always beats `.env`.

## Reading configuration back

```bash
rudra config list                        # everything, with sources
rudra config list --role planner         # just one role
rudra config get model.planner.model     # one value and where it came from
```

There is **no `rudra config set`**. The standard library can read TOML but not
write it, and every available writer either destroys the comments `rudra init`
puts in the file or adds a dependency for something your editor already does
well. Edit the file directly.

## Errors

A broken config fails immediately and tells you where:

```
Configuration error: /path/.rudra/config.toml: invalid TOML — Expected ']' (at line 2, column 1)
Configuration error: Unknown key 'tempreature' in [model.default] (/path/.rudra/config.toml). Did you mean 'temperature'?
Configuration error: Unknown provider 'banana' in [model.default]. Valid providers: anthropic, google, ollama, openai, openai_compatible.
```

Unknown keys are errors rather than warnings on purpose: the usual cause is a
typo, and quietly ignoring one means your setting never applies and nothing
says so.

Sections that aren't supported yet say when they will be:

```
Configuration error: [skills] is not supported yet — arrives in Step 11 (C5.1).
```

## Permissions — read this before relying on it

`[permissions]` parses, validates, and is visible to `rudra config get`. **It
is not enforced.** Rudra currently writes and overwrites files without asking,
whatever `mode` says. `--auto`, `--yolo`, and `--plan` set the value and print
a notice saying the same thing.

This is stated on every run and in `rudra doctor` rather than buried here,
because a setting reading `mode = "ask"` otherwise reads as a safety guarantee
that does not exist. Enforcement, diff previews, and approval prompts arrive
together with shell execution.

Until then: review what Rudra writes, and work on a branch or a clean tree.

## Compatibility flags

`[compat]` holds two workarounds built for small models, both **off** by
default. The supported minimum is a 32B model, which does not need them.

- `task_anchor` — re-injects the task into the system prompt on every model
  call. Can help on very long runs; costs tokens on every call.
- `sandbox_paths` — strips prefixes like `/workspace/` and `/tmp/` from paths
  the model emits. Off by default because those are also perfectly ordinary
  directories, and stripping them rewrites paths you meant.

Markdown-fence stripping and `filename` → `file_path` correction are always
on. They are not compatibility shims — without them, models that wrap file
contents in code fences write broken files.

## The `.rudra/` directory

```
.rudra/
  .gitignore      written by Rudra, covers only this directory
  config.toml     ← durable, safe to commit
  AGENTS.md       ← durable
  project.json    ← durable
  memory/export/  ← durable
  memory/palace/    volatile, ignored
  run/              volatile, ignored
    PLAN.md, current_task.md, tech_stack.md, checkpoints.db, logs/
```

Everything under `run/` is regenerated on every run. The durable files are
worth keeping, so committing `.rudra/` is safe by default — Rudra writes
`.rudra/.gitignore` to make it so, and never touches your project's own
`.gitignore`. Whether you commit it is your call.
