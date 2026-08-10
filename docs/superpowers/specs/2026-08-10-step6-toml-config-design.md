# Step 6 — Layered TOML Configuration: Design

**Date:** 2026-08-10
**Ledger rows:** `C2.1`–`C2.5`, plus riders `C1.8` (deferred here by Step 5) and `C0.9` (pulled forward from Stage I)
**Depends on:** Step 5 (`C1.1`–`C1.7`, model factory) — DONE
**Unblocks:** Step 7 (`C3.1`–`C3.4`, `U.7` — shell + permissions), and every later phase needing a config surface

---

## 1. Goal

Replace Rudra's environment-variable-only configuration with a five-layer loader whose top layer is TOML, so a project can declare its models, permissions, and compatibility flags in a file that lives beside the code and is committed with it.

The load order, later overriding earlier (D1, `CLAUDE.md` §6):

1. Built-in defaults, shipped in the package
2. `~/.config/rudra/config.toml` — user global
3. `<project>/.rudra/config.toml` — project
4. `RUDRA_*` environment variables (including `.env`)
5. CLI flags

## 2. Owner decisions taken during design

Recorded here because three of them narrow or move ledger scope, and a future session reading only `TODO.md` would otherwise read the deviation as an oversight.

| # | Decision | Effect on the ledger |
|---|---|---|
| S6.1 | **`rudra config` is read-only — `get` and `list`, no `set`** | **Narrows `C2.3`**, whose text says `get/set/list`. `tomllib` is stdlib but read-only; a writer means a new dependency, and neither `tomli-w` (drops comments) nor `tomlkit` (heavier) earns its place when `rudra init` scaffolds the file and an editor edits it |
| S6.2 | **`C0.9` (the D15 `.rudra/` layout) lands in Step 6** | Moves `C0.9` out of Stage I. Step 6 is the first thing to write `.rudra/config.toml`, so the durable/volatile split has to exist now or Steps 7–9 each add files to a tree with no policy |
| S6.3 | **Permission config surface ships; enforcement does not. Non-enforcement is stated in the UI, not just the docs** | `C2.5` splits across Steps 6 and 7. See §6 |
| S6.4 | **The `OLLAMA_*` deprecation shim survives Step 6** | `C1.3` promised "one release". Step 6 already moves the config surface underneath users; stacking an env-var removal on the same release is two migrations at once. Remove at 0.3.0 or in Step 7 |

## 3. Architecture

### 3.1 The chosen shape: merge dicts, then build once

Each layer is a function returning a plain nested `dict`. The layers are deep-merged in order, recording which layer set each leaf. The merged dict is validated once, then frozen dataclasses are constructed from it.

```
builtin  ─┐
user.toml─┤
proj.toml─┼─►  deep_merge  ─►  {values, provenance}  ─►  validate  ─►  Config
env      ─┤
cli flags─┘
```

**Rejected: extending today's resolver chain.** `_resolve_model_config` currently reads `env(role) or env(bare) or legacy or default` per field. Adding TOML makes that five branches per field across ~15 fields, restates precedence in every one, and can never answer *which layer won*.

That last point is the deciding one, not elegance. Two of the three configuration defects this project has shipped — `A5.1` (a `.env` above the package leaking into every project) and `A5.2` (`--project-dir` ignored when choosing `.env`) — were both unanswerable "where did this value come from" questions. Provenance is the feature; dict-merging gives it away free, and per-field resolver chains structurally cannot.

**Rejected: a settings library.** `pydantic-settings` does not express "project file beats user file beats builtin" without custom sources anyway; `dynaconf` is a large dependency for a five-layer merge. Both bury the precedence rule inside a third party at exactly the moment the goal is making it inspectable.

### 3.2 Modules

```
src/rudra/config/
  __init__.py   public surface: get_config, reset_config, Config, ModelConfig,
                PermissionsConfig, CompatConfig, AgentConfig
  schema.py     frozen dataclasses, DEFAULTS (layer 1), RESERVED_SECTIONS
  layers.py     the five readers; each returns a dict; no precedence logic
  loader.py     deep_merge, provenance, validate, construct
src/rudra/state/paths.py
                D15 layout constants, RudraPaths, ensure_layout(), gitignore writer
```

`config.py` becomes the `config/` package. `state/paths.py` rather than `config/paths.py`: `state/` already owns `.rudra/` (`project_config.py:39`, `checkpoint.py`), and configuration *consumes* the layout rather than defining it. This mirrors Step 5's split of pure data (`llm/providers.py`) from composition (`llm/factory.py`).

**Dependency direction:** `schema.py` imports nothing from Rudra. `layers.py` imports `schema` and `state.paths`. `loader.py` imports both. Nothing under `config/` imports `agent/`, `llm/`, or `cli.py`.

### 3.3 Public surface is preserved

`get_config(project_root)`, `reset_config()`, `Config.model_for(role)`, and `ModelConfig`'s field names stay exactly as Step 5 left them. Every existing caller — `llm/factory.py`, `llm/probe.py`, `cli.py`, the agent modules — continues to work unmodified. This is what keeps the 187-test suite meaningful as a regression net across a rewrite of the module's internals.

## 4. Schema

```toml
[model.default]                      # every role inherits from this
provider    = "ollama"               # ollama | openai_compatible | anthropic | openai | google
base_url    = "http://localhost:11434"
model       = "qwen3:32b"
api_key_env = "RUDRA_API_KEY"        # the NAME of an env var, never a key (C1.5)
temperature = 0.3
context_tokens    = 131072
max_output_tokens = 131072
timeout     = 300

[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
verbose = true

[permissions]
mode  = "ask"                        # ask | auto | plan
allow = []
deny  = []

[compat]                             # C1.8 — both default off per D4
task_anchor   = false
sandbox_paths = false
```

**Role names are not enumerated.** `[model.reviewer]` is accepted and stored today, and is picked up the moment Step 9 calls `build_model("reviewer")` — consistent with `Config.model_for`'s existing fallback to `default` for unknown roles. Only the keys *within* a role are validated.

**Unknown keys are a hard error.** The common case is a typo (`[permisions]`, `tempreature`), and silently ignoring one means the user's setting never applies with no signal at all. The error names the file, the key, and the nearest valid match.

**Reserved sections get a better error than "unknown".** `CLAUDE.md` §6 advertises `[skills]`, `[memory]`, and `[tools]`, which arrive in Steps 11, 14, and 7. `RESERVED_SECTIONS` maps each to the step that implements it, producing *"`[skills]` is not supported yet — arrives in Step 11"*. `CLAUDE.md` §6 gains a note marking which sections are live today.

## 5. Precedence semantics

**Merging is per-leaf, not per-table.** A project-level `[model.planner] model = "x"` must not erase a user-level `[model.planner] temperature = 0.5`.

**Environment variables map onto the same nested shape** so layer 4 is an ordinary dict like the others:

| Variable | Path |
|---|---|
| `RUDRA_MODEL` | `model.default.model` |
| `RUDRA_PLANNER_MODEL` | `model.planner.model` |
| `RUDRA_CODER_BASE_URL` | `model.coder.base_url` |
| `RUDRA_VERBOSE` | `agent.verbose` |
| `RUDRA_PERMISSIONS_MODE` | `permissions.mode` |

`RUDRA_PLANNER_MODEL` is genuinely ambiguous — role `planner` + suffix `MODEL`, or a suffix named `PLANNER_MODEL`? Resolved by matching a known-roles list first, preserving today's behavior in `config.py:72-78`. The known-roles list is the builtin set **union the roles present in the merged TOML**, so declaring `[model.reviewer]` makes `RUDRA_REVIEWER_MODEL` work with no code change.

### Three ordering rules, each a live bug class

1. **`--project-dir` is not a configuration value.** It selects *which* configuration to load, so it is resolved before any layer is read. This is `A5.2` again and strictly worse: the flag now picks the project TOML, not only the `.env`. `get_config()` still caches for the process, so `cli.py` must keep resolving `project_path` before its first `get_config()` call (`cli.py:308-309`).
2. **Role inheritance runs after the cross-layer merge**, never inside a layer. Applying it per-layer means a project `[model.planner]` fails to inherit a user-level `[model.default]`.
3. **`.env` continues to feed layer 4 only.** Read from `project_root/.env` with `override=False`, so a real environment variable still beats `.env` — unchanged from `A5.1`'s fix (`config.py:169`).

### One new deprecation

`AgentConfig` reads a bare, unprefixed `VERBOSE` (`config.py:130`), which collides with other tools. `RUDRA_VERBOSE` replaces it; `VERBOSE` keeps working behind the same warn-once shim `OLLAMA_*` uses (`config.py:56-69`), and is listed for removal alongside them.

## 6. Permissions: config without enforcement

Enforcement is Step 7 (`C3.3`, `U.7`). Step 6 ships only the surface. The design problem is that D5 locks the default to `ask`, and **a setting reading `mode = "ask"` tells the user Rudra will prompt before touching files. It will not** — today it overwrites silently (`A1.16`).

The asymmetry matters: an inert `--yolo` is harmless because it claims *less* safety than the user gets. An inert default of `ask` claims *more*. So the notice belongs on the default path, not only on the flags:

- The run panel — which already prints Planner and Coder (`cli.py:322-331`) — gains `permissions: ask — NOT ENFORCED (Step 7); files are written and overwritten without prompting`
- `rudra doctor` carries the same as an explicit row
- Passing `--auto` / `--yolo` / `--plan` prints a one-time notice that the mode was recorded and nothing consumes it yet

`allow` and `deny` parse and shape-validate as lists of strings; nothing reads them, and `doctor` says so. `--plan` is inert on the same terms — plan mode is `C6.9`, Step 10.

This is `A1.40`'s lesson applied before the fact rather than after: a flag or setting implying behavior it does not have is worse than its absence.

## 7. The D15 `.rudra/` layout (C0.9)

```
.rudra/
  .gitignore        written by Rudra, scoping only its own directory
  config.toml       durable
  AGENTS.md         durable
  project.json      durable   (becomes facts.json at C6.8a)
  memory/export/    durable   (Step 14)
  memory/palace/    volatile  [ignored]
  run/
    checkpoints.db  volatile  [ignored]
    PLAN.md         volatile  [ignored]
    current_task.md volatile  [ignored]
    tech_stack.md   volatile  [ignored]
    logs/           volatile  [ignored]
```

`.rudra/.gitignore` contains `run/` and `memory/palace/`. Rudra never edits the project's root `.gitignore` — whether `.rudra/` is committed stays the user's decision (D15, §0.7).

### Migration is nearly free — the finding that makes this cheap to pull in

Everything that moves is regenerated on every run: `PLAN.md`, `current_task.md`, `tech_stack.md`, `logs/`. `checkpoints.db` moves too but is **never resumed** (`A1.2`), so it is disposable — the new path is simply written, the old file is left in place (deleting is destructive and buys nothing), and `doctor` reports the stale one.

**Nothing durable moves.** `AGENTS.md` and `project.json` already sit at the durable root. There is therefore **no migration script** — only new write paths.

### The coupling that will break a run if missed

The agent-facing prompts name these paths as literal strings, and `update_plan`'s docstring instructs the model to `edit_file` the exact path (this is `A1.25`'s evidence). A grep sweep for `.rudra` literals across `src/` and `tests/` returns 35 hits; the ones that must change with the code, in the same commit:

| File | Nature |
|---|---|
| `src/rudra/tools/planning_tools.py:4,14,43,53,57,116,121,147,164,168,193` | Path construction **and** agent-facing docstrings |
| `src/rudra/agent/coder_agent.py:11,17,20,21` | Prompt text naming `current_task.md` and `tech_stack.md` |
| `src/rudra/agent/main_agent.py:340,488,556,567` | Prompt text, `tech_stack.md` writer, `plan_path=`, `rudra_dir` |
| `src/rudra/state/checkpoint.py:4,22,27` | Docstrings; `session_id.txt` is volatile → `run/` |
| `src/rudra/config.py:141` | `checkpoint_dir` |
| `src/rudra/compat/deepagents_path.py:76` | `plan_path` docstring |
| `tests/test_plan_items.py:68,77`, `tests/test_project_tree.py:93-100`, `tests/test_deepagents_path.py:41` | Assert literal paths |

Unaffected and deliberately so: `planner_agent.py:88`'s `memory=[".rudra/AGENTS.md"]` (durable, stays at root) and `filesystem/tree.py:34,38` (`.rudra` as a skip-dir name, not a path into it).

## 8. Commands

```
rudra init [--global] [--force]     scaffold a commented config.toml; create the D15 layout
rudra config list [--role R]        every effective value with the layer that set it
rudra config get <dotted.key>       e.g. model.planner.model — value and source
rudra doctor                        diagnose the setup
```

`config` becomes a sub-Typer beside `models_app` (`cli.py:64-65`). Consequence, already true of `models`: `config` becomes a reserved first token, so `rudra config the database` runs the subcommand rather than starting a task. `TaskOrCommandGroup` (`cli.py:24-51`) handles everything else.

`rudra init` is non-interactive, refuses to overwrite without `--force`, and writes a file that must parse back through the loader.

**`rudra doctor` reports only what exists today:** configuration files discovered and their precedence, which `.env` was read, `.rudra/` layout health (including a stale top-level `checkpoints.db`), per-role model reachability reusing `llm/probe.py`, the deepagents pin-versus-installed check from `compat/version_guard.py`, and the permission mode with its non-enforcement notice.

It does **not** check MCP servers, skill directories, or the MemPalace store — none exist. That deviates from `C2.3`'s text, which listed all three; those checks arrive with Steps 13, 11, and 14.

## 9. Error handling

Every failure names the file, and where TOML gives one, the line.

| Condition | Behavior |
|---|---|
| TOML syntax error | Abort naming file and line from `tomllib.TOMLDecodeError` |
| Unknown key or section | Abort naming file, key, and nearest valid match |
| Reserved future section | Abort naming the step that implements it |
| Invalid `provider` | Abort listing the five valid providers |
| Invalid `permissions.mode` | Abort listing `ask`, `auto`, `plan` |
| Out-of-range numeric | Abort naming the constraint |
| Missing user or project TOML | Not an error — the layer contributes `{}` |
| Unreadable TOML (permissions) | Abort; a config that exists but cannot be read is never silently skipped |

Error messages are asserted as strings in tests, not merely as exception types — a diagnostic that does not name the offending file is the defect this section exists to prevent.

## 10. Testing

- Precedence table-driven across all five layers; **per-leaf** merge asserted explicitly
- Role inheritance resolves after the cross-layer merge, not within a layer
- Provenance correctness — the layer `config list` names is the layer that actually won
- `--project-dir` selects the right project TOML (`A5.2`, extended from `.env` to config)
- Error quality asserted as strings for each row of §9
- `OLLAMA_*` and `VERBOSE` shims still work and still warn once
- Layout creation is idempotent; durable files present beforehand are untouched
- `rudra init` output round-trips back through the loader
- CLI smoke for `config list`, `config get`, `init`, `doctor`

### Acceptance run — a stricter bar than Step 5's

Step 5's acceptance ran from a `mktemp -d` driven by a `.env`. Step 6's must run **with no `RUDRA_*` or `OLLAMA_*` variables set at all**, configured purely by a `.rudra/config.toml`. That is the only evidence that layer 3 actually reaches the model factory.

## 11. Risks

1. **`[compat] task_anchor = false` is a behavior change, not merely configuration.** `TaskAnchorMiddleware` is wired into both agents unconditionally today (`planner_agent.py`, `coder_agent.py`); defaulting it off removes it from a live 32B run. D4 locks the default, so the acceptance run must exercise it. If output quality regresses, that is evidence for revisiting D4 — not grounds for quietly re-enabling.
2. `sandbox_paths = false` disables the sandbox-prefix half of `FixWriteParamsMiddleware`. Fence-stripping and `filename`/`path` → `file_path` aliasing stay always-on per D4.
3. Rewriting the configuration module touches every consumer. The suite must not drop below 187 passing.
4. `A1.39` — transient provider errors kill a whole run — will hit the live acceptance again. Budget retries; do not misread a 502 as a configuration failure.
5. Moving `PLAN.md` while an agent-facing prompt still names the old path produces a run where the coder writes where nothing reads. §7's table is the mitigation; the grep sweep is re-run at the end rather than trusted from this document.

## 12. Explicitly out of scope

Permission **enforcement** (Step 7), diff preview (`C3.4`), shell execution (`C3.1`), MCP configuration (Step 13, and see D19/§0.8), skill sources (Step 11), memory backend (Step 14). Their TOML tables are reserved with a helpful error, not implemented.
