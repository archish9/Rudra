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
- **Repo:** remote is `git@github.com:archish9/RudraAnvil.git`; `pyproject.toml:65-66` wrongly says `github.com/archish/Rudra`
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

## 3. Current Architecture (as of 2026-08-05, commit `3bac6ad`)

```
src/rudra/
├── cli.py            (419) Typer app. ONLY 2 entry paths: main callback + `watch`
├── config.py         ( 68) OllamaConfig / AgentConfig / SearchConfig; module-level `config`
├── agent/
│   ├── main_agent.py (601) RudraAgent — hand-rolled planner→coder orchestration loop
│   ├── planner_agent.py (96) deep agent, ChatOllama hardcoded
│   └── coder_agent.py   (84) deep agent, ChatOllama hardcoded, tools=[]
├── middleware/       (376) 6 middlewares, all workarounds for qwen3:14b misbehavior
├── tools/            (383) planning_tools, interaction_tools, code_tools
├── filesystem/       (686) VirtualFileSystem + FileSyncManager
├── state/            (113) session id (unused), ProjectConfigManager
└── compat/           (182) monkeypatches into deepagents internals
```

### Control flow (`main_agent.py:345-437`)
1. Planner runs once → must produce `.rudra/PLAN.md`, a checklist of **bare filenames** (`- [ ] main.py`).
2. Orchestrator parses pending filenames (`_parse_pending_files`, `main_agent.py:53`).
3. For each file: planner (same thread) writes `.rudra/current_task.md` → a **fresh** coder agent with a **fresh thread** writes that one file → up to 3 attempts.
4. Success check = **file exists on disk** (`main_agent.py:411`). Not content correctness.
5. Tick off with a string replace (`_check_off_file`, `main_agent.py:61`).

This is a hardcoded Python loop, not an agentic loop. There is no test/review/fix stage.

### `.rudra/` state directory
| File | Written by | Read by | Notes |
|---|---|---|---|
| `PLAN.md` | `update_plan` (`planning_tools.py:41`) | orchestrator + `read_plan` | Filenames only |
| `current_task.md` | `write_task_assignment` (`planning_tools.py:133`) | coder | Planner→coder handoff |
| `tech_stack.md` | `_write_tech_stack_file` (`main_agent.py:477`) | both | Rewritten every run |
| `AGENTS.md` | `_ensure_agents_md` (`main_agent.py:446`) | planner via `memory=` | **Created once, never updated** |
| `project.json` | `save_project_context` | `ProjectConfigManager` | Working |
| `checkpoints.db` | `AsyncSqliteSaver` | nothing | **thread_id is a fresh uuid4 each run — never resumed** |
| `logs/cmd_output.txt` | `run_command` | — | Tool is not wired to any agent |

---

## 4. What deepagents Already Provides (and Rudra ignores)

Table below is verified against **installed 0.4.12** (`.venv/.../deepagents/graph.py`). For the 0.7.4 delta — new `permissions=`, `RubricMiddleware`, provider/harness profiles, `TodoListMiddleware` no longer auto-added, `write()` now overwrites — see `TODO.md` §F. `create_deep_agent()` accepts:

| Param | What it gives | Rudra uses it? |
|---|---|---|
| `model: str \| BaseChatModel` | `provider:model` resolved via `init_chat_model` (`_models.py:11`) | ❌ passes `ChatOllama` instance |
| `skills: list[str]` | `SkillsMiddleware` — Anthropic Agent Skills spec, `<dir>/SKILL.md` + YAML frontmatter | ❌ **never used** |
| `subagents: list[SubAgent]` | `SubAgentMiddleware` + the `task` tool | ❌ Rudra **blocks** `task` (`block_task_tool.py`) |
| `memory: list[str]` | `MemoryMiddleware`, AGENTS.md into system prompt | ⚠️ planner only |
| `interrupt_on: dict` | `HumanInTheLoopMiddleware` — approval gates | ❌ never used |
| `backend` | `FilesystemBackend` / `LocalShellBackend` / `CompositeBackend` | ⚠️ plain `FilesystemBackend` |
| (automatic) | `create_summarization_middleware` — offloads history to `/conversation_history/{thread_id}.md` | ✅ inherited, not designed |
| (automatic) | `TodoListMiddleware`, `PatchToolCallsMiddleware` | ✅ inherited |

**Critical:** `execute` (shell) only works on a backend implementing `SandboxBackendProtocol`. `LocalShellBackend` does (`backends/local_shell.py:27`). Rudra's `OverwriteFilesystemBackend` extends plain `FilesystemBackend` → **the `execute` tool returns an error → Rudra's agents cannot run tests, linters, builds, or git.** (Contradicted by probe under 0.7.4; `execute` **is** registered on a plain `FilesystemBackend` — nobody has invoked it to confirm it's functional, not just registered. See `TODO.md` U.17.)

**Skills compatibility:** superpowers skills (`skills/<name>/SKILL.md` with `name:` + `description:` frontmatter — verified in the local plugin cache) match the format `SkillsMiddleware` parses. Superpowers can be dropped in as a skills source with no format conversion.

---

## 5. Provider Lock-in — the #1 architectural blocker

Ollama is hardcoded in three places:
- `config.py:12-22` — `OllamaConfig`, all env vars `OLLAMA_*`
- `planner_agent.py:75-81` — `ChatOllama(...)` with Ollama-only kwargs `num_predict`, `reasoning=True`
- `coder_agent.py:61-67` — same

Installed provider packages: `langchain_ollama`, `langchain_anthropic`, `langchain_google_genai`. **`langchain-openai` is NOT installed** — so `openai:`, vLLM, OpenRouter, LM Studio, Together, and every other OpenAI-compatible endpoint fail today.

The fix is a **Rudra-side model factory**, not a deepagents change — deepagents already accepts any `BaseChatModel`. One `ChatOpenAI(base_url=...)` adapter covers vLLM/OpenRouter/LM Studio/Groq/Together in a single code path.

---

## 6. Configuration Design (decided 2026-08-05, not yet implemented)

Owner decisions are recorded in `TODO.md` §0. Summary: TOML config, vendored superpowers, MemPalace Python API, permission mode `ask` by default with an `--auto` escape hatch, minimum local model 32B, `VirtualFileSystem` deleted.

**Layered TOML, later layers override earlier:**
1. Built-in defaults (shipped in package)
2. `~/.config/rudra/config.toml` (user global)
3. `<project>/.rudra/config.toml` (project)
4. Environment variables (`RUDRA_*`)
5. CLI flags

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

[skills]
sources = ["builtin", "~/.rudra/skills", ".rudra/skills"]
superpowers = true                      # ship-with-Rudra default

[memory]
backend = "mempalace"
store   = "chroma"

[tools]
shell = true
git   = true

[permissions]
mode  = "ask"                           # ask | auto | plan
allow = ["Read", "Grep", "Glob"]
deny  = ["execute:rm -rf *"]
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
- The 6 middlewares are all patches for qwen3:14b failure modes documented in their own docstrings. Minimum target model is now **32B** → 4 of the 6 get deleted, 2 survive behind a `[compat]` flag. Disposition table: `TODO.md` §0.1.
- `road-map.md` argues against trajectory fine-tuning and for planning-data fine-tuning; `improvements.txt` and `context_management_implementation_plan.md` are prior planning docs, partially implemented.

---

## 8. Commands

```bash
.venv/bin/ruff check src/            # 164 errors at merge-base b322978; standard is NO INCREASE vs that baseline, not an absolute ceiling
.venv/bin/pytest                     # tests/ is empty — 0 tests
.venv/bin/rudra --version            # prints 0.1.0; pyproject says 0.2.0
.venv/bin/rudra "build a flask app"  # single-shot
.venv/bin/rudra                      # REPL
```

## 9. Repo Hygiene Facts

- 41 files tracked. Includes `q-dev-chat-2026-03-20.md` (199 KB chat log), `filesystem-context.txt`, `improvements.txt`, `execution_tools.py` (255 lines, zero imports) — all noise for an OSS launch.
- `.env` and `.rudra/` are gitignored (verified via `git check-ignore`).
- No LICENSE file, no CI, no CONTRIBUTING, no tests.
