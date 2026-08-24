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

# Your model's real context window. Rudra uses it for two things: it
# summarises the conversation at 85% of it, and offloads any single tool
# result bigger than 10% of it. Without it both fall back to defaults built
# for very large hosted models, so on a 32k model summarisation never fires
# at all and one pytest transcript can eat a third of the window. Ollama
# also silently truncates at 4096 without it.
# `rudra doctor` tells you which roles are missing it.
# See Documentation/13-context-and-memory.md.
# context_tokens = 32768

[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
verbose = false
stream_tokens = false
max_fix_attempts = 3
max_questions = 5

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

### The five roles

| Role | Used by |
|---|---|
| `default` | Anything without its own section — and the `general-purpose` subagent |
| `planner` | Analyses the task and decides which files to write |
| `coder` | Writes one complete source file at a time |
| `tester` | Writes tests, runs the suite, reports failures |
| `reviewer` | Reads the diff and reports problems. Never edits |

`tester` and `reviewer` arrived with the subagents of the same names — see
[How It Works](05-how-it-works.md). Every one of them inherits
`[model.default]`, so a single-model setup configures `[model.default]` and
nothing else.

Giving the reviewer a stronger model is the common split:

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3:32b"

[model.reviewer]
model = "qwen3:72b"      # inherits provider and base_url from default
```

Role names are still not a closed list — an unknown one falls back to
`[model.default]` rather than erroring.

`rudra models test` probes **distinct endpoints**, not roles. Five roles
pointing at one model is one row, listing all five; the split above is two.

### API keys

Two settings, and `api_key` wins where both are set:

```toml
[model.default]
api_key     = "your-key-here"        # the key itself
api_key_env = "OPENROUTER_API_KEY"   # or: the NAME of a variable holding it
```

**Put `api_key` in `~/.config/rudra/config.toml`, not in your project's
`.rudra/config.toml`.** The global file lives in your home directory and is
in no repository — the same place `~/.aws/credentials` and `~/.npmrc` keep
theirs. The project file is [documented as safe to
commit](../README.md#what-rudra-leaves-in-your-project), so a key written
there gets committed with it. Rudra warns at run start when it finds one,
rather than refusing — it is your repository and it may well be private.

Set it once in the global file and every project on the machine uses it.
That is the difference between `api_key` and `.env`: `.env` is read from the
**project root only**, so it is the right home for a secret that differs
per project and the wrong one for an LLM key that doesn't.

Use `api_key_env` instead when the key must not be in any file — it names a
variable, and you export it from your shell or a `.env`.

Either way the value is never displayed: `rudra config list` prints
`<set — value hidden>`, no error message contains it, and `ModelConfig`
declares the field `repr=False` so it cannot reach a log line or a traceback.

## Environment variables

Every setting has an environment equivalent, and environment beats both config
files:

| Variable | Sets |
|---|---|
| `RUDRA_MODEL` | `model.default.model` |
| `RUDRA_PROVIDER` | `model.default.provider` |
| `RUDRA_BASE_URL` | `model.default.base_url` |
| `RUDRA_API_KEY` | `model.default.api_key` |
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

Sections that aren't supported yet say when they will be, naming the step that
implements them. There are none left — `[tools]`, `[skills]`, `[mcp]` and
`[memory]` are all real — but the mechanism stays for the next one.

## The memory section

```toml
[memory]
backend = "chroma"    # chroma | sqlite | milvus | qdrant | pgvector
```

One key, and the two you might expect are deliberately absent.

There is no `enabled`. Long-term memory is not optional — see **[Memory](15-memory.md)**
for what it stores, how it comes back, and how to purge it.

There is no knob for how much of the context window recalled memories may use.
It is 2%, a constant, because nobody has measured what it should be — and an
unmeasured knob is worse than a constant somebody can change with evidence.
`rudra doctor` and `.rudra/run/logs/usage.json` both report what it actually
costs, which is what a future default would be argued from.

The palace's location is not configurable either: it is always
`<project>/.rudra/memory/palace/`, because that layout is what makes committing
`.rudra/` safe.

The setting that affects memory most is not in this section at all: it is
`[model.<role>] context_tokens`. A role that declares none gets no recalled memories
in its prompt — see [Memory](15-memory.md#6-how-a-memory-comes-back-recall).

## The skills section

| Key | Means |
|---|---|
| `enabled` | Which bundled skills enter an agent's prompt index |

```toml
[skills]
enabled = ["brainstorming", "test-driven-development", "systematic-debugging"]
```

Leave the section out for the shipped set of nine. Name a subset to narrow it —
including any of the five that ship disabled, since all fourteen are vendored.
Set `enabled = []` to switch skills off for every agent.

An unknown name is an error, not a warning. The skill loader silently skips a
skill it cannot find, so accepting a typo would mean a skill that never loads
and never explains why.

See [Skills](12-skills.md) for what each one does and which agents get them.

## The MCP section

Controls outside tool servers. The servers themselves live in a separate
`.mcp.json` — see [MCP](14-mcp.md).

| Key | Means |
|---|---|
| `enabled` | `false` switches MCP off entirely. Default `true` |
| `mcp_in_auto` | May an unattended run call MCP tools? Default `false` |
| `timeout` | Seconds to wait for one MCP call. Default 60 |
| `allow` | `server__tool` patterns an agent may see and use. Empty means all |
| `deny` | Patterns it may not. Beats `allow` |
| `readonly` | Which tools the reviewer subagent may see |
| `disabled_servers` | Names from `.mcp.json` to leave unloaded |

```toml
[mcp]
mcp_in_auto = false
timeout     = 120
deny        = ["*__system_bootstrap"]
readonly    = ["kala__verify", "kala__system_status"]
```

**Why servers are in a different file.** `.mcp.json` uses Claude Code's schema
so an existing config pastes in unchanged; putting Rudra-specific settings in
it would break that. Policy here, servers there.

**`[mcp] allow` is not a permission.** It decides what an agent can *see and
name*. Whether a call may run is decided by `[permissions]`, always, on top of
this.

## The agent section

| Key | Means |
|---|---|
| `verbose` | Add the model's prose and untruncated payloads to the run trace. Default `false` |
| `stream_tokens` | Stream that prose token by token as it arrives. Default `false` |
| `max_fix_attempts` | How many times the fix loop retries one task before giving up. Default 3 |
| `max_questions` | How many clarifying questions the planner may ask across one whole run. Default 5; `0` never asks |

`verbose = false` does **not** mean a silent run. Every tool call, result and
error is printed either way — verbose adds the prose and stops truncating.
`--verbose` and `--no-verbose` override this per run, and `--no-verbose` drops
to errors only. The levels are in the
[CLI reference](04-cli-reference.md#what-the-trace-shows).

> **This default changed in v0.2.1**, from `true` to `false`. Until then the
> key had no effect at all — nothing read it — so nobody was getting the
> verbose output it promised. Rather than switch every existing project to
> untruncated output the day it started working, the default now matches what
> people have actually been seeing. Set it `true` if you want the full stream.

`stream_tokens` is off because it is unmeasured against the 32B models Rudra
targets. `--stream` turns it on for one run; it changes only what the trace
shows, never what the agent does.

`max_fix_attempts` is an upper bound, not a target. The loop usually stops
sooner: two attempts that fail *identically* count as no progress and stop
immediately, because a third would produce the same result. Raising it helps
only when attempts are genuinely converging.

`max_questions` is the whole run's budget, counted in questions rather than in
calls, so batching several related questions into one prompt costs what it
should. When the budget runs out the planner is told to infer the rest. An
unattended run (`--auto`, or any run without a terminal) never asks at all —
the tool is not registered there, so nothing can hang waiting for an answer
nobody is there to give.

It must be a whole number of 1 or more. Zero would block every task without
the coder running once, which reads as a broken model rather than a config
mistake, so it is rejected at load time.

## Permissions

`[permissions]` and `[tools]` control what Rudra may do without asking. The
short version:

```toml
[permissions]
mode  = "ask"                      # ask | auto | plan
allow = ["execute:pytest*"]        # never prompt for these
deny  = ["write_file:.env"]        # never allow these

[tools]
shell         = true               # false removes the execute tool entirely
shell_in_auto = false              # may --auto run commands?
auto_branch   = false              # create a rudra/<slug> branch before a run?
test_timeout  = 600                # seconds before a test run is killed
```

`auto_branch` puts the agent's work on its own branch. It is off by default
because it changes your repository before the model has done anything, and
it only fires when that is safe: inside a git repository, with a branch
checked out, from a clean working tree. Otherwise it prints why and carries
on where you are — it never fails a run. Rudra's own `.rudra/` directory
does not count as uncommitted work.

`test_timeout` bounds a test run. The number is a real setting rather than a
constant because suites differ by orders of magnitude, and because a runner
that hangs — Angular's Karma with no browser installed, for instance —
would otherwise stall a run indefinitely rather than failing it.

`ask` is the default: every write, edit, delete and command stops for
approval and shows a diff first. `auto` approves everything. `plan` writes
the plan and touches nothing else.

Full treatment — rule syntax, the deny floor, session grants, the audit log,
and what `--auto` does and doesn't protect — is in
**[Permissions](09-permissions.md)**. Read it before an unattended run.

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

Note that `.mcp.json` sits in your **project root**, not in `.rudra/` — that is
where every MCP client looks for it, so a config written for one tool works in
another.

```
.rudra/
  .gitignore      written by Rudra, covers only this directory
  config.toml     ← durable, safe to commit
  AGENTS.md       ← durable
  facts.json      ← durable
  memory/export/  ← durable
  memory/palace/    volatile, ignored
  run/              volatile, ignored
    ledger.json, checkpoints.db
    logs/           permissions.jsonl — every gated decision
    artifacts/      oversized tool output, offloaded out of your project
```

Everything under `run/` is regenerated on every run. The durable files are
worth keeping, so committing `.rudra/` is safe by default — Rudra writes
`.rudra/.gitignore` to make it so, and never touches your project's own
`.gitignore`. Whether you commit it is your call.
