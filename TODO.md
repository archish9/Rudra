# TODO.md — Rudra Live Ledger

**Last audit:** 2026-08-05 against commit `3bac6ad` (clean tree).
**Rules:** Nothing gets fixed before it is listed here as `PENDING`. Fix → mark `DONE` with the verifying command output. Every item carries `file:line` evidence.

Status: `PENDING` · `IN PROGRESS` · `DONE` · `WONTFIX`

> **Start here:** [Section E — Execution Order](#section-e--execution-order).

---

## SECTION 0 — Decisions Locked (owner, 2026-08-05)

| # | Decision | Consequence |
|---|---|---|
| D1 | **Config format = TOML** | `.rudra/config.toml` + `~/.config/rudra/config.toml`. MCP stays in separate `.mcp.json` (Claude-Code-compatible schema) |
| D2 | **Superpowers = vendored** (offline, reproducible) | Copy into `src/rudra/skills/superpowers/`, pin the upstream commit, add `rudra skills update`. MIT → NOTICE file required |
| D3 | **MemPalace = Python API**, not its MCP server | `mempalace` 3.6.0 on PyPI; deps `chromadb>=1.5.4` + `huggingface-hub` + `tokenizers` → local embeddings, no API key. Direct API = control over what gets stored |
| D4 | **Middlewares: delete 4, keep 2 narrow** — see [Section 0.1](#01--middleware-disposition-d4) | They were built for qwen3:14b. Min model is now 32B, and 3 of them actively block required features |
| D5 | **Permission mode default = `ask`**, with an escape hatch | `--auto` / `--yolo` CLI flag, `/auto` REPL toggle, `permissions.mode` in TOML |
| D6 | **Minimum local model = 32B** | Small-model workarounds are no longer the design center. Skills-by-description and MCP schema loading can assume real instruction following |
| D7 | **Delete `VirtualFileSystem` + `FileSyncManager`** | `load_from_disk` reads every text file in the repo into RAM (`virtual_fs.py:114-157`) — fatal for local-first memory budget. Replace with deepagents `LocalShellBackend` lazy file tools + a capped `project_tree()` helper that reads names only, never content |
| D8 | **Upgrade deepagents 0.4.12 → 0.7.4** | Large win, several obsolete workarounds. See [Section F](#section-f--deepagents-074-upgrade-findings) |
| D9 | **Fix loop = hand-rolled deterministic (Option A).** `RubricMiddleware` deferred, not adopted | Rationale in [Section 0.2](#02--fix-loop-design-d9) |
| D10 | **Superpowers skills: rewrite for Rudra tool names**, do not alias | Forks the vendored copy → `rudra skills update` becomes a merge, not a copy. Scope is smaller than feared: ~24 call sites. See [Section 0.3](#03--superpowers-skill-selection-d10--d11) |
| D11 | **Vendor all 14 skills; enable 8 by default** | Enabled: brainstorming · writing-plans · executing-plans · systematic-debugging · test-driven-development · verification-before-completion · requesting-code-review · receiving-code-review. Plus `using-superpowers` bootstrap (C5.3), not optional. Other 5 vendored but off until their prerequisite lands. Vendoring ≠ enabling: progressive disclosure means enablement is a prompt-index decision, disk cost is irrelevant. See [Section 0.3](#03--superpowers-skill-selection-d10--d11) |
| D12 | **MemPalace embedding model = explicit pre-fetch step, documented** | Preserves the offline/local-first promise. `rudra init` / `rudra doctor` warms the model; docs state the one-time download plainly |
| D13 | **Skills delivered via a user-level cache + `CompositeBackend` route** | Option 2. Answers G6 with no deepagents fork. Required because skills live outside the project root while the backend is rooted at it. See [Section 0.4](#04--skill-delivery-mechanism-d13) |
| D14 | **MemPalace palace is project-scoped, stored under `<project>/.rudra/memory/`** | Supported natively via `Config(palace_path=...)`. Some MemPalace state stays user-global and must be explicitly overridden. See [Section 0.6](#06--mempalace-storage-scope-d14) |
| D15 | **`.rudra/` split into durable vs volatile subtrees; Rudra writes `.rudra/.gitignore` scoping only itself** | Committing `.rudra/` must be safe by default, and it is the user's choice — Rudra never edits the project's root `.gitignore`. See [Section 0.7](#07--rudra-directory-layout-d15) |
| D16 | **Stay pure Python. Do not write Rust components.** | Measured 2026-08-05. Inference dominates non-inference work by 3–4 orders of magnitude (10–60 s vs single-digit ms). The CPU-bound paths are **already Rust** via `pydantic_core`, `jiter`, `orjson`, `regex`, `watchfiles`, and `tokenizers` (with mempalace). The only user-perceptible non-inference cost is **0.59 s startup**, which is the langchain/deepagents import itself — unfixable in Rust without abandoning deepagents. Real wins are Python-side: C9.8 (lazy import) and C9.9 (shell out to `ripgrep`). Revisit only if a single self-contained binary with no Python runtime becomes a goal |

### 0.1 — Middleware disposition (D4)

| Middleware | Verdict | Reason |
|---|---|---|
| `BlockPrematureAskMiddleware` | **DELETE** | Regex-blocks `ask_user` whenever the prompt names a framework (`block_premature_ask.py:20-23`) — directly blocks requirement #4, dynamic clarifying questions |
| `BlockTaskToolMiddleware` | **DELETE** | Blocks the `task` tool — blocks requirement #3, subagents for coder/tester/reviewer |
| `ContinueAfterWriteMiddleware` | **DELETE** | Already dead (zero call sites). The new agentic loop owns continuation |
| `EnforceTargetFileMiddleware` | **DELETE** | Only meaningful inside the one-file-per-agent loop being replaced. Also has a basename-matching hole (A1.6) |
| `TaskAnchorMiddleware` | **KEEP, opt-in** | Re-injects the task into the system prompt every model call. Still useful on long 32B runs. Gate behind `[compat] task_anchor = false` |
| `FixWriteParamsMiddleware` | **KEEP, split** | Markdown-fence stripping becomes **required** (0.7.4's `FilesystemBackend.write` no longer strips — Rudra's `OverwriteFilesystemBackend` was doing it). Keep fence-strip + `filename`/`path`→`file_path` aliasing always on; move sandbox-prefix stripping behind `[compat] sandbox_paths = false` |

### 0.2 — Fix-loop design (D9)

**Decision: hand-rolled deterministic loop. `RubricMiddleware` is deferred, not adopted.**

Primary reason is not reliability — it is **role overlap**. C6.3 already plans a reviewer subagent, i.e. the LLM-judgment layer. `RubricMiddleware` would add a *second* LLM judge that additionally **controls whether the agent is allowed to stop** (`rubric.py:92` — only `satisfied` / `failed` / `max_iterations` terminate). Two judges, one holding the exit door.

Clean split instead:

| Layer | Decides | Authority |
|---|---|---|
| Deterministic gate (C6.6) | done / not done | **Blocking.** Test exit code is ground truth |
| Reviewer subagent (C6.3) | quality | **Advisory.** Never gates the loop |

Secondary reasons, all sharper at 32B (D6):
- No extra inference pass per stop-attempt — matters for local latency and memory
- No structured-output fragility. Upstream already guards against self-contradictory grader responses (`rubric.py:293-297`), which implies that failure is common enough to need a guard
- The grader reads the **whole transcript** — a real context-window problem on a 32B model mid-session
- Failures are debuggable: a pytest traceback, not a grader's prose

**Cost of this choice, and how it is paid:** a test-only gate misses stubs, `TODO`s, unhandled errors. Covered deterministically in C6.6 — no judge needed.

**Revisit trigger:** if C6.6's deterministic gate proves too weak in practice, re-open `RubricMiddleware` as a *final completeness check only*, with a tight `max_iterations` — never as the loop controller.

### 0.3 — Superpowers skill selection (D10 + D11)

Verified against the upstream tree: **14 skills, 3,185 lines of `SKILL.md`, plus 36 supporting files** (`references/*.md`, helper scripts, prompt templates).

#### Vendor everything; gate enablement separately

Owner's rule is "don't strip anything that can help Rudra." Chasing the reference graph proves that rule converges on the whole set: adding `executing-plans` immediately pulled in three more skills (below). So the two questions get separated:

| Question | Answer |
|---|---|
| What gets **vendored** (on disk, resolvable, `read_file`-able) | **All 14.** Cost is disk, not context. Eliminates dangling references entirely |
| What gets **enabled** (name+description in the prompt index) | **8 by default**, rest opt-in via `[skills] enabled = [...]` in TOML |

`SkillsMiddleware` uses progressive disclosure (`middleware/skills.py:3`, `:738`, `:757`) — only `name` + `description` enter the prompt; the body is fetched with `read_file` on demand. Enablement is therefore a prompt-index decision, not a disk decision.

**Enabled by default (8) + bootstrap:**

| Skill | Lines | Why |
|---|---|---|
| `brainstorming` | 151 | Requirement #1 — plan before coding, dynamic clarification |
| `writing-plans` | 168 | Requirement #1 — plan artifact |
| `executing-plans` | 64 | Requirement #1 — a plan Rudra writes but cannot execute is half a workflow |
| `systematic-debugging` | 283 | Requirement #4 — fix wrong code |
| `test-driven-development` | 320 | Requirement #4 — testing; `systematic-debugging` leans on it |
| `verification-before-completion` | 120 | Requirement #4 — pairs with the C6.6 deterministic gate |
| `requesting-code-review` | 95 | Requirement #4 — main agent → reviewer subagent (C6.3) |
| `receiving-code-review` | 205 | Requirement #4 — acting on reviewer feedback, i.e. the C6.5 fix loop |
| `using-superpowers` | 62 | **Bootstrap, not optional.** Tracked as C5.3 |

> **Owner said "code-review" — no skill has that name.** Upstream splits it into `requesting-` and `receiving-`. Both halves map onto Rudra's reviewer-subagent design, so both ship.

**Vendored but off by default (5)** — flip on once their prerequisite lands:

| Skill | Lines | Enable when |
|---|---|---|
| `using-git-worktrees` | 167 | C3.5 (git tools) lands. Genuinely valuable — isolates agent work from the user's working tree |
| `finishing-a-development-branch` | 201 | C3.5 lands. Pairs with worktrees |
| `subagent-driven-development` | 503 | C6.2 (subagents) lands. Highest rewrite cost — 27 tool sites |
| `dispatching-parallel-agents` | 167 | Parallel subagents exist |
| `writing-skills` | 679 | Ship as user-facing: lets users author their own Rudra skills. 31 tool sites |

#### Reference graph — fully resolved

Adding `executing-plans` pulled in three further references, which is what motivated vendoring all 14:

| Skill | References | Resolved? |
|---|---|---|
| `systematic-debugging` | `test-driven-development`, `verification-before-completion` | ✓ both enabled |
| `writing-plans` | `executing-plans`, `subagent-driven-development`, `using-git-worktrees` | ✓ all vendored |
| `executing-plans` | `finishing-a-development-branch`, `subagent-driven-development`, `using-git-worktrees` | ✓ all vendored |

**Zero dangling references.** C5.4a is now about *enablement gating*, not stripping.

#### D10 rewrite scope — measured

Tool-name sites in `SKILL.md` files, all 14: **101 total** — 34 `Write`, 33 `Task`, 20 `Skill`, 13 `Read`, 1 `Edit`.

Concentrated in two files: `writing-skills` (31) and `subagent-driven-development` (27) — both off by default, so their rewrite can be deferred. The 8 enabled skills carry **~38 sites**.

Mapping:

| Claude Code | Rudra / deepagents | Note |
|---|---|---|
| `Task` | `task` | SubAgentMiddleware names it exactly that — near-identity |
| `Write` / `Read` / `Edit` | `write_file` / `read_file` / `edit_file` | Mechanical |
| `Skill` (20 sites) | **`read_file(file_path=<skill path>)`** | **Not a rename.** deepagents exposes *no* skill-invoke tool; progressive disclosure works by reading the SKILL.md path from the index (`skills.py:738`, `:757`). Shape changes from a bare tool name to a tool + argument |

**Also in scope, and not counted above: the 36 supporting files.** `systematic-debugging` alone ships 10 (`root-cause-tracing.md`, `find-polluter.sh`, …); `writing-skills` ships 5 incl. a `.js` renderer. C5.4 must scan these too, not just `SKILL.md`.

### 0.4 — Skill delivery mechanism (D13)

#### The constraint the README exposes

Per `README.md:99-164`, `rudra` is installed **once, globally** (pipx or venv; C10.5 adds PyPI). The user then `cd`s into **many different project directories** and runs `rudra`.

But deepagents' `FilesystemBackend` is rooted at the **project path** (`main_agent.py:554`), and skill sources are **backend-relative paths** (`skills.py`, "Path Conventions"). Vendored skills live in `site-packages` — **outside the backend root, therefore unreadable**.

This kills the naive "just point `skills=` at the package directory" approach, and it is why Option 1 (two directories inside the package) was rejected: `site-packages` is read-only on a pipx/PyPI install.

#### The mechanism

```python
CompositeBackend(
    default = LocalShellBackend(root_dir=project_path),           # project files + execute
    routes  = {"/skills/": FilesystemBackend(root_dir=cache_dir)},
)
create_deep_agent(skills=["/skills/active/"], backend=composite, ...)
```

Verified in the 0.7.4 wheel:
- `filesystem.py:155-159` — `virtual_mode=True`'s stated primary use case is exactly this: `CompositeBackend` strips the route prefix and forwards normalized paths
- `composite.py:774` — `CompositeBackend.execute` delegates to `default` when it is a `SandboxBackendProtocol`, so shell still works rooted at the project (C3.1 unaffected)

#### Cache layout

```
${XDG_CACHE_HOME:-~/.cache}/rudra/skills/<key>/
  library/    all 14, rewritten. Readable via read_file, NOT indexed.
  active/     copies of the 8 enabled. Passed as the skills source.
```

`<key>` = hash of (rudra version + upstream skills SHA + transform version + sorted enabled list). Built on first run; reused by every project sharing that config. A project whose TOML enables a different set gets its own key.

Two properties fall out, and both matter:

| Property | Consequence |
|---|---|
| **Enablement = which directory is a source** | No allowlist parameter needed. **G6 answered with zero deepagents patching** — avoids a second `compat/` monkeypatch on top of the U.4 hazard |
| **Cross-refs always point at `/skills/library/<name>/`** | One canonical path regardless of enablement. The C5.4 rename transform never needs to know what is enabled, and enabling a skill later (C5.9) rewrites no references |

#### Why the cache, not `.rudra/` and not the package

| Location | Verdict |
|---|---|
| `~/.cache/rudra/skills/` | **Chosen.** One install, many project dirs → shared. Nothing written to the install dir, so read-only `site-packages` is fine |
| `<project>/.rudra/skills/` | Rejected — copies ~3,185 lines + 36 support files into **every** project directory. Contradicts D7's memory/footprint goal |
| Inside the package | Rejected — read-only under pipx/PyPI installs |

### 0.5 — Static Q&A purge (C6.8a)

Requirement #1: *"if anything is not cleared from prompt it should ask user questions. this should be dynamic. no static QA."*

The current static questionnaire is **not one file**. It is a fixed 4-field model — language / framework / database / notes — threaded through 7 places. Deleting `ProjectContext` alone leaves 6 that silently re-impose the same 4 boxes.

#### Inventory — all verified

| # | Surface | Evidence | Failure it causes |
|---|---|---|---|
| 1 | `ProjectContext` — 4 fixed fields | `state/project_config.py:11-17` | The data model *is* the questionnaire. Nothing else can be recorded |
| 2 | `save_project_context` hardcoded allowlist | `interaction_tools.py:67` | Any fact outside the 4 fields is **silently discarded** — no error, no warning |
| 3 | `ask_user` docstring steers to language/framework/database **and mandates "ONE focused question at a time"** | `interaction_tools.py:32` | Caps the question space, and **directly contradicts C6.8's batching requirement** |
| 4 | `_ensure_agents_md` renders the same 4 fields | `main_agent.py:454-461` | Project memory can only remember those 4 things |
| 5 | `_write_tech_stack_file` renders the same 4, plus a **hardcoded 8-row inference table** (Flask/Django/FastAPI/React/Next.js/Express/Spring/Rails) | `main_agent.py:484-506` | **Static inference, the twin of static Q&A.** "Build a Rust CLI with clap" matches nothing and falls through to "infer the most likely one and proceed" |
| 6 | Planner prompt: *"Do NOT call ask_user() if the task already specifies a framework or language"* | `planner_agent.py:60` | Prompt-level twin of `BlockPrematureAskMiddleware`. **Deleting the middleware in C0.2 does not remove this** |
| 7 | `README.md:626-636` documents the literal 4-question form | `README.md:626-636` | Ships the static model as the advertised behavior |

Plus the on-disk `.rudra/project.json` schema, which mirrors #1.

#### Target design

| Concern | Replace with |
|---|---|
| Storage (#1, #4, #7) | **Open key/value fact store.** Agent records whatever it actually established — `{"language": "Rust", "cli_framework": "clap", "min_rust_version": "1.75", "no_async": true}`. Keys are not enumerated anywhere in code |
| Tool (#2) | Drop `valid_fields`. Accept arbitrary keys; validate types and size, not names |
| Tool contract (#3) | Rewrite `ask_user` to accept **a batch of related questions** and return a mapping. Delete "ONE question at a time" — it is the opposite of what C6.8 needs |
| Generated context (#5) | Render `tech_stack.md` from whatever facts exist, in insertion order. **Delete the 8-row inference table entirely** — inferring the stack is the model's job, not a lookup table's |
| Prompt rules (#6) | Replace the prohibition with a budget: *ask only what you cannot infer from the prompt or the codebase; batch related questions; you have N questions* |
| Docs (#7) | Rewrite alongside A4.1 |

**Migration:** existing `.rudra/project.json` files carry the 4 old keys. Read them as ordinary facts — the old field names become unremarkable keys in the open store, so no migration script is needed.

**Acceptance test for this item:** `rudra "build a CLI in Rust with clap"` in an empty directory must record `rust` and `clap` as facts and generate a Rust plan. Today surface #5 has no Rust row and surface #6 forbids asking, so the run is steered toward a Python/JS default.

### 0.6 — MemPalace storage scope (D14)

Verified by unpacking the `mempalace` 3.6.0 wheel.

#### Project-scoped palace is natively supported

| Mechanism | Evidence |
|---|---|
| `Config(palace_path=...)` constructor override | `config.py:368`, `:385` — clean Python API, matches D3 |
| `MEMPALACE_PALACE_PATH` env var | `config.py:387` |
| Default when unset | `~/.mempalace/palace` (`config.py:221`) — **user-global, must be overridden** |
| `tunnel_file` / `hallway_file` are siblings of `palace_path` | `config.py:397-410`. Docstring states this was changed *specifically* so "multiple palaces on one host" stop sharing one file → multi-palace is deliberate design |

Layout:

```
<project>/.rudra/memory/
  palace/         ChromaDB store  (palace_path)
  tunnels.json    sibling, automatic
  hallways.json   sibling, automatic
```

#### State that stays user-global regardless of palace_path

| Path | Evidence | Handling |
|---|---|---|
| `~/.mempalace/config.json` | `config.py:4`, `:364`, `:393` | **Must be explicitly overridden.** A user's personal MemPalace config would otherwise silently change Rudra's embedding model / backend — a bug that reproduces on one machine only |
| `~/.mempalace/entity_registry.json` | `entity_registry.py:299` | Accept, or point elsewhere if the API allows. Investigate in C8.1 |
| `~/.mempalace/hook_state/`, `state/` | `hooks_cli.py:23-24`, `diary_ingest.py:50` | Not used by Rudra's integration path — verify they stay dormant |
| HuggingFace embedding-model cache | `embedding.py:7-20` | **Leave global — this is the desirable case.** D12's pre-fetch downloads once and every project reuses it |

#### Governing rule this settles

| Nature of the data | Location |
|---|---|
| Identical across projects | **User cache** — skills (D13), embedding model |
| Unique per project | **Project dir** — palace, checkpoints, config, plan |

#### Durability

The palace is binary and churns on every write, so it must never be the only copy of anything worth keeping. `export_palace(palace_path, output_dir, format="markdown")` (`exporter.py:68`) writes durable markdown into `.rudra/memory/export/`, which D15 places in the committable subtree. Whether the user commits it is their choice; Rudra's job is to make that choice safe. See §0.7.

### 0.7 — `.rudra/` directory layout (D15)

#### Correcting a wrong premise

An earlier revision of this file claimed "`.rudra/` is gitignored". **That is false as a general statement.** `.gitignore:213` in *this* repo ignores `.rudra/` only because `rudra` was run here during development. Verified:

- `.rudra/` is created automatically by `main_agent.py:559-560`
- **No code anywhere writes to the project's `.gitignore`** (`grep -rn "gitignore" --include=*.py src/` → only *read* paths, in `virtual_fs.py`)
- `README.md:592-596` merely *suggests* the user add it

Whether `.rudra/` is committed is the **user's decision**, and Rudra must not make it for them.

#### The actual defect

`.rudra/` currently mixes durable text and volatile binary in one flat directory, so both user choices are bad:

| Choice | Outcome today |
|---|---|
| Commit `.rudra/` | Commits `checkpoints.db` — **61 KB in this repo, 278 KB in `coding-files/` after one small task** — plus, once C8 lands, a ChromaDB store that rewrites on every memory write |
| Gitignore `.rudra/` | Loses `AGENTS.md`, clarified facts, and the memory export — the durable context that is the entire point of C7.3 and Phase 8 |

#### Target layout

```
.rudra/
  .gitignore      written by Rudra, scoping ONLY its own directory
  config.toml     durable
  AGENTS.md       durable   (C7.3 living document)
  facts.json      durable   (open fact store, C6.8a — replaces project.json)
  memory/
    export/       durable   markdown export (exporter.py:68)
    palace/       volatile  ChromaDB                    [ignored]
  run/
    checkpoints.db  volatile                            [ignored]
    PLAN.md         volatile  current-run state         [ignored]
    logs/           volatile                            [ignored]
```

Committing `.rudra/` becomes safe by default; ignoring it becomes a deliberate, informed loss. Rudra writes only `.rudra/.gitignore`, which affects nothing outside its own directory.

---

## SECTION A — Bugs & Defects Found During Audit

All verified. None fixed yet.

### A1 — Correctness bugs

| # | Status | Item | Evidence |
|---|---|---|---|
| A1.1 | PENDING | `__version__ = "0.1.0"` but pyproject says `0.2.0`. `rudra --version` prints the wrong number. | `src/rudra/__init__.py:3` vs `pyproject.toml:7` |
| A1.2 | PENDING | Session ID is a fresh `uuid4()` every run → LangGraph checkpoints in `.rudra/checkpoints.db` are written but **never resumed**. No cross-run continuity. | `src/rudra/agent/main_agent.py:570` |
| A1.3 | PENDING | `get_or_create_session_id()` exists precisely to fix A1.2 and has **zero call sites**. | `src/rudra/state/checkpoint.py:19` |
| A1.4 | PENDING | Disk write failures silently swallowed, then the file is marked as written. Agent believes a failed write succeeded. | `src/rudra/filesystem/virtual_fs.py:206-209` (`except Exception: pass`) — resolved by D7 deletion |
| A1.5 | PENDING | `.env.example` sets `OLLAMA_NUM_PREDICT=2048`; `config.py` defaults to `131072`. A user copying the example gets files truncated at 2048 tokens. | `.env.example:14` vs `src/rudra/config.py:22` |
| A1.6 | PENDING | `EnforceTargetFileMiddleware` accepts any path with a matching **basename** → target `src/models.py` permits writing `tests/models.py`. | `src/rudra/middleware/enforce_target_file.py:36-42` — resolved by D4 deletion |
| A1.7 | PENDING | `_parse_pending_files` treats every `- [ ]` line as a filename. A planner emitting `- [ ] Set up auth` creates a file literally named `Set up auth`. | `src/rudra/agent/main_agent.py:53-58` |
| A1.8 | PENDING | Task success = file exists on disk. An empty or syntactically broken file counts as success and gets ticked off. | `src/rudra/agent/main_agent.py:411-416` |
| A1.9 | PENDING | `.rudra/AGENTS.md` created once, **never written again**. Long-term memory is a stub. | `src/rudra/agent/main_agent.py:446-474`; no other writer |
| A1.10 | PENDING | `_is_ignored` runs `fnmatch` on raw `.gitignore` lines. Real gitignore semantics (`!`, leading `/`, `**`, trailing `/`) not honored. | `src/rudra/filesystem/virtual_fs.py:78-112` — resolved by D7; 0.7.4 ships `wcmatch` for this |
| A1.11 | PENDING | `watch` busy-loops `asyncio.run(asyncio.sleep(1))` — builds and tears down an event loop once per second. | `src/rudra/cli.py:409` |
| A1.12 | PENDING | `aiosqlite` imported at runtime, not declared in `pyproject.toml` (only transitive). | `src/rudra/agent/main_agent.py:546`; `pyproject.toml:25-59` |
| A1.13 | PENDING | `langgraph-checkpoint-sqlite` listed twice in dependencies. | `pyproject.toml:30` and `:58` |
| A1.14 | PENDING | Project URLs point to `github.com/archish/Rudra`; real remote is `github.com/archish9/RudraAnvil`. | `pyproject.toml:65-66` |
| A1.15 | PENDING | `config` instantiated at import time → env changes after import ignored, no per-project config file possible. | `src/rudra/config.py:68` |
| A1.16 | PENDING | Files silently overwritten — no diff, no backup, no confirm. Data-loss path on a real repo. **Safety issue.** | `src/rudra/compat/overwrite_backend.py:50-65` — 0.7.4 `permissions=` + `interrupt_on=` is the fix |
| A1.17 | PENDING | **Summarization never fires for local models.** `create_deep_agent` auto-adds `create_summarization_middleware`, whose threshold comes from the model profile: 85% of context if a profile exists, else a fixed **170,000-token** fallback (`summarization.py:249-286`). Verified: `ChatOllama.profile is None` → Rudra takes the fallback. A 32B model (D6) has 32k–128k context, so it **overflows long before 170k**. The only real context-management mechanism in the product is inert for its primary target. Fix: set `trigger`/`keep` explicitly per role from configured context size, or register a model profile. | `.venv/.../deepagents/middleware/summarization.py:249-286`; `python -c "ChatOllama(...).profile"` → `None` |

### A2 — Dead code

| # | Status | Item | Evidence |
|---|---|---|---|
| A2.1 | PENDING | `execution_tools.py` — 255 lines at repo root, zero imports. **Delete.** | `grep -rn execution_tools . --exclude-dir=.venv` |
| A2.2 | PENDING | `filesystem/sync.py` — 271 lines, unreachable. **Delete (D7).** | `src/rudra/filesystem/sync.py`; `cli.py:18` imports, never calls |
| A2.3 | PENDING | `ContinueAfterWriteMiddleware` — 105 lines, zero call sites. **Delete (D4).** | `src/rudra/middleware/continue_after_write.py` |
| A2.4 | PENDING | `code_tools.py` given to no agent — only `watch` uses it. Agents cannot run any command. | `src/rudra/cli.py:393`; `coder_agent.py:80` `tools=[]` |
| A2.5 | PENDING | Unused imports: `asyncio`, `create_deep_agent` in `main_agent.py:5,11`; `FileSyncManager, SyncMode` in `cli.py:18`. | ruff F401 |
| A2.6 | PENDING | `AgentConfig.max_iterations`/`max_agents`/`checkpoint_interval` and `AgentContext.max_iterations`/`file_path`/`issue` never read. | `config.py:29-31`; `main_agent.py:30,34,35` |

### A3 — Quality gates

| # | Status | Item | Evidence |
|---|---|---|---|
| A3.1 | PENDING | `ruff check src/ execution_tools.py` → **213 errors** (165 auto-fixable). | command output 2026-08-05 |
| A3.2 | PENDING | `tests/` is empty. Zero tests, though pytest is configured. | `ls tests/`; `pyproject.toml:79-81` |
| A3.3 | PENDING | No CI. | no `.github/` |
| A3.4 | PENDING | `ruff` and `pytest` are runtime dependencies; belong in a `dev` extra. | `pyproject.toml:46-48` |

### A4 — Docs / OSS readiness

| # | Status | Item | Evidence |
|---|---|---|---|
| A4.1 | PENDING | README documents subcommands `build`, `chat`, `fix`, `edit`, `review`, `suggest`, `resume` that **do not exist**; also documents the static 4-question tech-stack form (§0.5 surface #7) and a `.rudra/session_id.txt` that is never written (A1.3). | `README.md:351-510,626-636` vs 1 `@app.command` in `cli.py` |
| A4.2 | PENDING | `license = "Apache-2.0"` declared, no LICENSE file on disk. | `pyproject.toml:10` |
| A4.3 | PENDING | Repo tracks noise: `q-dev-chat-2026-03-20.md` (199 KB), `filesystem-context.txt`, `improvements.txt`, `llms.txt`, `execution_tools.py`. | `git ls-files` |
| A4.4 | PENDING | `requirements.txt` (pinned) and `pyproject.toml` (ranges) list different sets and will drift. Pick one source of truth. | both files |
| A4.5 | PENDING | No CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue templates. | `ls` |
| A4.6 | PENDING | pyproject `keywords`/`description` still say "ollama" — contradicts provider-agnostic goal. | `pyproject.toml:8,15` |
| A4.7 | PENDING | Vendored superpowers (MIT, Jesse Vincent) + MemPalace (MIT) under Apache-2.0 need a NOTICE / THIRD_PARTY file. | licenses verified |
| A4.8 | **DONE** 2026-08-05 | **`.gitignore` ignored `docs/` and `tests/`.** Fixed: removed both lines; `docs/_build/` (`.gitignore:73`) still covers sphinx output. Verified `git check-ignore -v docs tests` → exit 1 (no match). `tests/` being ignored makes the step-4 CI gate structurally impossible — nothing under `tests/` can ever be tracked, though `pyproject.toml:79-81` configures `testpaths = ["tests"]`. This is the real reason behind A3.2, not merely "nobody wrote tests yet". `docs/` blocks C10.3 (design docs live in `docs/`). **Blocks Step 1** (contract tests + spec are both uncommittable). | `.gitignore:215-216`; `git check-ignore -v docs tests` |

---

## SECTION B — Requirement Gap Map

| Requirement | State | Evidence |
|---|---|---|
| Any API provider | ❌ Ollama hardcoded | `config.py:12-22`, `planner_agent.py:75`, `coder_agent.py:61`; `langchain-openai` not installed |
| MCP | ❌ Nothing | no MCP package, no config surface |
| Tools | ⚠️ built-ins only; Rudra's tools unwired; **no shell** | `coder_agent.py:80`; plain `FilesystemBackend` → `execute` errors |
| Skills | ❌ `skills=` supported by framework, never passed | `grep -rn "skills=" src/` → 0 |
| Loop engineering | ❌ Hardcoded Python loop, no verify stage | `main_agent.py:345-437` |
| Dynamic clarifying questions | ❌ `ask_user` exists, actively blocked | `middleware/block_premature_ask.py:20-23` |
| Progress tracking | ⚠️ Checkbox ticked by file-existence | `main_agent.py:61,411` |
| Context management | ⚠️ Partly by accident; checkpoints unusable (A1.2), AGENTS.md never updated (A1.9) | CLAUDE.md §3 |
| Superpowers | ❌ Nothing | — |
| MemPalace | ❌ Nothing | — |
| Coding / testing / review / fix | ⚠️ Coding only | no test runner, no reviewer, no fix loop |
| Git awareness | ❌ Nothing | no git code in `src/` |
| Diff preview / approval | ❌ Silent overwrite | A1.16 |

---

## SECTION C — Work Plan

Phases are dependency-ordered. Execution order across phases is in [Section E](#section-e--execution-order).

### Phase U — deepagents 0.7.4 upgrade (**do before anything else**)

Findings that drive this phase are in [Section F](#section-f--deepagents-074-upgrade-findings).

| # | Status | Item |
|---|---|---|
| U.1 | PENDING | Bump `deepagents==0.7.4`. Forces `langchain>=1.3.14` (have 1.2.13) and `langchain-core>=1.5.0` (have 1.2.23). Bump `langchain-ollama` → 1.1.0 (`langchain-core>=1.2.21`, compatible) |
| U.2 | PENDING | New transitive hard deps: `langchain-anthropic>=1.5.3`, `langchain-google-genai>=4.3.1`, `langsmith>=0.10.9`, `wcmatch>=11.0`, `packaging>=23.2`. Declare intentionally |
| U.3 | PENDING | **Delete `compat/overwrite_backend.py`.** 0.7.4 `FilesystemBackend.write()` overwrites by default (`backends/filesystem.py:489`, uses `O_TRUNC` + `O_NOFOLLOW`). ~~Only the markdown-fence stripping was still load-bearing → moves to `FixWriteParamsMiddleware` (D4)~~ **CORRECTED 2026-08-05: nothing needs porting.** `FixWriteParamsMiddleware` already strips fences (`fix_write_params.py:19`, `:31-33`, `:63-64`). U.3 reduces to swapping the constructor at `main_agent.py:554` and deleting the 65-line file — its sole instantiation. Two gaps the deletion exposes are tracked as U.14 and U.15 |
| U.4 | PENDING | Re-verify `compat/deepagents_path.py`. `validate_path` still exists at `backends/utils.py:648` and is still imported into `middleware/filesystem.py:77`, so the two-target monkeypatch still applies — but re-test, and add a version assert that fails loudly on a deepagents bump |
| U.5 | PENDING | **`TodoListMiddleware` is no longer auto-added** in 0.7.4's main stack (`graph.py:634` comment; it appears only inside the Codex harness profile). If `write_todos` is wanted, pass it explicitly with `system_prompt=""` per upstream's own note |
| U.6 | PENDING | `backend` param is now typed `BackendProtocol` only (no `BackendFactory`). Rudra passes an instance — verify no factory usage creeps in |
| U.7 | PENDING | Adopt `permissions: list[FilesystemPermission]` — new first-class param with `allow` / `deny` / `interrupt` modes (`middleware/filesystem.py:382-416`). This replaces most of hand-rolled C3.3 |
| U.8 | WONTFIX | `RubricMiddleware` (`middleware/rubric.py`) — **not adopted** per D9. Grader-subagent loop overlaps the reviewer subagent (C6.3) and would gate termination on an LLM verdict. Rationale + revisit trigger in §0.2. Left here so a future session does not "discover" it and re-litigate |
| U.9 | PENDING | Adopt provider profiles (`deepagents.profiles.provider`): `register_provider_profile` + built-in OpenRouter / NVIDIA / OpenAI profiles feed `init_chat_model` kwargs. Feeds directly into Phase 1 |
| U.10 | PENDING | Evaluate harness profiles (`HarnessProfile`, `register_harness_profile`, `excluded_tools`) for per-model tool visibility and prompt tuning — a clean home for per-model quirks instead of Rudra middleware |
| U.11 | PENDING | Note `AsyncSubAgentMiddleware` / `AsyncSubAgent` (`graph_id`-keyed) exists for out-of-process subagents. Not needed now; record for Phase 6 |
| U.12 | PENDING | Smoke test end-to-end on 0.7.4 before touching anything else. This is the riskiest single change in the repo. Target model: `gemma4:31b-cloud` via Ollama cloud (requires `ollama signin` — currently returns `{"error":"unauthorized"}`) |
| U.13 | PENDING | **Private deepagents API import.** `task_anchor.py:20` does `from deepagents.middleware._utils import append_to_system_message`. A leading-underscore module carries no stability guarantee, and `TaskAnchorMiddleware` is KEEP-opt-in per D4, wired into **both** agents (`planner_agent.py:93`, `coder_agent.py:71`). Third monkeypatch-class breakage risk alongside U.4. Add a guard that fails with an actionable message, plus a contract test | `src/rudra/middleware/task_anchor.py:20` |
| U.14 | PENDING | **Planner loses fence-stripping once U.3 lands.** `FixWriteParamsMiddleware` is on the coder only (`coder_agent.py:70`); the planner carries only `TaskAnchorMiddleware` + `BlockPrematureAskMiddleware`. Today `OverwriteFilesystemBackend` covers the planner; after deletion nothing does. Add `FixWriteParamsMiddleware()` to the planner middleware list | `src/rudra/agent/planner_agent.py:92-95` vs `coder_agent.py:70` |
| U.15 | PENDING | **Fence regex narrower than the backend's.** Middleware `_FENCE_RE` uses `[a-zA-Z0-9_\-]*` for the info string; the backend used `[^\n]*`. Fenced blocks with attributes (` ```py title="x" `) match the backend but not the middleware → coverage shrinks at U.3. Widen to `[^\n]*` | `middleware/fix_write_params.py:19` vs `compat/overwrite_backend.py:43` |
| U.16 | **DONE** 2026-08-05 | **0.7.4 API discovery, verified against a disposable `.venv-probe`.** `uv pip install --dry-run` resolved 78 packages cleanly, exit 0 (deepagents 0.7.4, langchain 1.3.14, langchain-core 1.5.3, langchain-ollama 1.1.0, langgraph 1.2.10) — real install also exit 0, no constraint conflict, so no version loosening needed. `validate_path` is present and `callable` in both `deepagents.backends.utils` and `deepagents.middleware.filesystem`, and `is` confirms it is the same object → U.4's two-target monkeypatch is still required. `deepagents.middleware._utils.append_to_system_message` is present with signature `(system_message: SystemMessage \| None, text: str) -> SystemMessage` → U.13's import path is stable on 0.7.4. `FilesystemBackend.write()` overwrites (write #1 `a`, write #2 `b`, on-disk content `b`) and preserves markdown fences verbatim (fence-preserved check: `True`, i.e. 0.7.4 does **not** strip fences itself) → confirms U.3's "nothing needs porting" and means U.15's fence-stripping fix is still load-bearing, not cosmetic (**Blocker B did not trip**). `virtual_mode=True` writes through to disk — `landed on disk: True`, content `x` read back correctly (**Blocker A did not trip**; `main_agent.py:556`'s straight swap to plain `FilesystemBackend(virtual_mode=True)` is safe). `create_deep_agent` signature now has 18 params incl. new `permissions`, `skills`, `interrupt_on`, `cache` (U.6/U.7/U.9 raw material). `write_todos` is absent from the compiled agent's default tool stack — the `'tools'` node (accessor: `agent.nodes['tools'].bound.tools_by_name`) lists exactly `['delete', 'edit_file', 'execute', 'glob', 'grep', 'ls', 'read_file', 'task', 'write_file']` → confirms U.5. `ChatOllama` + `create_deep_agent` construction made no network call (script ran to completion, no hang) even with an unreachable `base_url`. Raw output: scratchpad `probe_074.out` | `.venv-probe`, `probe_074.py` |

### Phase 0 — Stabilize

| # | Status | Item |
|---|---|---|
| C0.1 | PENDING | Fix all of A1 not already resolved by U/D7 |
| C0.2 | PENDING | Execute D4 + D7 deletions: `execution_tools.py`, `filesystem/sync.py`, `filesystem/virtual_fs.py`, `block_premature_ask.py`, `block_task_tool.py`, `continue_after_write.py`, `enforce_target_file.py`, `compat/overwrite_backend.py`. **Note:** deleting `block_premature_ask.py` does *not* end the ask-blocking — its prompt-level twin at `planner_agent.py:60` survives. Tracked as §0.5 surface #6 |
| C0.3 | PENDING | Replace VFS with a capped `project_tree()` helper: names only, depth + entry cap, `wcmatch` gitignore semantics, **never loads file content** |
| C0.4 | PENDING | `ruff check --fix` + resolve remainder; add `ruff format` |
| C0.5 | PENDING | pytest suite: path normalizer, PLAN parsing, config layering, model factory, tree helper |
| C0.6 | PENDING | GitHub Actions CI: ruff + pytest on 3.12/3.13 |
| C0.7 | PENDING | Move `ruff`/`pytest` to `[dependency-groups] dev`; drop duplicate dep; add `aiosqlite` |
| C0.9 | PENDING | **Implement the D15 `.rudra/` layout** — durable vs `run/` volatile split, plus Rudra writing `.rudra/.gitignore`. Do it in Stage I: every later phase (checkpoints C7.2, palace C8.1a, logs C3.2, facts C6.8a) writes into this tree, so settling paths now avoids migrating them four times |
| C0.10 | **DONE** 2026-08-05 | **Deleted dev debris:** `.rudra/` and `coding-files/` (10 files, 376 KB) — output from test runs of `rudra` against its own checkout. All untracked (`git ls-files -- .rudra coding-files` → empty), so no history impact. Verified gone; `git status --short` shows only the new `CLAUDE.md` / `TODO.md` |
| C0.8 | PENDING | Pin `deepagents==0.7.4` exactly while `compat/deepagents_path.py` monkeypatches exist, with a comment pointing at U.4 |

### Phase 1 — Provider-agnostic model layer

| # | Status | Item |
|---|---|---|
| C1.1 | PENDING | `src/rudra/llm/factory.py`: `build_model(role) -> BaseChatModel`. Providers: `ollama`, `openai_compatible` (vLLM / OpenRouter / LM Studio / Groq / Together), `anthropic`, `openai`, `google`. Layer on `register_provider_profile` (U.9) rather than reimplementing |
| C1.2 | PENDING | Add `langchain-openai>=1.4.1` — required for every OpenAI-compatible endpoint |
| C1.3 | PENDING | Delete `OllamaConfig`; replace with role-based `ModelConfig` (`planner`, `coder`, `reviewer`, `summarizer`). Keep `OLLAMA_*` env working one release with a deprecation warning |
| C1.4 | PENDING | Move Ollama-only kwargs (`num_predict`, `reasoning=True`) behind a provider check — invalid elsewhere and will raise on Anthropic/OpenAI (`planner_agent.py:78-80`, `coder_agent.py:64-66`) |
| C1.4a | PENDING | **Declare `context_tokens` per role and wire it into summarization — fixes A1.17.** Without it every local model silently gets the 170k fallback trigger and never compacts. Belongs here, not Phase 7: only the model factory knows the model's real context size |
| C1.5 | PENDING | API keys resolved only from env-var names named in config; never stored in TOML |
| C1.6 | PENDING | `rudra models test` — verify each role is reachable **and supports tool calling** (deepagents hard-requires it) |
| C1.7 | PENDING | Fail fast with a clear message on no-tool-calling models instead of crashing mid-run |
| C1.8 | PENDING | `[compat]` section for the two surviving middlewares (D4), default off |

### Phase 2 — TOML config (D1)

| # | Status | Item |
|---|---|---|
| C2.1 | PENDING | Layered loader: builtin → `~/.config/rudra/config.toml` → `<project>/.rudra/config.toml` → `RUDRA_*` env → CLI flags. Schema in CLAUDE.md §6 |
| C2.2 | PENDING | `rudra init` — scaffold `.rudra/config.toml` with a commented example per provider |
| C2.3 | PENDING | `rudra config get/set/list` + `rudra doctor` (provider reachability, MCP servers, skill dirs, MemPalace store) |
| C2.4 | PENDING | Config validation with actionable errors |
| C2.5 | PENDING | `[permissions] mode = "ask"` default (D5) + `--auto` / `--yolo` flag + `/auto` REPL toggle + `--plan` |

### Phase 3 — Real tool layer

| # | Status | Item |
|---|---|---|
| C3.1 | PENDING | Switch backend to `LocalShellBackend` so `execute` works. No overwrite subclass needed post-U.3. **Build it as the `default` of a `CompositeBackend` from the start (D13 / C5.2b)** — retrofitting the composite later means rewiring every agent constructor |
| C3.2 | PENDING | Retire `code_tools.run_command` in favor of backend `execute`; **keep the log-offload behavior** (big output → `.rudra/logs/`, short preview returned) — good context hygiene worth porting |
| C3.3 | PENDING | Permission layer on top of `permissions=` (U.7) + `interrupt_on=`: mode from config (D5), allow/deny path rules, per-session grants, audit log. **Required before shell ships** — `LocalShellBackend` runs arbitrary commands as the user |
| C3.4 | PENDING | Diff preview before write/edit approval prompts. Fixes A1.16 |
| C3.5 | PENDING | Git tools: status, diff, branch, commit, log. Auto-branch before an agent run; never commit unless asked |
| C3.6 | PENDING | Test-runner tool: detect pytest / jest / go test / cargo, run, parse failures into structured results the fix loop consumes |

### Phase 4 — MCP

| # | Status | Item |
|---|---|---|
| C4.1 | PENDING | Add `langchain-mcp-adapters>=0.3.1` (needs `mcp>=1.24.0`); load stdio + HTTP/SSE servers |
| C4.2 | PENDING | `.mcp.json` using the Claude-Code-compatible `{"mcpServers": {...}}` schema so users paste existing configs verbatim |
| C4.3 | PENDING | `rudra mcp add/list/remove/test` |
| C4.4 | PENDING | Lazy tool-schema loading + per-server allow/deny — a dozen servers will still blow a 32B context if loaded eagerly |
| C4.5 | PENDING | Tool namespacing (`server__tool`) to avoid collisions |
| C4.6 | PENDING | Decide the shipped default bundle. Candidates: filesystem, git, fetch |

### Phase 5 — Skills + Superpowers (vendored, D2)

| # | Status | Item |
|---|---|---|
| C5.1 | PENDING | Pass `skills=[...]` to `create_deep_agent`. Sources: bundled `rudra/skills/`, `~/.rudra/skills/`, `<project>/.rudra/skills/`. Later sources win (framework behavior) |
| C5.2 | PENDING | **Vendor all 14 skills** (D11) into `src/rudra/skills/superpowers/` — including the **36 supporting files** (`references/*.md`, `find-polluter.sh`, `render-graphs.js`, prompt templates). Format is already compatible: `<name>/SKILL.md` with `name:`/`description:` frontmatter is exactly what `SkillsMiddleware` parses. Record the upstream commit SHA in `VENDORED.md` |
| C5.2a | PENDING | **Skill cache builder (D13).** Materialize `${XDG_CACHE_HOME:-~/.cache}/rudra/skills/<key>/{library,active}/` from the read-only vendored copy, applying the C5.4 rename transform. `<key>` = hash(rudra version + upstream SHA + transform version + sorted enabled list). Auto-rebuild on key mismatch; `rudra skills rebuild` to force. Must never write into the install dir. Layout + rationale in §0.4 |
| C5.2b | PENDING | **`CompositeBackend` wiring (D13):** `default=LocalShellBackend(project_path)`, `routes={"/skills/": FilesystemBackend(root_dir=cache_dir, virtual_mode=True)}`, then `skills=["/skills/active/"]`. **Couples with C3.1** — build the composite once, in one place, not separately per phase. Verify `execute` still reaches the project root through the composite (`composite.py:774` says it delegates to `default`) |
| C5.2c | PENDING | Cross-references inside rewritten skills resolve to `/skills/library/<name>/SKILL.md` — the canonical path, independent of enablement. Enforce this in the C5.4 transform so C5.9 never has to rewrite references |
| C5.3 | PENDING | Port the superpowers **bootstrap** (`using-superpowers`, 62 lines): its instruction block must be injected into the system prompt. Skills without it are inert — this is the piece that makes the agent reach for them |
| C5.4 | PENDING | **Rewrite for Rudra tool names (D10).** 101 sites across all 14; **~38 in the 8 enabled** ones. `Task`→`task`, `Write`/`Read`/`Edit`→`write_file`/`read_file`/`edit_file`. Mapping table in §0.3. Must scan the 36 supporting files too, not just `SKILL.md` |
| C5.4a | PENDING | **`Skill` tool has no deepagents equivalent — 20 sites.** deepagents exposes no skill-invoke tool; progressive disclosure works via `read_file` on the path shown in the skill index (`skills.py:738`, `:757`). Rewrite these as `read_file(file_path=...)`. This is the one non-mechanical part of C5.4 — do it first and confirm a 32B model follows the pattern |
| C5.4b | PENDING | Set up the rewrite so `rudra skills update` (C5.6) stays workable. D10 forks the vendored copy → update becomes a 3-way merge against upstream, not a copy. Keep the rename as a **scripted, re-runnable transform** over a pristine upstream checkout rather than hand-edited files |
| C5.4c | PENDING | Defer the rewrite of the 2 off-by-default heavyweights — `writing-skills` (31 sites) and `subagent-driven-development` (27 sites) — until they are enabled. Together they are 58 of the 101 sites |
| C5.5 | PENDING | Port the superpowers hooks concept (`hooks/session-start`) — the session-start injection has no Rudra equivalent yet |
| C5.6 | PENDING | `rudra skills list/add/remove/validate/update` (`update` re-runs the C5.4b transform against a newer pinned upstream ref) |
| C5.7 | PENDING | NOTICE / THIRD_PARTY attribution for the vendored MIT code (A4.7). Must state that the skills were **modified** — MIT requires the notice, and the rename pass makes them derivative |
| C5.8 | PENDING | Verify skill selection works at 32B (D6): does the model pick the right skill from 8 descriptions alone, then `read_file` the body? Progressive disclosure keeps the index small — the risk is *selection quality* and *follow-through on the read step*, not token cost |
| C5.9 | PENDING | Enable the 5 deferred skills as their prerequisites land: `using-git-worktrees` + `finishing-a-development-branch` after C3.5 (git); `subagent-driven-development` + `dispatching-parallel-agents` after C6.2 (subagents); `writing-skills` whenever user-authored skills ship. Each needs its C5.4 rewrite done at that point |

### Phase 6 — Agent architecture rewrite

The hand-rolled loop is the root cause of most limitations.

| # | Status | Item |
|---|---|---|
| C6.1 | PENDING | Replace the file-by-file Python loop (`main_agent.py:345-437`) with a real agentic loop: the agent owns the todo list, picks the next action, verifies its own work |
| C6.2 | PENDING | Use `subagents=[...]` for coder / tester / reviewer roles, each with its own model. `BlockTaskToolMiddleware` already deleted in C0.2 |
| C6.3 | PENDING | Reviewer subagent: reads the diff, reports issues, does not edit |
| C6.4 | PENDING | Tester subagent: writes and runs tests, feeds failures back |
| C6.5 | PENDING | **Hand-rolled fix loop (D9):** `write → verify (C6.6) → diagnose → fix → reverify`. Deterministic gate only — no LLM judge decides termination |
| C6.5a | PENDING | **Loop bounds.** Max fix attempts per task + **no-progress detection**: same test failing with the same error twice → stop and escalate to the user. Without this the loop burns tokens forever on a bug the model cannot fix — the one way Option A is worse than `RubricMiddleware`, which at least ships `max_iterations`. Prior art in this repo: `main_agent.py:210` (3 consecutive failures → halt) and `:262` (same tool call 3× → halt). Carry the pattern, not the code |
| C6.6 | PENDING | Replace file-existence success (A1.8) with a **deterministic verification gate**: syntax check → lint → type check → test → **stub scan** (grep for `TODO`, bare `pass`, `NotImplementedError`, empty function bodies). The stub scan is what closes Option A's blind spot — an agent can pass tests and still leave placeholders. Zero token cost |
| C6.7 | PENDING | Three-stage planning: clarify → architect (folder structure, stack, decisions) → task breakdown. Persist decisions **and rationale**, not just filenames |
| C6.8 | PENDING | **Dynamic clarifying questions.** Bounded clarification phase: agent asks only what it cannot infer, batches related questions, has a question budget. Model-driven, never a static script. `BlockPrematureAskMiddleware` already deleted in C0.2 |
| C6.8a | PENDING | **Purge static Q&A — all 7 surfaces.** The fixed language/framework/database questionnaire is not one file, it is threaded through the schema, the tool, the prompts, and two generated files. Full inventory and target design in [§0.5](#05--static-qa-purge-c68a). Deleting only `ProjectContext` leaves 6 surfaces that re-impose the same 4 boxes |
| C6.9 | PENDING | Plan mode: present plan → approve → execute |
| C6.10 | PENDING | Progress ledger the agent maintains itself (supersedes the PLAN.md checkbox hack) |

### Phase 7 — Context management

| # | Status | Item |
|---|---|---|
| C7.1 | PENDING | Document inherited vs designed. Today only `create_summarization_middleware` (auto-added) does real context management |
| C7.2 | PENDING | Fix checkpoint continuity (A1.2 + A1.3); add `rudra --continue` / `--resume` |
| C7.3 | PENDING | Make `.rudra/AGENTS.md` a living document (A1.9) — updated after each completed task |
| C7.4 | PENDING | Add `create_summarization_tool_middleware` → `compact_conversation` tool + `/compact` command |
| C7.5 | PENDING | Token accounting + context-usage indicator in the REPL |
| C7.6 | PENDING | **Per-role context budgets — fixes A1.17.** Add `context_tokens` to each `[model.*]` role in TOML; construct `SummarizationMiddleware` explicitly with `trigger=("fraction", 0.85)` against that number instead of accepting the 170k fallback. Alternative: register a langchain model profile carrying `max_input_tokens` so the fraction path activates. **Decide which in C1.1** — it is a model-factory concern, not a Phase 7 one |
| C7.7 | PENDING | Codebase indexing so the agent finds relevant files without loading the tree. Blocked on C0.3 (VFS removal) |
| C7.8 | PENDING | Evaluate 0.7.4's `_message_eviction.py` / `_overflow_clip.py` internals — they may already cover part of C7.5/C7.6 |

### Phase 8 — MemPalace via Python API (D3)

| # | Status | Item |
|---|---|---|
| C8.1 | PENDING | Add `mempalace>=3.6.0`. Wrap the Python API behind `src/rudra/memory/` so the backend stays swappable |
| C8.1a | PENDING | **Project-scoped palace (D14):** construct `Config(palace_path=<project>/.rudra/memory/palace)`. `tunnels.json` / `hallways.json` follow automatically as siblings. Layout in §0.6 |
| C8.1b | PENDING | **Isolate from the user's global MemPalace config.** `~/.mempalace/config.json` is read as file-config (`config.py:364`, `:393`) and would silently override Rudra's embedding model / backend. Pass every setting explicitly. Add a `rudra doctor` check that reports when a user-global config exists and is being ignored |
| C8.1c | PENDING | Audit the remaining user-global paths — `entity_registry.json` (`entity_registry.py:299`), `hook_state/`, diary `state/`. Confirm they stay dormant on Rudra's integration path, or scope them too |
| C8.2 | PENDING | Default vector store = ChromaDB (embedded, `chromadb>=1.5.4` is already a mempalace dep). Expose SQLite / Milvus / Qdrant / pgvector via TOML. Note per-project disk cost — one ChromaDB store per project |
| C8.3 | PENDING | Define what gets written: architecture decisions + rationale, task outcomes, user preferences, bug→fix pairs. **Not raw transcripts** |
| C8.4 | PENDING | Define retrieval triggers: session start, before planning, on "why did we…" questions |
| C8.5 | PENDING | **Embedding model pre-fetch (D12).** mempalace pulls `huggingface-hub` + `tokenizers` → the model is downloaded, not bundled (`embedding.py:7-20`). Add an explicit warm step in `rudra init` and a check in `rudra doctor`; never let it download lazily mid-task. The HF cache is user-global, so this is a **one-time cost shared by all projects** — document size, cache location, and how to pre-seed for an air-gapped install |
| C8.5a | PENDING | **Memory durability.** Periodically `export_palace(..., format="markdown")` (`exporter.py:68`) into `.rudra/memory/export/` — the durable subtree per D15 — and support re-import on a fresh clone. The palace is binary and churns; the export is the portable copy |
| C8.6 | PENDING | Degrade gracefully: missing or corrupt store must not stop Rudra |
| C8.7 | PENDING | `rudra memory search/list/forget` + privacy docs (what is stored, where, how to purge) |
| C8.8 | PENDING | Integration tests against a real MemPalace store |
| C8.9 | PENDING | Bridge memory ↔ `.rudra/AGENTS.md` (C7.3) so short-term and long-term memory don't diverge |

### Phase 9 — UX / production polish

| # | Status | Item |
|---|---|---|
| C9.1 | PENDING | Token-level streaming. Today output is truncated at 300 chars per message (`main_agent.py:117`) |
| C9.2 | PENDING | Fix README vs reality (A4.1). Keep natural-language-first; add `/`-commands in the REPL; delete stale README sections |
| C9.3 | PENDING | Ctrl-C cancels the current agent turn, not just prints a hint (`cli.py:313`) |
| C9.4 | PENDING | REPL: history, multiline input, `@`-file mentions, `/`-command completion |
| C9.5 | PENDING | Session transcript persistence + `rudra resume` |
| C9.6 | PENDING | Cost / latency / token reporting per run |
| C9.7 | PENDING | Structured logging behind `--debug`, separate from the user-facing trace |
| C9.8 | PENDING | **Lazy-import the agent module.** `cli.py:16` imports `rudra.agent` at module level, so even `rudra --version` / `--help` pays the full deepagents import. Measured **0.58–0.61 s** startup; breakdown `rudra.cli` 503 ms → `deepagents` 376 ms → `langchain.agents` 234 ms → `langchain_anthropic` 132 ms. Defer the import into the command body |
| C9.9 | PENDING | **Use `ripgrep` for the grep tool when `rg` is on PATH**, falling back to the Python implementation. Only real CPU win available on large repos; not installed on this machine |

### Phase 10 — OSS launch

| # | Status | Item |
|---|---|---|
| C10.1 | PENDING | LICENSE file (A4.2) + NOTICE / THIRD_PARTY (A4.7, C5.7) |
| C10.2 | PENDING | Rewrite README against actual behavior; provider setup matrix (Ollama / vLLM / OpenRouter / Anthropic / OpenAI) |
| C10.3 | PENDING | Clean repo noise (A4.3); move design docs to `docs/` |
| C10.4 | PENDING | CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue/PR templates (A4.5) |
| C10.5 | PENDING | PyPI publish; `uv tool install rudra` / `pipx install rudra` |
| C10.6 | PENDING | Security docs: `LocalShellBackend` grants arbitrary command execution as the user. State the permission model and its limits plainly |
| C10.7 | PENDING | Fix pyproject metadata (A1.14, A4.6) |

---

## SECTION E — Execution Order

Each numbered step is one fresh session with its own self-contained plan under `plans/`. Do not start a step until its dependencies are `DONE`.

### Stage I — Make the base sound (no new features)

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **1** | **U.1 – U.6, U.12 – U.15** — deepagents 0.7.4 upgrade + smoke test. Design spec: `docs/superpowers/specs/2026-08-05-deepagents-074-upgrade-design.md` | A4.8 (**DONE**) | Riskiest change in the repo. Every later phase builds on 0.7.4 APIs. Doing it after new code means porting twice. A4.8 was pulled forward and fixed first — `tests/` was gitignored, so the contract tests could not be committed |
| **2** | **C0.2, C0.3, A2.1–A2.6, D7** — delete dead code, delete VFS, add capped `project_tree()` | 1 | Removes ~1,100 lines. Everything after touches less surface. Memory budget fix (D7) |
| **3** | **A1.1, A1.5, A1.7, A1.11–A1.15, C0.4, C0.7, C0.8** — correctness bugs + ruff + dep hygiene | 2 | Cheap, mechanical, unblocks CI |
| **4** | **C0.5, C0.6, A3.1–A3.4** — pytest suite + CI green | 3 | **Gate.** No further work without a regression net |

### Stage II — Make it configurable (unblocks everything user-facing)

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **5** | **C1.1 – C1.8, U.9** — provider-agnostic model factory | 4 | Requirement #1. Also the precondition for testing anything on a 32B non-Ollama model |
| **6** | **C2.1 – C2.5** — TOML config + permission mode plumbing | 5 | Every later phase (MCP, skills, memory) needs a config surface. Building them first means retrofitting three times |

### Stage III — Make it capable

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **7** | **C3.1 – C3.4, U.7** — `CompositeBackend(default=LocalShellBackend)` + permissions + diff approval | 6 | Shell unlocks tests/lint/git. **Permissions land in the same step, never after** — shipping shell without them is the single biggest safety hole. **Build the composite here even though its `/skills/` route is empty until step 11** (D13 / C5.2b) — retrofitting it later rewires every agent constructor |
| **8** | **C3.5, C3.6** — git tools + test-runner tool | 7 | Both need shell. Test-runner output is the input to the fix loop |
| **9** | **C6.1 – C6.6, C6.5a, C6.10, U.11** — agentic loop rewrite: subagents, reviewer, tester, hand-rolled fix loop | 8 | Requirement #3 + #4. Needs shell + test runner to have anything to verify against. Build C6.6 (deterministic gate) **before** C6.5 — the loop has nothing to loop on until the gate exists |
| **10** | **C6.7 – C6.9** — three-stage planning, dynamic clarifying questions, plan mode | 9 | Requirement #1 (planning before coding). Sits on the new loop |

### Stage IV — Make it smart

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **11** | **C5.1 – C5.9** — skills + vendored superpowers; add the `/skills/` route to the step-7 composite | 10 | Superpowers skills assume subagents and a todo system — both exist only after step 9/10. Vendoring earlier means auditing against an architecture that is about to change |
| **12** | **C7.1 – C7.8** — context management: checkpoints, living AGENTS.md, compaction, budgets, indexing | 11 | Needs the final agent shape and skill/tool payload sizes to budget against |
| **13** | **C4.1 – C4.6** — MCP | 12 | Needs config (6), the permission system (7), and context budgeting (12) — a dozen MCP servers will blow a 32B window without lazy loading |
| **14** | **C8.1 – C8.9** — MemPalace long-term memory | 13 | Largest new subsystem. Needs a stable definition of "what a completed task looks like" (step 9) to know what is worth storing |

### Stage V — Ship

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **15** | **C9.1 – C9.7** — streaming, REPL, cancellation, cost reporting | 14 | Polish against final behavior |
| **16** | **C10.1 – C10.7, A4.1 – A4.7** — LICENSE, NOTICE, README rewrite, repo cleanup, PyPI | 15 | Docs written last describe what actually shipped |

### Parallelizable

These have no cross-dependencies and can run in a separate session alongside their stage:

- **A4.2 + A4.7 (LICENSE + NOTICE)** — do any time; needed before the first public push
- **A4.3 (repo noise cleanup)** — any time after step 2
- **C9.7 (structured logging)** — any time after step 4; makes every later step easier to debug
- **C5.4 (superpowers skill audit)** — can be researched during Stage III, applied in step 11

### Hard gates

1. **Step 4 (CI green)** — no feature work before a regression net exists.
2. **Step 7 (permissions with shell)** — `LocalShellBackend` must never ship without the permission layer in the same step.
3. **Step 16 (docs)** — no public repo push until README matches reality and LICENSE/NOTICE exist.

---

## SECTION F — deepagents 0.7.4 Upgrade Findings

Verified by unpacking the 0.7.4 wheel and diffing against the installed 0.4.12.

### What Rudra can delete
| Rudra code | Why obsolete |
|---|---|
| `compat/overwrite_backend.py` | 0.7.4 `FilesystemBackend.write()` creates **or overwrites** (`backends/filesystem.py:489`), using `O_TRUNC` + `O_NOFOLLOW`. The whole reason the subclass existed is gone. Only fence-stripping survives → moves to middleware |

### What Rudra must re-verify
| Item | Status in 0.7.4 |
|---|---|
| `validate_path` monkeypatch | Still exists (`backends/utils.py:648`), still imported into `middleware/filesystem.py:77` → patch is still viable, but retest and add a version guard |
| `TodoListMiddleware` | **No longer in the default main stack.** Only present inside the Codex harness profile. Pass explicitly if `write_todos` is wanted (`graph.py:634`) |
| `backend` param type | Now `BackendProtocol` only — `BackendFactory` no longer accepted |

### New capabilities that replace planned Rudra work
| 0.7.4 feature | Replaces |
|---|---|
| `permissions: list[FilesystemPermission]` — `allow` / `deny` / `interrupt` path rules (`middleware/filesystem.py:382-416`) | Most of hand-rolled C3.3 |
| `RubricMiddleware` (`middleware/rubric.py`) — grader subagent runs whenever the agent would stop; injects feedback and resumes until satisfied / failed / max_iterations | **Rejected (D9).** Overlaps the reviewer subagent and gates termination on an LLM verdict. See §0.2 |
| Provider profiles (`profiles/provider/`) — `register_provider_profile`, built-in OpenRouter / NVIDIA / OpenAI kwarg injection, consumed by `resolve_model` | Reduces C1.1 to configuration |
| Harness profiles (`profiles/harness/`) — `excluded_tools`, per-model prompt/tool tuning; ships Anthropic Sonnet/Opus/Haiku, OpenAI Codex, NVIDIA Nemotron profiles | Clean home for per-model quirks instead of Rudra middleware |
| `AsyncSubAgentMiddleware` / `AsyncSubAgent` (`graph_id`) | Out-of-process subagents, Phase 6 option |
| `ContextHubBackend` | LangSmith-hosted files — **not** local-first, skip |
| `_message_eviction.py`, `_overflow_clip.py` | May cover part of C7.5 / C7.6 |
| `FsToolName`, `_ToolExclusionMiddleware` | Per-agent tool visibility |

### Dependency impact
| Package | Have | Need |
|---|---|---|
| `deepagents` | 0.4.12 | **0.7.4** |
| `langchain` | 1.2.13 | **>=1.3.14** |
| `langchain-core` | 1.2.23 | **>=1.5.0** |
| `langchain-ollama` | 1.0.1 | 1.1.0 (needs core `>=1.2.21`, compatible) |
| `langchain-anthropic` | 1.4.0 | **>=1.5.3** (now a hard dep of deepagents) |
| `langchain-google-genai` | 4.2.1 | **>=4.3.1** (now a hard dep) |
| `langchain-openai` | not installed | **>=1.4.1** (needs core `>=1.5.1` — compatible) |
| `langsmith`, `wcmatch`, `packaging` | — | new transitive deps |

`wcmatch` arriving as a deepagents dep is convenient — it is the right tool for real gitignore semantics (A1.10).

---

## SECTION G — Open Questions

| # | Question | Blocks |
|---|---|---|
| ~~G1~~ | ~~`RubricMiddleware` vs hand-rolled fix loop~~ — **ANSWERED 2026-08-05: hand-rolled (Option A).** See D9 / §0.2 | resolved |
| ~~G2~~ | ~~Rewrite skills for Rudra tool names vs alias?~~ — **ANSWERED 2026-08-05: rewrite.** See D10 / §0.3. Measured scope: ~28 call sites | resolved |
| ~~G3~~ | ~~Which skills ship, enabled or off?~~ — **ANSWERED 2026-08-05: 6 skills, all enabled by default.** See D11 / §0.3 | resolved |
| ~~G4~~ | ~~MemPalace embedding model download?~~ — **ANSWERED 2026-08-05: explicit pre-fetch + docs.** See D12 / C8.5 | resolved |
| ~~G5~~ | ~~Promote `test-driven-development` + `executing-plans`, or strip the references?~~ — **ANSWERED 2026-08-05: promote both; vendor all 14, enable 8.** See D11 / §0.3. Zero dangling refs remain | resolved |
| ~~G6~~ | ~~How is "vendored but off by default" implemented?~~ — **ANSWERED 2026-08-05: user-level skill cache with `library/` + `active/` dirs, mounted via a `CompositeBackend` route.** See D13 / §0.4. No deepagents fork needed | resolved |
| ~~G7~~ | ~~Commit project memory, or accept it as machine-local?~~ — **WITHDRAWN 2026-08-05: premise was wrong.** Rudra never gitignores `.rudra/`; that is the user's choice. Rudra's job is to make either choice safe → D15 / §0.7 | withdrawn |
