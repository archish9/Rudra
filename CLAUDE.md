# CLAUDE.md — Rudra

Context loaded into every fresh Claude Code session. Read `TODO.md` next — it is the live ledger of what is done and what is pending.

---

## 1. What Rudra Is

Rudra (रुद्र) is an **autonomous coding agent CLI** — a local-first alternative to Claude Code. It plans work, writes code, tests it, reviews it, and fixes it, while the user stays in control.

- **Language:** Python 3.12+
- **Agent framework:** [LangChain `deepagents`](https://github.com/langchain-ai/deepagents) — installed `0.4.12`, **target `0.7.4`** (upgrade is TODO Phase U, step 1). Anything you read in `.venv` is the *old* version; check `TODO.md` §F for what changed.
- **CLI:** Typer + Rich + prompt_toolkit
- **Package:** `rudra`, entry point `rudra.cli:app` (`pyproject.toml:62`)
- **License:** Apache-2.0 declared in `pyproject.toml:10`; full text on disk at `LICENSE` (added 2026-08-10, A4.2). A NOTICE file is still absent and is only needed once superpowers/MemPalace are vendored (A4.7)
- **Repo:** remote is `git@github.com:archish9/Rudra.git`, and `[project.urls]` in `pyproject.toml` matches it. Corrected 2026-08-10 (A1.41) — this line previously claimed the remote was `RudraAnvil` and that `pyproject.toml` was wrong; both halves were stale. `RudraAnvil` is the project's former name and survives only in dated `docs/superpowers/` records and the untracked-noise files A4.3 removes
- **Ships as:** open-source GitHub project

### Product goals (owner's stated requirements)
1. **Provider-agnostic.** User configures API URL + model + API key. Must work with Ollama, vLLM, OpenRouter, Anthropic, OpenAI, or anything else.
2. **MCP + Tools + Skills.** Rudra ships with some; user can add their own.
3. **Loop engineering.** Code → test → review → fix, iteratively.
4. **Plan before coding.** Dynamic clarifying questions (no static Q&A scripts), tracked progress.
5. **Ship [obra/superpowers](https://github.com/obra/superpowers)** as a default, integrated methodology layer (MIT, Jesse Vincent).
6. **Ship [MemPalace](https://github.com/MemPalace/mempalace)** as default long-term memory (MIT; `pip install mempalace`; ChromaDB/SQLite/Milvus/Qdrant/pgvector backends; exposes 36 MCP tools).
7. **Local-first**, with Claude Code-grade capability for production work.

---

## 2. Session Rules (for Claude Code, not for Rudra)

1. **Read `TODO.md` at the start of every session.** It is the single source of truth for status. §0 = locked decisions, §E = execution order (which step to do next), §F = deepagents 0.7.4 upgrade findings.
2. **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with file:line evidence first. Then fix it. Then mark `DONE`. This ordering is non-negotiable — the owner asked for it explicitly.
3. **Evidence-based only.** Every claim about the codebase must cite `file.py:line`. No assumptions, no guessing. If you cannot verify, say so.
4. **Verify before claiming done.** Run the command, show the output. `ruff check`, `pytest`, actual CLI invocation.
5. **Planning sessions produce self-contained implementation plans** that a fresh session can execute without this conversation's context. Write them under `plans/`.
6. When done with a work item, update `TODO.md` in the same commit as the code.

---

## 3. Current Architecture (as of 2026-08-13, Step 10a)

```
src/rudra/
├── cli.py                  Typer app: main callback + `models`, `config`, `init`, `doctor`
├── config/                 Layered TOML config (Step 6). schema / layers / loader / template
├── llm/                    Provider-agnostic model factory (Step 5)
├── stacks/                 Multi-language stack detection (Step 4)
├── permissions/            The gate (Step 7). One pure engine, two mechanisms:
│                           rules/floor/grants decide; middleware denies;
│                           interrupts+approval ask; diff/audit/env support it
├── shell/                  runner.py — the ONLY subprocess call site (Step 8).
│                           Renders argv, asks the engine as execute:<command>
├── git/                    core.py — Python git API (C3.5). Orchestrator-facing
├── testing/                runner.py → TestResult, parse.py counts (C3.6).
│                           Step 9c's fix loop consumes this directly
├── verify/                 The deterministic gate (Step 9a, C6.6):
│                           syntax · lint · typecheck · test · stubs.
│                           Blocking except lint. Six outcomes, not two.
│                           VerifyReport.escalate splits fix-loop input
│                           (iterate) from user action (stop). Calls
│                           run_gated and run_tests; starts no subprocess
├── subagents/              The four subagents (Step 9b): coder · tester ·
│                           reviewer · general-purpose. spec/registry/build/
│                           runner. build.py is the ONLY assembly path, so
│                           every subagent carries the gate — deepagents
│                           inherits interrupt_on but NOT middleware.
│                           The reviewer cannot write because the tools are
│                           never registered, not because a prompt says so
├── facts/                  The open fact store (Step 10a, C6.8a). store.py
│                           imports nothing from Rudra. A fact is
│                           {value, why, source}; keys are enumerated
│                           nowhere in code — validation covers types and
│                           size, never names. render.py puts them in every
│                           agent's prompt: the planner at construction,
│                           each subagent at its own build_agent call
├── context/                Context budgeting (Step 12). budget.py is pure and
│                           imports nothing from Rudra. evict_limit derives the
│                           tool-result eviction threshold from the same
│                           context_tokens C1.4a feeds to max_input_tokens, so
│                           the eviction threshold and the summarization
│                           trigger cannot drift apart (A1.47). evict_kwargs
│                           exists because omitting that argument and passing
│                           None are different: None disables eviction outright
├── loop/                   The agentic loop (Step 9c). ledger · bounds · tools ·
│                           engine. ledger.py and bounds.py import nothing from
│                           Rudra. No agent-facing tool can write DONE — only
│                           engine.py, and only on VerifyReport.passed
├── memory/                 Long-term memory (Step 14a, C8.1). store.py is the
│                           ONLY module that imports mempalace, and imports it
│                           lazily -- chromadb pulls onnxruntime, grpcio and
│                           opentelemetry, which at module scope would land on
│                           `rudra --version`. entry/taxonomy/degrade import
│                           nothing from Rudra. Every mempalace call passes
│                           palace_path, collection_name and backend
│                           explicitly, because two of the three cannot be
│                           overridden by env at all (C8.1b)
├── agent/
│   ├── main_agent.py       RudraAgent — run setup; run() delegates to run_loop,
│   │                       build_backend() → CompositeBackend(default=LocalShellBackend)
│   └── planner_agent.py    THREE staged agents (Step 10b): clarify · architect ·
│                           breakdown, one tool set each via _tools_for_stage.
│                           consult_planner + the lifted _stream_planner_turn
├── middleware/             2 survivors of D4, both opt-in behind [compat]
├── tools/                  EVERY tool the model can call, and nothing else:
│                           interaction (record_fact · ask_user) · git_tools ·
│                           testing_tools.
│                           The last two are thin wrappers over git/ and
│                           testing/, whose APIs the orchestrator calls directly
├── filesystem/             capped project_tree() — VFS deleted in Step 2 (D7)
├── state/                  paths.py (D15 layout), session id (unused).
│                           ProjectConfigManager died with C6.8a
└── compat/                 monkeypatches + version guard into deepagents internals
```

**Permission flow (Step 7).** `build_gate(cfg, project_path)` returns a `Gate`
bundling the engine, the deny middleware, the `interrupt_on` map, session
grants, and the audit log — constructed together because they must share
objects. `PermissionEngine.decide(tool, args)` is pure and is the only thing
that interprets policy. Precedence, top down: **deny floor** (every mode,
`floor_disable` per rule) → `permissions.deny` → session grants →
`permissions.allow` → mode default. Deny beats allow; reads are never gated;
Rudra's own `add_tasks`/`drop_task`/`ask_user` are control plane
and never gated.

### Control flow (`loop/engine.py`)
1. **Planning runs in three stages before any code** (Step 10b, C6.7): `clarify` settles the facts, `architect` records layout and boundaries, `breakdown` calls `add_tasks` with units of **work**, not filenames. Each stage is a separate agent with its own tool set — clarify cannot `add_tasks`, breakdown cannot `ask_user` or `record_fact` — so a stage cannot do another stage's job. The fact store is the only channel between them, and no stage has a tool that can mark anything done.
2. **The plan is presented and approved** (Step 10c, C6.9): facts with their source, then the tasks. In `ask` mode the user approves, revises — which re-enters `breakdown` with their words **verbatim**, up to `MAX_REVISIONS` (3) times — or cancels. `--plan` presents and stops. `--auto` skips the gate. **EOF and Ctrl-C are cancel, never approve.** The gate lives in `RudraAgent`, between `plan()` and `work()`; the loop does not know what a terminal is.
3. `work()` takes the next pending task and calls `run_task`.
4. `run_task`: coder subagent writes → `verify_project` gates → on failure the blocker goes back **verbatim** and it retries, up to `[agent] max_fix_attempts`.
5. Success = **`VerifyReport.passed`**. Only `engine.py` writes `DONE`, and only there.
6. Two identical failure signatures in a row → `BLOCKED` (C6.5a). A gate `escalate` → the whole run stops.
7. The planner is consulted again **only** on a block or an empty ledger — never after an ordinary success, and only the `breakdown` stage is re-entered (S10b.3). Clarify and architect run once: re-opening the questions after code exists churns decisions the coder already built on.
8. Reviewer runs once at the end, advisory, printed, gating nothing.

**The split that makes this work (S9c.1):** the model decides what work exists;
Python decides when a task is done and when to stop. `C6.1` wanted an agent that
owns the todo list, D9 forbids an LLM deciding termination — both hold because
the ledger tools *cannot express* `DONE`, not because a prompt asks nicely.

Two edges worth knowing before touching it: an **empty diff is a failed attempt**
(with no changed files the gate reports "0 files parsed" and would pass an
untouched task), and `files_touched` comes from **git**, not from the model.

### `.rudra/` state directory

Split into durable and volatile subtrees by D15 (implemented Step 6, C0.9).
`src/rudra/state/paths.py` is the single source of truth — never build a
`.rudra/...` path by hand. `rudra_paths()` is pure; `ensure_layout()` is the
only function that creates anything.

| File | Durable? | Written by | Read by | Notes |
|---|---|---|---|---|
| `config.toml` | durable | `rudra init` | config loader | Layer 3 of five |
| `AGENTS.md` | durable | `_ensure_agents_md` creates it; `record_task_in_memory` and `summarise_architecture` update it | planner via `memory=` | **A living document since C7.3** (closes A1.9): one Session Log entry per completed task, with `files_touched` from git, and one model call per run folding them into Architecture Notes. **The log is capped at 20 entries** — this file is paid for on every planner call, so an uncapped one is a tax that grows without bound (S12.4) |
| `facts.json` | durable | `record_fact` / `ask_user` (agent) | every agent's prompt | Open key/value: `{key: {value, why, source}}`. Keys are enumerated nowhere in code. Replaced `project.json`, which is **ignored, not migrated** (S10a.8) — the old file is left on disk, unreferenced |
| `.gitignore` | durable | `ensure_layout` | git | Written by Rudra, scopes **only** `.rudra/` |
| `run/ledger.json` | volatile | `add_tasks`/`drop_task` (agent) + `engine.py` (status) | `run_loop`, `summarise` | Tasks are **work**, not filenames. Written atomically after every status change; never resumed |
| `run/checkpoints.db` | volatile | `AsyncSqliteSaver` | nothing | **fresh uuid4 thread_id each run — never resumed** (A1.2) |
| `run/logs/permissions.jsonl` | volatile | `permissions.AuditLog` | humans; later `rudra audit` | One line per gated decision, every mode (Step 7) |
| `run/logs/verify.log` | volatile | `verify_project` | humans | Every stage's full output from the last gate run (Step 9a) |
| `run/logs/usage.json` | volatile | `write_usage_log` | humans | Per-role tokens and compactions for the last run. Written at run end, never mid-run, and a write failure is swallowed — a finished run must not be reported failed over bookkeeping (C7.5) |
| `run/artifacts/` | volatile | deepagents eviction + summarization | the agent, via the `/artifacts/` route | Kept out of the project by `artifacts_root` (A1.45) |
| `memory/export/` | durable | — | — | Step 14c. The palace is binary and churns, so the markdown export is the portable copy. `exporter.export_palace` is **not** used — it resolves collection and backend from the user's global config (**A1.87**) |
| `memory/palace/` | volatile | `rudra.memory.store` | `rudra.memory.store` | ChromaDB, project-scoped per D14/S14.5. Opened only through `MemoryStore`; a failure degrades loudly and never fails a task (C8.6) |

Agent-facing prompts used to name these paths as literal strings, and a path
that moved without its prompt meant the coder wrote where nothing reads. Step 9c
removed the hazard rather than guarding it: the ledger is reached only through
tools, so **no prompt names a state path at all**.
`tests/test_rudra_dir_migration.py` now asserts that absence.

---

## 4. What deepagents Already Provides (and Rudra ignores)

Table below is verified against **installed 0.4.12** (`.venv/.../deepagents/graph.py`). For the 0.7.4 delta — new `permissions=`, `RubricMiddleware`, provider/harness profiles, `TodoListMiddleware` no longer auto-added, `write()` now overwrites — see `TODO.md` §F. `create_deep_agent()` accepts:

| Param | What it gives | Rudra uses it? |
|---|---|---|
| `model: str \| BaseChatModel` | `provider:model` resolved via `init_chat_model` (`_models.py:11`) | ⚠️ passes a `BaseChatModel` instance from `llm/factory.py` (Step 5) |
| `skills: list[str]` | `SkillsMiddleware` — Anthropic Agent Skills spec, `<dir>/SKILL.md` + YAML frontmatter | ❌ **never used** — Step 11 |
| `subagents: list[SubAgent]` | `SubAgentMiddleware` + the `task` tool | ✅ Step 9b: four specs in `subagents/registry.py`. **Rudra ships its own `general-purpose` to suppress the ungated one deepagents auto-adds** (`graph.py:751`). Nothing passes `subagents=` to a live agent yet — 9c owns the parent that delegates |
| `memory: list[str]` | `MemoryMiddleware`, AGENTS.md into system prompt | ⚠️ planner only |
| `permissions: list[FilesystemPermission]` | `allow` / `deny` / `interrupt` path rules | ❌ **cannot be used** — raises on any execute-capable backend (U.7) |
| `interrupt_on: dict` | `HumanInTheLoopMiddleware` — approval gates | ✅ Step 7: one entry per mutating tool, `when` calling Rudra's `PermissionEngine` |
| `backend` | `FilesystemBackend` / `LocalShellBackend` / `CompositeBackend` | ✅ Step 7: `CompositeBackend(default=LocalShellBackend)` |
| (automatic) | `create_summarization_middleware` — offloads history to `/conversation_history/{thread_id}.md` | ✅ inherited, not designed |
| (automatic) | `TodoListMiddleware`, `PatchToolCallsMiddleware` | ✅ inherited |

**Critical (settled in Step 7):** `execute` only works on a backend implementing `SandboxBackendProtocol`. `LocalShellBackend` does; plain `FilesystemBackend` does not — the tool is still *registered* in the default stack, and returns `"Error: Execution not available. This agent's backend does not support command execution (SandboxBackendProtocol)."` when called. Measured through a real graph run, closing **U.17**: registered ≠ functional, and `CLAUDE.md`'s original claim was right.

**Since Step 7 Rudra ships `CompositeBackend(default=LocalShellBackend(...))`, so `execute` works** and agents can run tests, linters, builds, and git. The composite also carries an `/artifacts/` route with `artifacts_root="/artifacts"`, because that root otherwise defaults to the backend root and deepagents would write `large_tool_results/` and `conversation_history/` into the user's project (**A1.45**).

**`permissions=` is deliberately never passed.** It raises `NotImplementedError` on any execute-capable backend, and its `FilesystemOperation` is `('read','write')` only, so it never covered `execute` regardless. Rudra's own gate in `src/rudra/permissions/` does the whole job. See `TODO.md` **U.7** and **A1.46**; `tests/test_deepagents_contract.py::test_permissions_still_rejected_with_execute_backend` is the trigger that reopens U.7 if upstream lifts the restriction.

**Skills compatibility:** superpowers skills (`skills/<name>/SKILL.md` with `name:` + `description:` frontmatter — verified in the local plugin cache) match the format `SkillsMiddleware` parses. Superpowers can be dropped in as a skills source with no format conversion.

---

## 5. Provider Independence — solved in Step 5

**This section used to read "Provider Lock-in — the #1 architectural blocker".**
It is done: `src/rudra/llm/` is the model factory, `langchain-openai` ships as a
dependency, and `ChatOllama` is constructed in exactly one place nobody outside
that package imports.

| Rule | Enforcement |
|---|---|
| No module outside `rudra/llm/` may import a provider package | `tests/test_no_direct_provider_imports.py` parses every module with `ast` and fails if one does |
| Ask for a model by role, never by provider | `build_model(role)` (`llm/factory.py:64`) — no network call, and every configuration error raises before inference could start |
| One adapter covers the OpenAI-compatible world | `provider = "openai_compatible"` with a `base_url` reaches vLLM, OpenRouter, LM Studio, Groq and Together through one code path (`llm/providers.py:87`) |

The historical detail — `OllamaConfig`, the `OLLAMA_*` env vars, `ChatOllama`
built inline in two agent modules — is gone from the code. The `OLLAMA_*`
variables survive only as a deprecation shim that warns and maps to `RUDRA_*`.

---

## 5a. Context Management (Step 12)

Most of what manages context is **inherited from deepagents**. Rudra's work
was making it real: three of the four inherited mechanisms were present and
inert before Step 12.

| Mechanism | Origin | State |
|---|---|---|
| Auto-summarization at 85% of the window | inherited | Real since `C1.4a`/`C7.6` declares `max_input_tokens` per role. Before that every local model took a fixed **170 000**-token fallback and never summarized (A1.17) |
| Tool-result eviction to `/artifacts/` | inherited | Real since Step 12a passes a derived threshold. Before that every agent took the **20 000** default — a third of a 32B window for one failing-`pytest` transcript (A1.47) |
| `compact_conversation` | inherited | Registered for the coder and tester only (S12.8). It is control plane, and that is load-bearing: outside `CONTROL_PLANE_TOOLS` the gate denies it |
| Artifacts kept out of the user's repo | **designed** | `artifacts_root="/artifacts"`, or deepagents writes `large_tool_results/` and `conversation_history/` into the project (A1.45) |
| Per-run token accounting | **designed** | `src/rudra/context/usage.py`. Nothing upstream reports what a run cost |
| Per-task memory in `AGENTS.md` | **designed** | `src/rudra/context/agents_md.py` + the two writers in `loop/engine.py`. Session Log capped at 20 entries (C7.3) |

**One number, two consumers.** `[model.<role>] context_tokens` feeds both
the summarization trigger and the eviction threshold. That is deliberate:
two independently configured numbers would drift, and the pair only makes
sense read together.

**The standing gap, stated rather than implied: nothing measures system
prompt growth.** Skills, facts and the bootstrap are a fixed cost paid on
every call, and no mechanism here trims them. Summarization compacts the
*conversation*; the prompt is rebuilt in full each time.

**The compaction count is tool-driven only.** deepagents' automatic
summarization fires without passing through any Rudra middleware, so
`usage.json`'s `compactions` must not be read as "every time context was
shed".

**Continuity is the ledger, not the transcript.** `rudra --continue` works
the remaining tasks from `.rudra/run/ledger.json` and never replays a
conversation (S12.2, C7.2). A stable per-project `thread_id` — the literal
reading of A1.2 — would have every run inherit every prior run's history,
which is a context defect in the context step. The checkpointer stays
regardless: interrupt-and-resume approval depends on it.

**`/compact` is not a REPL command**, and that is not an oversight. The
REPL builds a fresh agent per input (`cli.py`, inside `_repl_session`), so
context does not accumulate between turns and there would be nothing to
compact. Making the REPL session persistent is Step 15's call (`C9.1`–`C9.7`).

---

## 6. Configuration Design (decided 2026-08-05, shipped in Step 6)

Owner decisions are recorded in `TODO.md` §0. Summary: TOML config, vendored superpowers, MemPalace Python API, permission mode `ask` by default with an `--auto` escape hatch, minimum local model 32B, `VirtualFileSystem` deleted.

**Layered TOML, later layers override earlier:**
1. Built-in defaults (shipped in package)
2. `~/.config/rudra/config.toml` (user global)
3. `<project>/.rudra/config.toml` (project)
4. Environment variables (`RUDRA_*`)
5. CLI flags

**Live as of Step 7:** `[model.*]`, `[agent]`, `[permissions]`, `[compat]`,
`[tools]`. `[skills]` and `[memory]` are **reserved** — writing one is a hard
error naming the step that implements it (11 and 14). MCP stays in `.mcp.json`
(Step 13). Unknown keys are fatal and suggest the nearest valid name, because
the common case is a typo.

`[tools]` carries `shell` and nothing else. The oversized-tool-result
threshold that would naturally sit beside it is unreachable through
`create_deep_agent` (**A1.47**), and an inert config key is worse than no key.

`rudra init` scaffolds the file; `rudra config list` shows every effective
value and the layer that set it. There is no `rudra config set` — see
TODO.md S6.1.

```toml
# .rudra/config.toml
[model.planner]
provider  = "openai_compatible"        # ollama | openai_compatible | anthropic | google | openai
base_url  = "http://localhost:8000/v1"
model     = "qwen3-30b"
api_key_env = "RUDRA_PLANNER_KEY"      # name of env var — never the key itself
temperature = 0.3

[model.coder]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3-coder:30b"

[agent]
max_fix_attempts = 3                    # fix-loop retries per task (C6.5a)
max_questions = 5                       # clarification budget for the run; 0 never asks

[tools]
shell = true                            # false removes the execute tool
shell_in_auto = false                   # may --auto run commands? (A1.49)
auto_branch = false                     # branch before a run? (Step 8, S8.3)
test_timeout = 600                      # seconds before a test run is killed

[permissions]
mode  = "ask"                           # ask | auto | plan

# Rules are "tool" or "tool:pattern", using REAL tool names:
#   read_file  ls  glob  grep  write_file  edit_file  delete  execute  task
# A pattern starting with / matches the resolved absolute path; otherwise
# the project-relative path. For execute it matches the command string.
# deny beats allow.
allow = ["execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *", "write_file:.env"]

# Built-in rules denied in EVERY mode, including --auto. Name one to switch
# it off; the run says so, and calls it would have blocked are still audited.
#   git-dir · catastrophic-command
# ("outside-root" is rejected here: the backend confines writes, not this
#  rule, so disabling it would change nothing — see A1.50)
floor_disable = []

# Reserved, not yet accepted — listed to show where they will go:
#   [skills] Step 11    [memory] Step 14
```

**MCP servers in a separate `.mcp.json`, Claude-Code-compatible schema**, so users reuse existing configs verbatim:
```json
{ "mcpServers": { "mempalace": { "command": "mempalace", "args": ["mcp"] } } }
```
Loaded via `langchain-mcp-adapters` → tools handed to `create_deep_agent(tools=[...])`.

**API keys never in TOML.** Config names the env var; the key lives in the environment or `.env`.

---

## 7. Hard-Won Knowledge (don't relearn)

- `install_path_normalizer` (`compat/deepagents_path.py`) monkeypatches `deepagents.backends.utils.validate_path` **and** `deepagents.middleware.filesystem.validate_path` because the middleware does `from ... import validate_path` — patching one is not enough. This will break on any deepagents upgrade; `pyproject.toml:27` uses `>=`, not `==`.
- `OverwriteFilesystemBackend` (`compat/overwrite_backend.py`) exists because **0.4.12's** `FilesystemBackend.write()` refuses to overwrite, sending small local models into a `write → error → edit no-op → write` infinite loop. **In 0.7.4 `write()` overwrites by default** (`backends/filesystem.py:489`) → the subclass is obsolete; only its markdown-fence stripping still matters.
- The 6 middlewares are all patches for qwen3:14b failure modes documented in their own docstrings. Minimum target model is now **32B** → 4 of the 6 were deleted in Step 2, and the 2 survivors are **opt-in behind `[compat]`, default off, as of Step 6** (C1.8). Disposition table: `TODO.md` §0.1. `FixWriteParamsMiddleware` is split rather than gated wholesale: fence-stripping and `filename`/`path` → `file_path` aliasing are **always on** (required since U.3 — 0.7.4's `write()` no longer strips), while sandbox-prefix stripping sits behind `[compat] sandbox_paths` because `/src/` and `/tmp/` are sandbox prefixes *and* ordinary absolute directories.
- **Configuration precedence lives in exactly one function**, `config/loader.py::deep_merge`, and every value records the layer that set it. This shape was chosen because A5.1 and A5.2 were both "which source won?" defects that a per-field `x or y or default` chain structurally cannot answer. Do not reintroduce per-field resolution.
- **Role inheritance runs after the cross-layer merge**, not inside a layer. Inside a layer, a project-level `[model.planner]` would fail to inherit a user-level `[model.default]`.
- `road-map.md` argues against trajectory fine-tuning and for planning-data fine-tuning; `improvements.txt` and `context_management_implementation_plan.md` are prior planning docs, partially implemented.

---

## 8. Commands

```bash
.venv/bin/ruff check src/ tests/     # must print "All checks passed!" — absolute gate since Step 3
.venv/bin/ruff format --check src/ tests/
uv run pytest -q                     # 955 passed, 2 skipped at Step 10a; must never go down
git config core.hooksPath .githooks  # once per clone: run all three gates on push (A3.7)
.venv/bin/rudra --version            # Rudra v0.2.0

.venv/bin/rudra init                 # scaffold .rudra/config.toml + the D15 layout
.venv/bin/rudra config list          # every effective value + which layer set it
.venv/bin/rudra doctor --offline     # diagnose config, layout, deps; --offline skips network
.venv/bin/rudra models test          # verify each role is reachable and can call tools
.venv/bin/rudra verify               # deterministic gate over the changed files (Step 9a)
.venv/bin/rudra verify --all --json  # whole project, machine-readable. Exit 0/1/2
.venv/bin/rudra "build a flask app"  # single-shot; prompts before each write and command
.venv/bin/rudra --auto "..."         # unattended: files yes, commands no (A1.49)
.venv/bin/rudra --auto --allow-shell "..."   # ...and commands too, opted in explicitly
.venv/bin/rudra --plan "..."         # show the plan and stop — writes nothing
.venv/bin/rudra                      # REPL
```

**`mode = "ask"` needs a TTY.** Piped or redirected stdin exits 2 before any
model call rather than hanging on a prompt nobody can answer. Use `--auto` for
unattended runs.

**Unattended shell is opt-in (A1.49).** `--auto` alone runs the filesystem
tools only. Those the backend genuinely confines to the project via
`virtual_mode=True` — which, not Rudra's floor, is what actually contains them
(A1.50). `execute` is denied under `--auto` unless the user says so once, with
`--allow-shell` or `[tools] shell_in_auto = true`.

The reason is measured, not theoretical. The floor's path rules cover
`write_file`/`edit_file`/`delete`; `execute` is checked only against
`rm -rf /`-shaped commands, and a shell command can write anywhere the user
can. Before this gate existed, the Step 7 acceptance run watched the model
find that route by itself — denied twice on `write_file`, it ran
`echo "hello" > /abs/path` and succeeded. Re-running the same task now denies
the shell attempt (`source: "auto-shell"`) and nothing leaves the project.

**Since Step 8 this has a consequence worth stating plainly: `--auto` alone
runs no tests.** `run_tests` is gated as `execute` — correctly, since
`pytest` executes the test files the model itself wrote — so an unattended
run cannot verify its own output unless shell is opted in. A
loop-engineering run wants `--auto --allow-shell`. Step 9's fix loop
inherits this rather than working around it.

**Step 9a extends this to `rudra verify`.** Its lint, typecheck, and test
stages are commands, so the same rule applies: under `--auto` without
`--allow-shell`, and under `mode = "ask"` with no terminal, they report
`denied`, the verdict escalates, and the command exits 2. Only Python's
`ast.parse` syntax check and the stub scan are native and always run.

`ask` mode is unaffected and keeps full shell, because there the user reads
each command before it runs. An explicit `allow = ["execute:pytest*"]` also
counts as opting in for that command — naming it *is* the consent. Turning
`shell_in_auto` on restores the original exposure; the audit log is then the
only control, and real containment would need OS-level isolation.

## 9. Repo Hygiene Facts

- 41 files tracked. Includes `q-dev-chat-2026-03-20.md` (199 KB chat log), `filesystem-context.txt`, `improvements.txt`, `execution_tools.py` (255 lines, zero imports) — all noise for an OSS launch.
- `.env` and `.rudra/` are gitignored (verified via `git check-ignore`).
- ~~No LICENSE file, no CI, no CONTRIBUTING, no tests.~~ Stale as written. `LICENSE` landed with A4.2, CI with A3.3 (`.github/workflows/ci.yml` — lint + a 3.12/3.13 test matrix running under coverage), and the suite is at 520 passed / 2 skipped. **CONTRIBUTING is still absent** (A4.7).
- **A local `.venv` drifts from `uv.lock` and will lie to you.** Measured 2026-08-11 on unmodified `main`: `.venv/bin/pytest -q` → `14 failed, 502 passed`, against `520 passed, 2 skipped` on a clean `uv sync` clone; the venv was missing `langchain_openai`, which `pyproject.toml:47` requires. Two defects reached `main` under that noise (A3.5, A1.51). Run `uv sync` before believing a local failure, and prefer `uv run` — which reconciles first — when the answer matters.
