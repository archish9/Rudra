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
| D7 | **Delete `VirtualFileSystem` + `FileSyncManager`** | `load_from_disk` reads every text file in the repo into RAM (`virtual_fs.py:114-157`) — fatal for local-first memory budget. Replace with deepagents `LocalShellBackend` lazy file tools + a capped `project_tree()` helper that reads names only, never content. **DONE** 2026-08-06 |
| D8 | **Upgrade deepagents 0.4.12 → 0.7.4** | Large win, several obsolete workarounds. See [Section F](#section-f--deepagents-074-upgrade-findings) |
| D9 | **Fix loop = hand-rolled deterministic (Option A).** `RubricMiddleware` deferred, not adopted | Rationale in [Section 0.2](#02--fix-loop-design-d9) |
| D10 | **Superpowers skills: rewrite for Rudra tool names**, do not alias | Forks the vendored copy → `rudra skills update` becomes a merge, not a copy. Scope is smaller than feared: ~24 call sites. See [Section 0.3](#03--superpowers-skill-selection-d10--d11) |
| D11 | **Vendor all 14 skills; enable 8 by default** | Enabled: brainstorming · writing-plans · executing-plans · systematic-debugging · test-driven-development · verification-before-completion · requesting-code-review · receiving-code-review. Plus `using-superpowers` bootstrap (C5.3), not optional. Other 5 vendored but off until their prerequisite lands. Vendoring ≠ enabling: progressive disclosure means enablement is a prompt-index decision, disk cost is irrelevant. See [Section 0.3](#03--superpowers-skill-selection-d10--d11) |
| D12 | **MemPalace embedding model = explicit pre-fetch step, documented** | Preserves the offline/local-first promise. `rudra init` / `rudra doctor` warms the model; docs state the one-time download plainly |
| D13 | **Skills delivered via a user-level cache + `CompositeBackend` route** | Option 2. Answers G6 with no deepagents fork. Required because skills live outside the project root while the backend is rooted at it. See [Section 0.4](#04--skill-delivery-mechanism-d13) |
| D14 | **MemPalace palace is project-scoped, stored under `<project>/.rudra/memory/`** | Supported natively via `Config(palace_path=...)`. Some MemPalace state stays user-global and must be explicitly overridden. See [Section 0.6](#06--mempalace-storage-scope-d14) |
| D15 | **`.rudra/` split into durable vs volatile subtrees; Rudra writes `.rudra/.gitignore` scoping only itself** | Committing `.rudra/` must be safe by default, and it is the user's choice — Rudra never edits the project's root `.gitignore`. See [Section 0.7](#07--rudra-directory-layout-d15) |
| D16 | **Stay pure Python. Do not write Rust components.** | Measured 2026-08-05. Inference dominates non-inference work by 3–4 orders of magnitude (10–60 s vs single-digit ms). The CPU-bound paths are **already Rust** via `pydantic_core`, `jiter`, `orjson`, `regex`, `watchfiles`, and `tokenizers` (with mempalace). The only user-perceptible non-inference cost is **0.59 s startup**, which is the langchain/deepagents import itself — unfixable in Rust without abandoning deepagents. Real wins are Python-side: C9.8 (lazy import) and C9.9 (shell out to `ripgrep`). Revisit only if a single self-contained binary with no Python runtime becomes a goal |
| D17 | **Delete the `watch` command and `tools/code_tools.py`.** | Decided 2026-08-06. `watch` (`cli.py:351-414`) is a separate command, not part of a `rudra "<task>"` run, and its only actionable branch dispatches to a tool named `check_syntax` (`cli.py:396`) that exists solely in the dead `execution_tools.py:230` — never in `code_tools.py`, whose tools are `run_command` and `grep_in_file`. The branch has never executed. What remains is a printer of `Modified: <path>` lines. Deleting `watch` removes `code_tools.py`'s only consumer. Command execution returns in **C3.2** (step 7) on backend `execute`, with the log-offload behavior (big output → `.rudra/logs/`, short preview returned) ported as C3.2 already specifies. Run-visibility during a task run is served by `RudraAgent._log_single_message` (`main_agent.py`, method `_log_single_message`), improved later by C9.1/C9.7 |
| D18 | **Multi-language targets are first-class: Python, Rust, Node, React/Next.js, Angular** | Rudra's *own* toolchain stays Python (`pytest`, `ruff`, 3.12/3.13) regardless of what the user's project is written in — the two must never be conflated. Greenfield and brownfield are both supported and the choice is the user's; Rudra never requires a scaffold step, a config file, or a particular layout before it will work. Frontend completion gate is **unit tests only** — no build step, no e2e, no browser automation. Design: `docs/superpowers/specs/2026-08-07-multi-language-targets-design.md` |

### 0.1 — Middleware disposition (D4)

| Middleware | Verdict | Reason |
|---|---|---|
| `BlockPrematureAskMiddleware` | **DELETE** | Regex-blocks `ask_user` whenever the prompt names a framework (`block_premature_ask.py:20-23`) — directly blocks requirement #4, dynamic clarifying questions |
| `BlockTaskToolMiddleware` | **DELETE** | Blocks the `task` tool — blocks requirement #3, subagents for coder/tester/reviewer |
| `ContinueAfterWriteMiddleware` | **DELETE** | Already dead (zero call sites). The new agentic loop owns continuation |
| `EnforceTargetFileMiddleware` | **DELETE** | Only meaningful inside the one-file-per-agent loop being replaced. Also has a basename-matching hole (A1.6) |
| `TaskAnchorMiddleware` | **KEEP, opt-in** | Re-injects the task into the system prompt every model call. Still useful on long 32B runs. Gate behind `[compat] task_anchor = false` |
| `FixWriteParamsMiddleware` | **KEEP, split** | Markdown-fence stripping becomes **required** (0.7.4's `FilesystemBackend.write` no longer strips — Rudra's `OverwriteFilesystemBackend` was doing it). Keep fence-strip + `filename`/`path`→`file_path` aliasing always on; move sandbox-prefix stripping behind `[compat] sandbox_paths = false` |

#### Accepted risk: D4 was not purely subtractive (recorded 2026-08-06)

Deleting `EnforceTargetFileMiddleware` **and** `BlockTaskToolMiddleware` in the same change moved two guardrails at once, in opposite directions:

- The coder **gained a reachable `task` tool** — 0.7.4 registers `task` unconditionally in the default stack (U.16), and nothing blocks it any more.
- The coder **lost path enforcement** — nothing now checks that the file it writes is the file it was assigned.

Meanwhile the orchestrator's success test is unchanged: *does the file exist on disk* (A1.8). So a coder that writes the wrong filename now burns all three attempts, is reported as failed, and **leaves the wrong file behind** — a state that neither middleware permitted before.

**Accepted** per D4 and D6: prompt-level steering (`.rudra/current_task.md`, `_CODER_ANCHOR`, the per-file orchestrator message, and the coder prompt's `Do NOT call task.` rule) is expected to suffice at 32B, and `EnforceTargetFileMiddleware`'s own basename hole (A1.6) meant it never actually closed this. Do **not** restore either middleware. **Re-evaluate at C6.2**, when subagents become a designed feature rather than an unblocked side effect — the right replacement is a real completion gate (C6.6), not a path matcher.

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

But deepagents' `FilesystemBackend` is rooted at the **project path** (`main_agent.py`, the `FilesystemBackend(root_dir=str(project_path), virtual_mode=True)` construction in `create_main_agent`), and skill sources are **backend-relative paths** (`skills.py`, "Path Conventions"). Vendored skills live in `site-packages` — **outside the backend root, therefore unreadable**.

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
| 6 | Planner prompt: *"Do NOT call ask_user() if the task already specifies a framework or language"* | `planner_agent.py:63` (verified 2026-08-06 — the last line of `build_planner_prompt`'s `## RULES` block) | Prompt-level twin of `BlockPrematureAskMiddleware`. **Deleting the middleware in C0.2 does not remove this** |
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
| A1.1 | **DONE** 2026-08-07 | `__version__ = "0.1.0"` but pyproject says `0.2.0`. `rudra --version` prints the wrong number. | `src/rudra/__init__.py` now reads from `importlib.metadata.version()` with a `PackageNotFoundError` fallback to `"0.0.0+unknown"`. **Verified 2026-08-07 (Step 3, Task 3):** `.venv/bin/pytest tests/test_package_version.py -v` → 2 passed; `.venv/bin/rudra --version` → `Rudra v0.2.0`; `.venv/bin/pytest -q` → 39 passed (37 baseline + 2 new tests). |
| A1.2 | PENDING | Session ID is a fresh `uuid4()` every run → LangGraph checkpoints in `.rudra/checkpoints.db` are written but **never resumed**. No cross-run continuity. | `src/rudra/agent/main_agent.py` — the `session_id = uuid.uuid4().hex[:12]` assignment in `create_main_agent` |
| A1.3 | PENDING | `get_or_create_session_id()` exists precisely to fix A1.2 and has **zero call sites**. | `src/rudra/state/checkpoint.py:19` |
| A1.4 | **DONE** 2026-08-06 | Disk write failures silently swallowed, then the file is marked as written. Agent believes a failed write succeeded. | `src/rudra/filesystem/virtual_fs.py:206-209` (`except Exception: pass`) — resolved by D7 deletion. **Verified 2026-08-06 (Task 7):** `ls src/rudra/filesystem/` → `__init__.py`, `tree.py` only; `virtual_fs.py` no longer exists. Writes now go through deepagents' `FilesystemBackend.write()`, which raises on failure instead of swallowing it |
| A1.5 | **DONE** 2026-08-07 | `.env.example` sets `OLLAMA_NUM_PREDICT=2048`; `config.py` defaults to `131072`. A user copying the example gets files truncated at 2048 tokens. | `.env.example:13` vs `src/rudra/config.py:22`. **Verified 2026-08-07 (Step 3, Task 4):** `.env.example`'s `OLLAMA_NUM_PREDICT` now reads `131072`, matching `config.py`'s coded default. **Corrected 2026-08-07 (Step 3, final review):** this row's closing claim — that `grep -n "OLLAMA_" .env.example` and `grep -n "getenv" src/rudra/config.py` "line up on every value" — overstated it; re-verified directly against both files. The *numeric* values agree: `.env.example:14` `OLLAMA_TEMPERATURE=0.3` matches `config.py:31`'s `0.3` default, `.env.example:16` `OLLAMA_NUM_PREDICT=131072` matches `config.py:34`'s `131072` default, and `.env.example:15` `OLLAMA_TIMEOUT=300` matches `config.py:33`'s `300` default. The *model names* do not agree, and are not supposed to: `.env.example:9,12,13` ship `qwen3:32b`, `qwen3:32b`, `qwen3-coder:32b`, while `config.py:19,22,27`'s own coded defaults still read `qwen3:14b` — deliberately, per **A1.23**, which scoped changing those defaults to Step 5/6 (`C1.x`/`C2.x`). This row's Task 4 fix only ever touched `.env.example`'s numeric values; the model-name divergence was correct and intentional all along, the row's own closing sentence was just wrong about it |
| A1.6 | **DONE** 2026-08-06 | `EnforceTargetFileMiddleware` accepts any path with a matching **basename** → target `src/models.py` permits writing `tests/models.py`. | `src/rudra/middleware/enforce_target_file.py:36-42` — resolved by D4 deletion. **Verified 2026-08-06 (Task 7):** `ls src/rudra/middleware/` → `__init__.py`, `fix_write_params.py`, `task_anchor.py` only; `enforce_target_file.py` no longer exists. `grep -rn "EnforceTargetFile" src/ tests/` → no hits |
| A1.7 | **DONE** 2026-08-07 | `_parse_pending_files` treats every `- [ ]` line as a filename. A planner emitting `- [ ] Set up auth` creates a file literally named `Set up auth`. | `src/rudra/agent/main_agent.py` — module-level function `_parse_pending_files`. **Verified 2026-08-07 (Step 3, Task 5):** enforcement split across two points — `update_plan` (`src/rudra/tools/planning_tools.py`) now rejects prose with a `REJECTED:` string naming the offending items and writes nothing to disk, giving the model a correction signal; `_parse_pending_files` (`src/rudra/agent/main_agent.py`) now filters silently via the same `looks_like_path` predicate, since `PLAN.md` is user- and `edit_file`-editable and has no author to correct at parse time. `looks_like_path` (`src/rudra/tools/planning_tools.py`) is the single definition, imported by `main_agent.py`; no import cycle (`.venv/bin/python -c "import rudra.agent.main_agent; print('ok')"` → `ok`). `.venv/bin/pytest tests/test_plan_items.py -v` → 17 passed; `.venv/bin/pytest -q` → 62 passed (45 baseline + 17 new). Single-word prose (e.g. `authentication`) still passes `looks_like_path` — that gap is intentionally left open and tracked separately as `A1.24` |
| A1.8 | PENDING | Task success = file exists on disk. An empty or syntactically broken file counts as success and gets ticked off. | `src/rudra/agent/main_agent.py` — in `RudraAgent.run`, the `file_on_disk = self.context.project_path / filename` / `if file_on_disk.exists():` success check inside the retry loop |
| A1.9 | PENDING | `.rudra/AGENTS.md` created once, **never written again**. Long-term memory is a stub. | `src/rudra/agent/main_agent.py` — module-level function `_ensure_agents_md` (returns early when the file exists); no other writer |
| A1.10 | **DONE** 2026-08-06 | `_is_ignored` runs `fnmatch` on raw `.gitignore` lines. Real gitignore semantics (`!`, leading `/`, `**`, trailing `/`) not honored. | `src/rudra/filesystem/virtual_fs.py:78-112` — resolved by D7; 0.7.4 ships `wcmatch` for this. **Verified 2026-08-06 (Task 7):** `virtual_fs.py` deleted; `project_tree()` (`src/rudra/filesystem/tree.py:41`) uses `_to_wcmatch` (`tree.py:169`) to translate `.gitignore` lines into `wcmatch.glob` patterns before matching (`tree.py:192` `_matches`), covering `!`, leading `/`, `**`, and trailing `/` semantics that raw `fnmatch` did not. `.venv/bin/pytest tests/test_project_tree.py -v` → 8 passed, incl. `test_gitignored_paths_are_excluded` and `test_fallback_honors_gitignore_without_a_repo` |
| A1.11 | **DONE** 2026-08-06 | `watch` busy-loops `asyncio.run(asyncio.sleep(1))` — builds and tears down an event loop once per second. | `src/rudra/cli.py:409` — **closed by deletion**, exactly like A1.4 / A1.6 / A1.10. The `watch` command was deleted by D17 in Step 2. **Verified 2026-08-06:** `src/rudra/cli.py` is now 344 lines, so the cited `:409` is past end-of-file; `grep -n "watch\|asyncio.sleep" src/rudra/cli.py` → no hits; `.venv/bin/rudra --help` shows only the main callback, no `watch` subcommand. Removed from Section E Step 3's work column in the same commit, so no future session opens a task against code that no longer exists |
| A1.12 | **DONE** 2026-08-07 | `aiosqlite` imported at runtime, not declared in `pyproject.toml` (only transitive). | `src/rudra/agent/main_agent.py` — the function-local `import aiosqlite` inside `create_main_agent`; `pyproject.toml:25-59`. **Verified 2026-08-07 (Step 3, Task 2):** `"aiosqlite>=0.22.1"` added to `dependencies` at `pyproject.toml:35`, directly after `langgraph-checkpoint-sqlite`. `grep -n "aiosqlite" pyproject.toml` → hit at `:35`; `.venv/bin/python -c "import aiosqlite; print(aiosqlite.__version__)"` → `0.22.1` |
| A1.13 | **DONE** 2026-08-07 | `langgraph-checkpoint-sqlite` listed twice in dependencies. | `pyproject.toml:33` and `:68`. **Verified 2026-08-07 (Step 3, Task 2):** duplicate at the old `:68` deleted; single declaration remains at `pyproject.toml:33` under "Core Agent Framework" |
| A1.14 | **DONE** 2026-08-07 | Project URLs point to `github.com/archish/Rudra`; real remote is `github.com/archish9/RudraAnvil`. | `pyproject.toml:75-76`. **Verified 2026-08-07 (Step 3, Task 2):** `[project.urls]` (now `pyproject.toml:66-68`) — `Homepage` and `Repository` both point at `https://github.com/archish9/RudraAnvil` |
| A1.15 | **DONE** 2026-08-07 | `config` instantiated at import time → env changes after import ignored, no per-project config file possible. | `src/rudra/config.py` — the module-level `config = Config.load()` at end of file. **Verified 2026-08-07 (Step 3, Task 4):** the module-level `config` name is removed outright (not aliased — an alias is still evaluated at import by whoever binds it); replaced by `get_config()` (cached, loads on first call) and `reset_config()` (test-support only). Every call site converted: `planner_agent.py`, `coder_agent.py`, `main_agent.py`, `cli.py`. `cli.py`'s `--verbose` Typer default was the non-obvious case — a decorator argument evaluated at import — now `Optional[bool] = typer.Option(None, ...)`, resolved via `get_config().agent.verbose` inside the callback body before any other code reads `verbose`. `grep -rn "from rudra.config import config\|config\.ollama\|config\.agent" src/` → no code hits (one comment mentioning `config.agent.verbose` survives, documenting the old defect). `.venv/bin/pytest tests/test_config.py -v` → 6 passed, incl. `test_get_config_is_cached` and `test_reset_config_rereads_the_environment` |
| A1.16 | PENDING | Files silently overwritten — no diff, no backup, no confirm. Data-loss path on a real repo. **Safety issue.** | `src/rudra/compat/overwrite_backend.py:50-65` — 0.7.4 `permissions=` + `interrupt_on=` is the fix |
| A1.18 | PENDING | **Found by final whole-branch review 2026-08-05, not fixed (logged only).** `FixWriteParamsMiddleware._strip_fences` (`src/rudra/middleware/fix_write_params.py:34-36`) data-loses on a Markdown file whose entire legitimate body is one fenced block: content ` ```mermaid\ngraph TD\n``` ` (a complete, valid Mermaid-fenced Markdown file) gets unwrapped to `graph TD\n` — the fences are silently removed from content where they were not a wrapper artifact but the actual intended text. Verified directly: `.venv/bin/python3 -c "from rudra.middleware.fix_write_params import _strip_fences; print(repr(_strip_fences('\`\`\`mermaid\\ngraph TD\\n\`\`\`')))"` → `'graph TD\n'`. **Not a regression from this branch** — checked against the deleted backend's original regex (`overwrite_backend.py:43`, quoted in A4.9's sibling U.15 row) and U.15's widened middleware regex: both strip identically on this input, before and after. It has **no ledger entry anywhere** prior to this row and is a live data-loss path today, independent of U.3/U.14/U.15. Root cause: the function cannot distinguish "the model wrapped real code in a fence because that's the write_file convention it learned" from "the fenced block genuinely is the entire file content" — heuristically these look identical. Not fixed here — out of this fix wave's scope; would need either a smarter heuristic (e.g. only strip when content-after-unwrap still looks like the target file's expected language) or an opt-out per target filename/extension (e.g. skip stripping for `.md` targets) | `src/rudra/middleware/fix_write_params.py:34-36` |
| A1.17 | PENDING | **Summarization never fires for local models.** `create_deep_agent` auto-adds `create_summarization_middleware`, whose threshold comes from the model profile: 85% of context if a profile exists, else a fixed **170,000-token** fallback (`summarization.py:249-286`). Verified: `ChatOllama.profile is None` → Rudra takes the fallback. A 32B model (D6) has 32k–128k context, so it **overflows long before 170k**. The only real context-management mechanism in the product is inert for its primary target. Fix: set `trigger`/`keep` explicitly per role from configured context size, or register a model profile. | `.venv/.../deepagents/middleware/summarization.py:249-286`; `python -c "ChatOllama(...).profile"` → `None` |
| A1.19 | **DONE** 2026-08-06 | **`project_tree()` returns `(empty project)` for a directory that has files.** Found by final whole-branch review 2026-08-06. `_git_listing` (`src/rudra/filesystem/tree.py`, `_git_listing`) returns `None` only on *failure* — git missing, exit ≠ 0, timeout. But when the root is inside a git work tree and is itself **gitignored**, `git ls-files --cached --others --exclude-standard` exits **0 with empty stdout**, so `_git_listing` returns `[]`, which is not `None`, and `project_tree`'s `if entries is None:` branch never reaches the `_walk_listing` fallback. Result: a directory full of files reports `(empty project)` into the planner prompt (`src/rudra/agent/planner_agent.py`, `build_planner_prompt`'s `## PROJECT STRUCTURE` section). **Reproduced 2026-08-06:** a repo whose `.gitignore` contains `playground/`, with `playground/main.py` and `playground/src/mod.py` on disk — `git -C <repo>/playground ls-files --cached --others --exclude-standard -z` → `rc=0`, `stdout=b''`; `project_tree(<repo>/playground)` → `'(empty project)'`. **Regression against `VirtualFileSystem.get_tree()`**, which walked disk and had no git path at all. **Fixed 2026-08-06:** the fallback now triggers on a falsy listing, not only on `None`. Deliberate consequence, recorded in the `project_tree` docstring: in a directory where everything is ignored, the walk lists ignored files — the correct answer for "show the agent what is here", and what the pre-branch VFS did | `src/rudra/filesystem/tree.py` (`project_tree`, `_git_listing`); test `tests/test_project_tree.py::test_gitignored_root_still_lists_its_own_files` |
| A1.20 | PENDING (partial fix applied 2026-08-06) | **`_stream_coder`'s repeated-tool-call guard does not watch `task`.** The guard counts repeats only for `write_file` / `edit_file` / `read_file` (`src/rudra/agent/main_agent.py`, `_stream_coder`, the guarded-name tuple), so a coder looping on `task` is unbounded. `task` became reachable when `BlockTaskToolMiddleware` was deleted (D4 / C0.2) — deepagents 0.7.4 registers `task` unconditionally in the default tool stack (U.16 probe: `['delete', 'edit_file', 'execute', 'glob', 'grep', 'ls', 'read_file', 'task', 'write_file']`). **Partially fixed 2026-08-06:** `"task"` added to the guarded-name tuple. **A second, deeper issue is NOT fixed, and this row stays PENDING for it:** `_stream_coder` streams with `subgraphs=True` but keeps a **single `processed` counter across all namespaces**. Parent and subagent each carry their own `messages` list; once a subagent's list outgrows the parent's, `processed` has already advanced past the parent's length, so the parent's later messages never enter the `while processed < len(msgs)` body and the guard silently stops seeing them. `_stream_planner` has the identical structure and the identical hole. Fixing it properly means per-namespace state, not one counter — belongs with C6.2 (subagents) / C9.1 (trace rework) | `src/rudra/agent/main_agent.py` (`_stream_coder`: the guarded-name tuple, the `processed` counter, the `subgraphs=True` `astream` call); same shape in `_stream_planner` |
| A1.21 | PENDING | **`project_tree`'s non-git fallback diverges from git in three ways, none of which the tests cover.** Found by final whole-branch review 2026-08-06. Moot whenever git answers — it bites only in a non-repo directory, or (post-A1.19) in a gitignored root. (1) **Directory-only semantics discarded:** `_to_wcmatch` (`src/rudra/filesystem/tree.py`) does `line.rstrip("/")`, so a `.gitignore` line `build/` — which git applies to *directories only* — also excludes a plain **file** named `build`. (2) **Only the root `.gitignore` is read:** `_gitignore_patterns` opens `root / ".gitignore"` and nothing else — nested `.gitignore` files in subdirectories, `.git/info/exclude`, and `core.excludesFile` are all ignored, so the fallback under-excludes wherever a project splits its ignore rules. (3) **The tests exercise exactly one pattern.** `tests/test_project_tree.py` uses `build/` in every gitignore-related test; `!` negation, leading-`/` anchoring and `**` are asserted **nowhere**, even though `_to_wcmatch`'s docstring claims all three and A1.10 was closed on that claim. Fix alongside any future work that makes the fallback load-bearing | `src/rudra/filesystem/tree.py` (`_to_wcmatch`, `_gitignore_patterns`); `tests/test_project_tree.py` |
| A1.22 | **DONE** 2026-08-07 | **Found while designing Step 3.** `.env.example:11` sets `OLLAMA_TEMPERATURE=0.7`; `config.py:20` defaults to `0.3`. A user copying the example silently gets different sampling behavior from the documented default. Fix with **A1.5** | `.env.example:11` vs `src/rudra/config.py:20`. **Verified 2026-08-07 (Step 3, Task 4):** `.env.example`'s `OLLAMA_TEMPERATURE` now reads `0.3`, matching `config.py`'s coded default |
| A1.23 | **DONE** 2026-08-07 | **Found while designing Step 3.** `.env.example:6,9` ship `qwen3:14b` and `:10` ships `qwen3-coder:30b` — all below **D6**'s 32B minimum, so the shipped example contradicts the locked decision. Example file only; changing `config.py:17-19`'s own defaults is a behavior change belonging to Step 5/6 (`C1.x`/`C2.x`). Fix with **A1.5** | `.env.example:6,9,10` vs TODO.md D6. **Verified 2026-08-07 (Step 3, Task 4):** `.env.example` now ships `OLLAMA_MODEL=qwen3:32b`, `OLLAMA_MODEL_PLANNER=qwen3:32b`, `OLLAMA_MODEL_CODER=qwen3-coder:32b` — all at or above the 32B floor — plus a new comment above the model block citing D6. `config.py:17-19`'s own coded defaults (`qwen3:14b`) are deliberately untouched, per this row's own scoping — that is Step 5/6's `C1.x`/`C2.x` |
| A1.24 | PENDING | **Accepted risk, not a bug to fix here. Found while designing Step 3.** `looks_like_path` (introduced by Task 5's fix for A1.7; does not exist yet) will reject prose by whitespace and trailing punctuation, so a single-word task description (`authentication`, `setup`) still passes as a filename. Closing it needs either a static allowlist of extensionless filenames (§0.5 rules that out) or an LLM judgment (D9 keeps gates deterministic). The real fix is **C6.6**'s deterministic completion gate in Step 9, which tests behavior instead of file existence | `src/rudra/tools/planning_tools.py` — `looks_like_path` (to be added by Task 5). **Note 2026-08-07 (Step 3, Task 5):** `looks_like_path` landed as predicted; the gap is pinned deliberately by `tests/test_plan_items.py::test_single_word_prose_is_a_known_accepted_gap`, which asserts `looks_like_path("authentication")` is `True`. This row stays PENDING — deferred to C6.6, not closed by Task 5 |
| A1.25 | PENDING | **Found by final whole-branch review 2026-08-07.** A filtered-out plan item is invisible to the user, and the run still reports success. `_parse_pending_files` (`src/rudra/agent/main_agent.py:46-59`) silently drops checklist items that don't look like a file path — correct *toward the model*, since `update_plan` already rejects prose with a `REJECTED:` signal at write time (A1.7). The defect is *toward the user*: `total = len(pending_files)` (`main_agent.py:374`) is computed AFTER the filter runs, so the completion summary (`main_agent.py:432`, `:437`) reports e.g. `2/2 files generated` with `success=True` while a third requested item was never written and never mentioned anywhere in the run's output. Reachable in normal operation, not only via hand-editing `PLAN.md`: `update_plan`'s own docstring (`src/rudra/tools/planning_tools.py:71-73`) instructs the agent to check off files with `edit_file` directly against `.rudra/PLAN.md` on disk, which bypasses `update_plan`'s own `REJECTED:` validation gate entirely — so a plan rewritten that way, or one containing a stray prose line that slipped past `looks_like_path` (A1.24), gets silently dropped with no trace in the summary. Not fixed here. Suggested remedy for a future session: log the skipped items via `_log_always` before the coding loop starts, so the count shown to the user is `total` items requested, not `total` items that survived the filter | `src/rudra/agent/main_agent.py:46-59` (`_parse_pending_files`), `:374` (`total = len(pending_files)`), `:432,437` (summary print + `AgentResult.message`); `src/rudra/tools/planning_tools.py:71-73` (`update_plan` docstring's `edit_file` instruction). Verified: `.venv/bin/python -c "from rudra.agent.main_agent import _parse_pending_files; print(_parse_pending_files('- [ ] main.py\n- [ ] app/routes.py (REST endpoints)\n- [ ] models.py'))"` → `['main.py', 'models.py']` — the middle item is silently dropped; `total` would be `2`, not the `3` the plan actually named |
| A1.26 | **DONE** 2026-08-07 | **`_ALWAYS_SKIP_DIRS` omits every build-output directory of the non-Python targets.** `tree.py:34` lists `.git`, `.rudra`, `__pycache__`, `node_modules`, `.venv`, `venv` — missing `target/` (Cargo), `.next/`, `out/` (Next.js), `dist/`, `build/` (JS toolchains), `.angular/`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`. The set is applied to **both** listing paths (`tree.py:81`), so this is not fallback-only; on the git path it is largely masked because `--exclude-standard` already drops conventionally-gitignored build dirs, and on the walk path it is load-bearing — the walk runs in a non-git directory, and per the A1.19 note at `tree.py:58-63` also when git lists nothing because the root is itself gitignored. Harm is crowding, not noise: `max_entries=300` (`tree.py:44`) gets filled with build artefacts, truncating away the source the planner needs | `src/rudra/filesystem/tree.py:34,44,81`. **Verified 2026-08-07 (Step 4, Task 4):** `_ALWAYS_SKIP_DIRS` now splits into `_RUDRA_SKIP_DIRS = frozenset({".git", ".rudra"})` and `_ALWAYS_SKIP_DIRS = _RUDRA_SKIP_DIRS | ALL_SKIP_DIRS`, importing `ALL_SKIP_DIRS` from `rudra.stacks` (Task 2's registry) instead of hardcoding a literal set. Assertion covering all 15 names (`.git`, `.rudra`, `__pycache__`, `node_modules`, `.venv`, `venv`, `target`, `.next`, `out`, `dist`, `build`, `.angular`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`) passes: `sorted(_ALWAYS_SKIP_DIRS)` → `['.angular', '.git', '.mypy_cache', '.next', '.pytest_cache', '.rudra', '.ruff_cache', '.tox', '.venv', '__pycache__', 'build', 'coverage', 'dist', 'node_modules', 'out', 'target', 'venv']` — nothing lost. No import cycle: `.venv/bin/python -c "import rudra.filesystem.tree; import rudra.stacks; print('ok')"` → `ok`. Three new tests added to `tests/test_project_tree.py` (confirmed failing before the fix, passing after): `test_build_output_dirs_are_never_listed`, `test_frontend_build_dirs_are_never_listed`, `test_skip_dirs_are_sourced_from_the_stack_registry`. `.venv/bin/pytest tests/test_project_tree.py -v` → 12 passed; `.venv/bin/pytest -q` → 92 passed (89 baseline + 3 new); `.venv/bin/ruff check` / `ruff format` on both touched files → clean |
| A1.27 | PENDING | **Prompt filename examples are uniformly Python**, steering the model toward `.py` for every task including "build a Rust CLI". 11 locations: `planning_tools.py:12-14,60-62,68-69,72-73,113,160,178`; `planner_agent.py:46,53,61`; `coder_agent.py:28`. Prompt-only — the validation logic is already language-agnostic (`looks_like_path` accepts `Cargo.toml`, `package.json`, `angular.json`, `src/main.rs`, verified by execution) | the 11 locations above |

### A2 — Dead code

| # | Status | Item | Evidence |
|---|---|---|---|
| A2.1 | **DONE** 2026-08-06 | `execution_tools.py` — 255 lines at repo root, zero imports. **Delete.** | `grep -rn execution_tools . --exclude-dir=.venv` — **Verified 2026-08-06 (Task 7):** `ls execution_tools.py` → `No such file or directory`. `git diff --stat 040dae4..HEAD` shows `execution_tools.py \| 255 ----------------` (full deletion) |
| A2.2 | **DONE** 2026-08-06 | `filesystem/sync.py` — 271 lines, unreachable. **Delete (D7).** | `src/rudra/filesystem/sync.py`; `cli.py:18` imports, never calls — **Verified 2026-08-06 (Task 7):** `ls src/rudra/filesystem/` → `__init__.py`, `tree.py` only. `git diff --stat 040dae4..HEAD` shows `src/rudra/filesystem/sync.py \| 271 -----------------------` |
| A2.3 | **DONE** 2026-08-06 | `ContinueAfterWriteMiddleware` — 105 lines, zero call sites. **Delete (D4).** | `src/rudra/middleware/continue_after_write.py` — **Verified 2026-08-06 (Task 7):** `ls src/rudra/middleware/` → `__init__.py`, `fix_write_params.py`, `task_anchor.py` only. `grep -rn "ContinueAfterWrite" src/ tests/` → no hits |
| A2.4 | **DONE** 2026-08-06 | `code_tools.py` given to no agent — only `watch` uses it. Agents cannot run any command. | `src/rudra/cli.py:393`; `coder_agent.py:80` `tools=[]` — **Verified 2026-08-06 (Task 7):** `code_tools.py` and the `watch` command are both deleted (`ls src/rudra/tools/` → `__init__.py`, `interaction_tools.py`, `planning_tools.py`; `.venv/bin/rudra --help` shows only the main callback, no `watch` subcommand). Real command execution is deferred to C3.2 per D17 |
| A2.5 | **DONE** 2026-08-06 | Unused imports: `asyncio`, `create_deep_agent` in `main_agent.py:5,11`; `FileSyncManager, SyncMode` in `cli.py:18`. | ruff F401 — **Verified 2026-08-06 (Task 7):** `main_agent.py`'s import block (`:3-12`) no longer imports `asyncio` or `create_deep_agent`; `grep -rn "FileSyncManager\|SyncMode" src/` → no hits |
| A2.6 | **DONE** 2026-08-06 | `AgentConfig.max_iterations`/`max_agents`/`checkpoint_interval` and `AgentContext.max_iterations`/`file_path`/`issue` never read. | `config.py:29-31`; `main_agent.py:30,34,35` — **Verified 2026-08-06 (Task 7):** `AgentConfig` (`config.py:25-30`) now carries only `verbose`; `AgentContext` (`main_agent.py:15-31`) carries no `max_iterations`/`file_path`/`issue` fields |
| A2.7 | **DONE** 2026-08-06 | `SearchConfig` (tavily / duckduckgo) and `Config.search` are never referenced anywhere in `src/`. **Delete.** | `src/rudra/config.py:36-41` (class), `:50` (field); `grep -rn "config.search\|SearchConfig\|tavily\|duckduckgo" -i src/` returns only the definition itself — **Verified 2026-08-06 (Task 7), removed in Task 6's sweep (commit `a484551`):** `grep -rn "config.search\|SearchConfig\|tavily\|duckduckgo" -i src/ tests/` → zero hits, including the definition. `src/rudra/config.py`'s `Config` dataclass has no `search` field |
| A2.8 | **DONE** 2026-08-06 | `AgentResult.todo_summary` is never set and never read — the field is the only occurrence of the name in the repo. **Delete.** | `src/rudra/agent/main_agent.py:50`; `grep -rn "todo_summary" src/` → 1 hit — **Verified 2026-08-06 (Task 7):** `AgentResult` (`main_agent.py:34-42`) has no `todo_summary` field; `grep -rn "todo_summary" src/ tests/` → no hits |
| A2.9 | **DONE** 2026-08-06 | `watch`'s `.py` branch dispatches to a tool named `check_syntax` that `create_code_tools` never produces — it exists only in the dead `execution_tools.py`. The branch has never executed, so `watch` has never reported an error. Resolved by the D17 deletion. | `src/rudra/cli.py:396` vs `src/rudra/tools/code_tools.py` (returns `[run_command, grep_in_file]`, `:135`) and `execution_tools.py:230` — **Verified 2026-08-06 (Task 7):** `watch` command, `code_tools.py`, and `execution_tools.py` are all deleted; the dead branch no longer exists in any form |
| A2.10 | **DONE** 2026-08-06 | `--max-agents` writes `config.agent.max_agents`, which nothing reads — a live CLI flag with no effect. **Delete the flag with the field (A2.6).** Real sub-agent fan-out arrives with C6.2. | `src/rudra/cli.py:180` (option), `:192` (assignment); `src/rudra/config.py:29` (field) — **Verified 2026-08-06 (Task 7):** `grep -n "max.agents\|max_agents" src/rudra/cli.py src/rudra/config.py` → no hits; `.venv/bin/rudra --help` shows no `--max-agents` option |
| A2.11 | **DONE** 2026-08-07 | `watchdog>=4.0.0` is now an unused runtime dependency — its only import sites were `from watchdog.observers import Observer` and `from watchdog.events import FileSystemEventHandler` inside the `watch` command deleted by D17. **Drop it with the rest of the dependency hygiene work (C0.7).** | `pyproject.toml:65`; `grep -rn "watchdog" src/` → no hits. **Verified 2026-08-07 (Step 3, Task 2):** `watchdog>=4.0.0` and its comment line deleted from `dependencies`; `.venv/bin/rudra --help` → help text, no traceback |
| A2.12 | **DONE** 2026-08-06 | Stale comment describing a deleted component as a live rationale: `# Read directly from disk — never from VFS cache, which may be stale`. The VFS was deleted by D7; there is no cache. Not A4.9-protected prose — it does not explain a removal, it asserts a current reason in terms of something that no longer exists. | `src/rudra/tools/planning_tools.py:114` — **Verified 2026-08-06 (Task 7):** `grep -n "VFS cache\|Read directly from disk" src/rudra/tools/planning_tools.py` → no hits; comment removed |
| A2.13 | **DONE** 2026-08-06 | `create_coder_agent`'s `project_path` parameter is never used in the function body and is not referenced anywhere else in the file either — not even in the docstring. Pre-existing (confirmed dead at commit `2722bd8`, before the VFS removal), not introduced by D7. | `src/rudra/agent/coder_agent.py:47` — **Verified 2026-08-06 (Task 7):** `create_coder_agent`'s signature (`coder_agent.py:44-48`) is now `(tech_stack_content, filesystem_backend, checkpointer)` — the dead `project_path` parameter is gone entirely, not merely unused |
| A2.14 | **DONE** 2026-08-06 | **`.env.example` still ships five environment variables that nothing reads.** `MAX_AGENTS` (`.env.example:16`), `MAX_ITERATIONS` (`:17`), `CHECKPOINT_INTERVAL` (`:18`) — the `AgentConfig` fields behind them were deleted by A2.6 — and `TAVILY_API_KEY` (`:23`), `USE_DUCKDUCKGO` (`:24`) — `SearchConfig` was deleted by A2.7. A user who copies this file to `.env` and sets `MAX_ITERATIONS=50` gets silence: no effect, no warning, no error. Found by final whole-branch review 2026-08-06; same class as A2.6/A2.7, missed because their scope was `src/` only. **Fixed 2026-08-06:** all five removed, plus their now-orphaned `# Search Tools (optional)` heading. `OLLAMA_NUM_PREDICT=2048` (`.env.example:13`) deliberately left alone — it has its own open rows, A1.5 and A5.1 | `.env.example:16,17,18,23,24`; `grep -rn "MAX_AGENTS\|MAX_ITERATIONS\|CHECKPOINT_INTERVAL\|TAVILY_API_KEY\|USE_DUCKDUCKGO" src/ tests/` → zero hits |
| A2.15 | PENDING | **Three more never-read members, same class as A2.6, outside its scope.** Found by final whole-branch review 2026-08-06. (1) `AgentContext.command` (`src/rudra/agent/main_agent.py`, `AgentContext`, the `command: str = "auto"` field) — assigned by `create_main_agent` from its own `command: str = "build"` parameter, itself passed by nothing in `src/`; `grep -rn "context\.command" src/` → zero hits. (2) `AgentContext.verbose` — assigned by `create_main_agent`, never read; the verbosity that actually reaches the console comes from `cli.py`'s `--verbose` flag and `config.agent.verbose`, not from this field; `grep -rn "context\.verbose" src/` → zero hits. (3) `RudraAgent.iterations` (`main_agent.py`, `RudraAgent.__init__`, `self.iterations = 0`) — never incremented, never read; `AgentResult(iterations=...)` in `run()` is populated from the local `total`, not from this attribute, and `cli.py`'s `Iterations:` line prints `result.iterations`. Not fixed here — deleting the `command` parameter changes `create_main_agent`'s public signature, which belongs with the C2/C6.8a work that reworks it. Do NOT confuse with `AgentContext.stop_on_error` (read in `_stream_coder`) or `AgentConfig.verbose` (read by `cli.py`), both of which are live | `src/rudra/agent/main_agent.py` (`AgentContext.command`, `AgentContext.verbose`, `RudraAgent.__init__`'s `self.iterations`); `grep -rn "context\.command\|context\.verbose" src/` → no output |
| A2.16 | **DONE** 2026-08-07 | **Six runtime dependencies have zero import sites in `src/`.** Every user installs `langchain-community` (`pyproject.toml:39`), `fastapi` (`:51`), `uvicorn[standard]` (`:52`), `tavily-python` (`:60`), `duckduckgo-search` (`:61`), `requests` (`:63`) for nothing. The fastapi/uvicorn pair is commented "Web Server (optional API layer)" (`:50`), but no such layer exists and no ledger item plans one. Drop with the rest of the dependency hygiene work (**C0.7**) | `grep -rn "fastapi\|uvicorn\|tavily\|duckduckgo\|langchain_community\|^import requests\|^from requests" src/` → no hits. **Verified 2026-08-07 (Step 3, Task 2):** all six lines and their heading comments (`# Web Server (optional API layer)`, `# Web Search Tools`, `# HTTP Requests`) deleted from `dependencies`. `.venv/bin/python -c "import importlib.metadata as m; print(len(list(m.distributions())))"` → **105 before, 73 after** `uv sync`. `.venv/bin/rudra --help` → prints help text, no `ModuleNotFoundError` — `langchain-community` was not reachable via any runtime import path exercised by the CLI entry point. `.venv/bin/pytest -q` → `37 passed` |

### A3 — Quality gates

| # | Status | Item | Evidence |
|---|---|---|---|
| A3.1 | PENDING | `ruff check src/ execution_tools.py` → **213 errors** (165 auto-fixable). | command output 2026-08-05 |
| A3.2 | PENDING | `tests/` is empty. Zero tests, though pytest is configured. | `ls tests/`; `pyproject.toml:79-81` |
| A3.3 | PENDING | No CI. | no `.github/` |
| A3.4 | **DONE** 2026-08-07 | `ruff` and `pytest` are runtime dependencies; belong in a `dev` extra. **Found by a reviewer during Task 2, not included in Task 2's own closing list — closed here in Task 7.** `pyproject.toml`'s `[dependency-groups] dev` (`:57-61`) already holds `"ruff>=0.15.8"` and `"pytest>=9.0.2"`; neither name appears in `[project] dependencies` (`:25-55`). **Verified 2026-08-07 (Step 3, Task 7):** `grep -n "ruff\|pytest" pyproject.toml` → both hits are inside `[dependency-groups] dev` only. This is the same landing C0.7's row already verified (`uv sync` installs the group by default, distribution count dropped 105 → 73) — A3.4 was simply never marked DONE alongside it | `pyproject.toml:57-61` |

### A4 — Docs / OSS readiness

| # | Status | Item | Evidence |
|---|---|---|---|
| A4.1 | PENDING | README documents subcommands `build`, `chat`, `fix`, `edit`, `review`, `suggest`, `resume` that **do not exist**; also documents the static 4-question tech-stack form (§0.5 surface #7) and a `.rudra/session_id.txt` that is never written (A1.3). | `README.md:351-510,626-636` vs 1 `@app.command` in `cli.py` |
| A4.2 | PENDING | `license = "Apache-2.0"` declared, no LICENSE file on disk. | `pyproject.toml:10` |
| A4.3 | PENDING | Repo tracks noise: `q-dev-chat-2026-03-20.md` (199 KB), `filesystem-context.txt`, `improvements.txt`, `llms.txt`, `execution_tools.py`. | `git ls-files` |
| A4.4 | PENDING | `requirements.txt` (pinned) and `pyproject.toml` (ranges) list different sets and will drift. Pick one source of truth. **Partial 2026-08-05:** `requirements.txt` deleted (`git rm requirements.txt`, Task 5 step 4); `pyproject.toml` + `uv.lock` are now the single source of truth. Remaining: `deepagents` is pinned exactly, not ranged (see U.1) — the rest of A4.4's "ranges vs pinned" framing still applies to other deps. | both files |
| A4.5 | PENDING | No CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, issue templates. | `ls` |
| A4.6 | PENDING | pyproject `keywords`/`description` still say "ollama" — contradicts provider-agnostic goal. | `pyproject.toml:8,15` |
| A4.7 | PENDING | Vendored superpowers (MIT, Jesse Vincent) + MemPalace (MIT) under Apache-2.0 need a NOTICE / THIRD_PARTY file. | licenses verified |
| A4.8 | **DONE** 2026-08-05 | **`.gitignore` ignored `docs/` and `tests/`.** Fixed: removed both lines; `docs/_build/` (`.gitignore:73`) still covers sphinx output. Verified `git check-ignore -v docs tests` → exit 1 (no match).
| A4.9 | PENDING | **Stale `file:line` citations pointing into the deleted `overwrite_backend.py`.** U.3 deleted `src/rudra/compat/overwrite_backend.py`, but two comments still cite `overwrite_backend.py:43` — `src/rudra/middleware/fix_write_params.py:20` and `tests/test_fix_write_params.py:4`. Naming a **line number** in a file that no longer exists is a stale pointer, not historical context: a reader who goes looking for line 43 to see the original regex finds nothing, and `CLAUDE.md` §2 rule 3 requires citations be checkable. Five other references to the class/module survive as prose (`main_agent.py:555`, `planner_agent.py:99`, `test_fix_write_params.py:3`, `test_deepagents_contract.py:144`, `:166`) and are fine — they explain *why* code exists without promising a lookup. Fix: drop the `:43` and quote the regex inline, or point at the commit that deleted it. Deliberately not fixed in U.3 — those files were outside that task's permitted scope. **Extended by final whole-branch review 2026-08-05:** this item is **still only about the citations into the deleted file** (`fix_write_params.py:20`, `test_fix_write_params.py:4` → `overwrite_backend.py:43`), which remain outstanding — not fixed here, still one pass when someone picks this up. A *different* category of stale citation — numeric pointers into **live** files that had simply drifted (`test_deepagents_contract.py:164`'s `main_agent.py:556`, and `TODO.md` U.3's `main_agent.py:554`/`:554-560`) — was found by the same review and corrected immediately; see `tests/test_deepagents_contract.py::test_virtual_mode_writes_through_to_disk` and the U.3 row. Not double-counted here: those were live-file drift, this row is dead-file citations, deliberately left for the cleanup pass this row already describes | `fix_write_params.py:20`, `test_fix_write_params.py:4`; file deleted in `adc8fce` | `tests/` being ignored makes the step-4 CI gate structurally impossible — nothing under `tests/` can ever be tracked, though `pyproject.toml:79-81` configures `testpaths = ["tests"]`. This is the real reason behind A3.2, not merely "nobody wrote tests yet". `docs/` blocks C10.3 (design docs live in `docs/`). **Blocks Step 1** (contract tests + spec are both uncommittable). | `.gitignore:215-216`; `git check-ignore -v docs tests` |
| A4.10 | PENDING | **Line-citation drift around `main_agent.py:550-565` is systemic, not isolated — do a citation audit, not one-off patches.** The 0.7.4 upgrade branch produced **four** separate wrong-line findings in this one region, three of which were found only because a reviewer checked rather than assumed. Known-stale as of 2026-08-05: `TODO.md:420` (U.16) cites `main_agent.py:556` for the backend swap — `:556` is mid-comment, the constructor is `:558-561`; `TODO.md:142` (§0.4) cites `main_agent.py:554` for `root_dir=project_path` — actually `:559`; `TODO.md:268` (§0.7) cites `main_agent.py:559-560` for `.rudra/` auto-creation — actually `:564`. Root cause: hand-written line numbers in the ledger drift silently every time the code beneath them moves, and nothing checks them. Fix as one pass over `TODO.md` + `CLAUDE.md`, and prefer symbol/function names over bare line numbers where a name is stable enough to serve. Left unfixed deliberately: each instance was outside the dispatched scope of the task that noticed it. **Widened 2026-08-06 (final whole-branch review of `cleanup/step2-dead-code`):** Step 2 deleted ~1,520 lines, which shifted the numbers again and made the drift repo-wide rather than region-local. Nine citations were repaired in that review's commit — D17, §0.4, A1.2, A1.7, A1.8, A1.9, A1.12, A1.15, and the Section B `Tools` row — each rewritten as a **stable symbol name** instead of a line number, which is the durable form this row asks for. The following were **measured but deliberately left**, because they were outside that fix wave's scope and belong to this row's single audit pass: Section B still cites `planner_agent.py:75` and `coder_agent.py:61` for the `ChatOllama` constructions (actual `:77` and `:57`), `main_agent.py:345-437` for the orchestration loop (actual `RudraAgent.run`, `:336-434`) and `main_agent.py:61,411` for progress tracking (actual `_check_off_file` `:53` and the `file_on_disk.exists()` check `:401-402`); C1.4 cites `planner_agent.py:78-80` / `coder_agent.py:64-66` for the Ollama-only kwargs (actual `:81-82` / `:61-62`); §0.5 #4 cites `main_agent.py:454-461` and #5 `main_agent.py:484-506` (actual `_ensure_agents_md` `:437-465`, `_write_tech_stack_file` `:468-502`); §0.7 cites `main_agent.py:559-560` for `.rudra/` auto-creation (actual `:549-550`); and every U-row that cites `main_agent.py:558-561` for the `FilesystemBackend` construction (U.3, U.6, U.12, U.16, U.22) now means `:544-547`. Also still outstanding, and pointing at files that no longer exist at all: Section B's `middleware/block_premature_ask.py:20-23`, A1.16's `compat/overwrite_backend.py:50-65`, and A2.4's `coder_agent.py:80`. `CLAUDE.md` §3–§5 carries the same class of drift and must be included in the pass | re-review of `f02e295..6dc381c`; re-measured 2026-08-06 against `src/` on `cleanup/step2-dead-code` |
| A4.11 | PENDING | **`compat/deepagents_path.py`'s stated rationale is false under 0.7.4.** Its docstring (`:5-6`) says deepagents' `validate_path` "rejects absolute paths with a hard ValueError". Measured on the pinned 0.7.4: `validate_path('/abs/x.py')` returns `'/abs/x.py'` and `validate_path('main.py')` returns `'/main.py'` — it raises nothing and prepends `/` (virtual-root semantics). The module still does useful work (stripping real-machine and sandbox prefixes so the virtual path is correct), so this is a **documentation** defect, not dead code. Do not delete the module on the strength of this row; correct the docstring and re-derive what it is actually for | `src/rudra/compat/deepagents_path.py:5-6`; probe output recorded in this row |

### A5 — Config / environment hygiene (found during Task 8 live smoke run)

| # | Status | Item | Evidence |
|---|---|---|---|
| A5.1 | **DONE (load_dotenv half only)** 2026-08-07 | **Local `.env`'s `OLLAMA_NUM_PREDICT=-1` silently overrides `config.py:22`'s documented `131072` default, for *any* process, regardless of invocation directory — and broke the U.12 smoke run against `gemma4:31b-cloud`.** `config.py:9` calls bare `load_dotenv()`, which resolves `.env` relative to the location of the module doing the importing (walking upward from `src/rudra/config.py`'s own directory), not the process's cwd. Task 8's first smoke attempt ran `rudra` from a `mktemp -d` tempdir wholly outside the repo and still picked up the repo's `.env` this way. `.env` sets `OLLAMA_NUM_PREDICT=-1` (a value meaningful to plain local Ollama as "unbounded"), which — because `os.getenv` in `config.py:16-22` only falls back to its coded default when the var is *unset*, not when it's present with an unwanted value — took priority over the `131072` default and was forwarded verbatim into `ChatOllama(num_predict=-1, ...)` (`planner_agent.py:79-85`, `coder_agent.py:61-67`). `langchain-ollama 1.1.0` puts this straight into the request's `options.num_predict` (`langchain_ollama/chat_models.py:782`); Ollama's cloud passthrough for `gemma4:31b-cloud` rejects it server-side: `ollama._types.ResponseError: max_tokens must be positive, got: -1 (status code: 400)`. Reproduced directly: instrumenting `ChatOllama._chat_params` mid-run printed `options={'num_predict': -1, 'temperature': 0.3}` right before the crash. `.env` is gitignored (`git check-ignore -v .env` → `.gitignore:139`), so this is not a tracked-file defect, but the `load_dotenv()` call-site behavior in `config.py:9` is: any contributor with a `.env` anywhere upward of `src/rudra/` — including one copied from `.env.example`, which per **A1.5** already ships a different wrong value (`2048`) — gets a silent override no matter what directory they run `rudra` from. Not fixed here (out of Task 8's scope: no `src/` edits permitted); worked around for U.12 by explicitly exporting `OLLAMA_NUM_PREDICT=131072` on the smoke-run command line, which takes precedence over `.env` per `python-dotenv`'s default `override=False`. Fix belongs with A1.5/A1.15: either `load_dotenv(override=False)` from the actual project root only (not module-relative), or validate `num_predict > 0` in `OllamaConfig` and reject/warn on nonsense values. **Closed 2026-08-07 (Step 3, Task 4) on the first leg only.** `config.py`'s `Config.load()` now calls `load_dotenv(Path.cwd() / ".env")` — an explicit path derived from the invocation directory, not a bare `load_dotenv()` walking upward from this module's own location — with `override=False` retained so a real env var still beats `.env`. `.venv/bin/pytest tests/test_config.py -v` → `test_dotenv_is_looked_up_at_the_cwd_only`, `test_dotenv_in_the_cwd_is_honored`, and `test_a_real_env_var_beats_dotenv` all pass. **The second leg — validating `num_predict > 0` — is deliberately declined, not merely deferred.** This row's own leg-(1) evidence above already establishes that `-1` is a *valid* value for plain local Ollama, meaning "unbounded," and that only Ollama's **cloud** passthrough rejects it server-side; a validator in `OllamaConfig` would reject a legal value for the local-first default case in order to protect a hosted one it doesn't even talk to by default. Value legality is provider-specific, not a `config.py` concern, and belongs with the Step 5 model factory (**C1.x** — see the note added to **C1.4**), which is the first place in the codebase that will actually know which provider it is talking to. A future session should not read this row's original text and conclude the validation suggestion was silently dropped. **See A5.2** (added 2026-08-07, final review) for a related, still-open gap this fix did not cover: the `.env` lookup is keyed to `Path.cwd()`, not to `--project-dir`'s resolved project root | repro: `python -c` instrumenting `ChatOllama._chat_params`, printed in Task 8 report; `src/rudra/config.py:9,16-22`; `src/rudra/agent/planner_agent.py:79-85`; `.venv/lib/python3.12/site-packages/langchain_ollama/chat_models.py:782`; `.env:9` (local, gitignored) vs `.env.example` (A1.5). **NOT a 0.7.4 regression — confirmed on all three legs:** (1) `curl` straight at `/api/chat` with `"options":{"num_predict":-1}` and no langchain/deepagents/Rudra anywhere in the path returns `{"StatusCode":400,"error":"max_tokens must be positive, got: -1"}`, while the same request with `131072` succeeds — the constraint is the Ollama **cloud** endpoint's, not the client stack's; `-1` remains valid for plain local Ollama. (2) Constructing `ChatOllama(num_predict=-1, temperature=0.3)` and reading `._chat_params([])['options']` under both `langchain-ollama` versions gives the identical `{'num_predict': -1, 'temperature': 0.3}` — verified in a scratch venv on **1.0.1** (pre-upgrade) and in the repo's own `.venv` on **1.1.0** (current): neither version filters or clamps a non-positive `num_predict`, both forward it verbatim. (3) Therefore this would have failed identically on 0.4.12/1.0.1, so Step 1's "behaves exactly as it does today" success condition is intact |
| A5.2 | PENDING | **Found by final whole-branch review 2026-08-07.** The `.env` lookup fixed by **A5.1** is keyed to the process's current working directory, but the CLI separately accepts `--project-dir`/`-d` to name a different project root — so `rudra -d /path/to/proj "task"`, run from anywhere else, ignores `/path/to/proj/.env` completely and instead reads (or misses) whatever `.env` happens to sit in the invocation directory instead. `Config.load` (`src/rudra/config.py:67`) calls `load_dotenv(Path.cwd() / ".env")` — `Path.cwd()`, not the resolved project path. A5.1's own remediation text asked for `.env` to be read "from the actual project root only"; cwd *is* the project root only when `-d` is absent, a condition A5.1's fix did not check for. Compounding constraint a fixer will hit: `cli.py:212` calls `get_config()` — which caches the `Config` instance for the rest of the process (`config.py`'s module-level `_config`, set in `get_config`) — *before* `project_path` is computed at `cli.py:214` from `project_dir`. A correct fix therefore can't just change the path expression passed to `load_dotenv`; it has to reorder `cli.py` so `project_path` is known before `get_config()` is first called, or make `Config.load` accept an explicit project root instead of reading `Path.cwd()` itself. Should land before the Step 5 model factory (`C1.x`) inherits the same cwd-vs-project-root assumption baked into `Config.load` today | `src/rudra/config.py:67` (`load_dotenv(Path.cwd() / ".env")`); `src/rudra/cli.py:212` (`verbose = get_config().agent.verbose`) vs `:214` (`project_path = get_project_path(project_dir)`) — verified by direct read, `get_config()` precedes `project_path` in source order in the same function body. Cross-ref **A5.1** |

---

## SECTION B — Requirement Gap Map

| Requirement | State | Evidence |
|---|---|---|
| Any API provider | ❌ Ollama hardcoded | `config.py:12-22`, `planner_agent.py:75`, `coder_agent.py:61`; `langchain-openai` not installed |
| MCP | ❌ Nothing | no MCP package, no config surface |
| Tools | ⚠️ built-ins only; Rudra's tools unwired; **no shell** | `coder_agent.py` — the `tools=[]` argument in `create_coder_agent`'s `create_deep_agent(...)` call; plain `FilesystemBackend` → `execute` errors (premise re-opened by U.17) |
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
| U.1 | **DONE** 2026-08-05 | Bumped `deepagents==0.7.4` in `pyproject.toml:26-38` (Core Agent Framework block: header, EXACT-pin comment, the pin itself at line 30, langgraph deps, LLM Integration header). Forced `langchain>=1.3.14`, `langchain-core>=1.5.0`, `langchain-ollama>=1.1.0` (`pyproject.toml:36-38`). `.venv/bin/python -c "from importlib.metadata import version; print(version('deepagents'))"` → `0.7.4`. `.venv/bin/pytest tests/test_deepagents_contract.py tests/test_fix_write_params.py -v` → `18 passed in 1.10s` (11 contract + 7 fix_write_params, no warnings) |
| U.2 | **DONE** 2026-08-05 | New transitive hard deps declared intentionally in `pyproject.toml:42-46`: `langchain-anthropic>=1.5.3`, `langchain-google-genai>=4.3.1`, `langsmith>=0.10.9`, `wcmatch>=11.0`, `packaging>=23.2`. `uv sync` resolved 107 packages, exit 0. **Found by final whole-branch review 2026-08-05, noted (not fixed):** this step also silently carried a transitive **downgrade**, `websockets` 16.0 → 15.0.1 (`uv.lock`: merge-base `b322978` pins `websockets==16.0`; branch HEAD pins `websockets==15.0.1`, pulled in via `uvicorn[standard]`'s `ws` extra, `pyproject.toml:52`). `grep -rn "uvicorn" src/` → no hits: `uvicorn` has no entry point in this codebase today, so the risk is nil. But it is a real, silent backwards version move in a step whose success condition was "no behavior change", and this ledger previously said nothing about it |
| U.3 | **DONE** 2026-08-05 | **Deleted `compat/overwrite_backend.py`.** 0.7.4 `FilesystemBackend.write()` overwrites by default (`backends/filesystem.py:489`, uses `O_TRUNC` + `O_NOFOLLOW`). ~~Only the markdown-fence stripping was still load-bearing → moves to `FixWriteParamsMiddleware` (D4)~~ **CORRECTED 2026-08-05: nothing needs porting.** `FixWriteParamsMiddleware` already strips fences (`fix_write_params.py:19`, `:31-33`, `:63-64`). U.3 reduced to swapping the constructor at `main_agent.py:558` and deleting the 65-line file — its sole instantiation. Two gaps the deletion exposes are tracked as U.14 and U.15 (both DONE). **Executed:** import swapped `main_agent.py:550` (`rudra.compat.overwrite_backend.OverwriteFilesystemBackend` → `deepagents.backends.filesystem.FilesystemBackend`), constructor at `main_agent.py:558-561` now `FilesystemBackend(root_dir=str(project_path), virtual_mode=True)` with a comment explaining why the subclass is gone. `git rm src/rudra/compat/overwrite_backend.py`. `grep -rn "OverwriteFilesystemBackend\|overwrite_backend" src/ tests/` finds only historical comments left by U.14/U.15/U.16/U.18 documenting the deletion — no import or instantiation remains outside the deleted file. `.venv/bin/ruff check src/` → 164 errors (≤213 bar). `.venv/bin/pytest tests/ -v` → 18 passed, no warnings. **CORRECTED 2026-08-05 (final whole-branch review):** the `:554`/`:554-560` citations above had drifted from the file (actual `:558`/`:558-561`) after later edits shifted line numbers; corrected in place (MINOR 6). Same review recorded two more undocumented byte-level deltas from this deletion — see the two rows immediately below | `main_agent.py:558-561`; verified by reading the file directly, not by trusting the prior citation |
| U.3a | **DONE** 2026-08-05 (found by final whole-branch review) | **Undocumented byte-level delta: CRLF vs LF.** The deleted `OverwriteFilesystemBackend` wrote via plain `Path.write_text(...)`, which performs CRLF translation for text mode on Windows. Upstream `FilesystemBackend.write()` opens with `newline=""` (`.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:512-514` — comment: "newline=\"\" disables Windows CRLF translation so callers that pass LF-only content get LF-only bytes on disk"), i.e. LF-only regardless of OS. LF is the better outcome, but it is an observable behavior change and this repo ships cross-platform path shims (`compat/deepagents_path.py`), so it belongs on the record rather than being an implicit side effect of U.3 | `.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:512-514` |
| U.3b | **DONE** 2026-08-05 (found by final whole-branch review) | **Undocumented byte-level delta: symlink handling.** The deleted `OverwriteFilesystemBackend` wrote straight through `Path.write_text(...)`, which follows symlinks. Upstream opens with `O_NOFOLLOW` when the platform supports it (`.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:507-511`), refusing to write through a symlink. This is security hardening gained incidentally by U.3, not something U.3 set out to do or recorded | `.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:507-511` |
| U.4 | **DONE** 2026-08-05 | Re-verified `compat/deepagents_path.py`. `validate_path` still exists at `backends/utils.py:648` and is still imported into `middleware/filesystem.py:77`, so the two-target monkeypatch still applies. **Guarded:** `install_path_normalizer` (`src/rudra/compat/deepagents_path.py:72-86`) now calls `require_deepagents_version("U.4")` and `require_deepagents_attr(...)` for both `deepagents.backends.utils.validate_path` and `deepagents.middleware.filesystem.validate_path` before touching either module, via the new `src/rudra/compat/version_guard.py` (`EXPECTED_DEEPAGENTS_VERSION = "0.7.4"`, `DeepagentsCompatError`). In-situ check: `.venv/bin/python -c "from rudra.compat.deepagents_path import install_path_normalizer; install_path_normalizer(...)"` → `install_path_normalizer OK`. Covered by `tests/test_version_guard.py` (6 tests) | `src/rudra/compat/deepagents_path.py:72-86`, `src/rudra/compat/version_guard.py`, `tests/test_version_guard.py` |
| U.5 | **DONE** 2026-08-05 | **`TodoListMiddleware` is no longer auto-added** in 0.7.4's main stack (`graph.py:634` comment; it appears only inside the Codex harness profile). No code change needed: Rudra never relied on `write_todos` — `planning_tools.py:3` already supplies `update_plan`/`write_task_assignment` as the planner's own tools, replacing it entirely. Locked by `tests/test_deepagents_contract.py::test_default_stack_has_no_write_todos_tool`, which asserts `write_todos` is absent from the compiled agent's default tool stack under 0.7.4 (passed in Task 8's Step 4 run: `24 passed in 0.50s`) | `src/rudra/tools/planning_tools.py:3`; `tests/test_deepagents_contract.py::test_default_stack_has_no_write_todos_tool` |
| U.6 | **DONE** 2026-08-05 | `backend` param is now typed `BackendProtocol` only (no `BackendFactory`). No code change needed: `main_agent.py:558-561` always constructs and passes a `FilesystemBackend(...)` **instance**, never a factory callable — no factory usage exists anywhere in `src/`. Locked by `tests/test_deepagents_contract.py::test_create_deep_agent_routes_writes_to_the_passed_backend`, which drives a real graph run (not just tool-name presence) to prove the tmp_path-rooted instance passed in is the one actually wired through, not silently substituted (passed in Task 8's Step 4 run: `24 passed in 0.50s`) | `src/rudra/agent/main_agent.py:558-561`; `tests/test_deepagents_contract.py::test_create_deep_agent_routes_writes_to_the_passed_backend` |
| U.7 | PENDING | Adopt `permissions: list[FilesystemPermission]` — new first-class param with `allow` / `deny` / `interrupt` modes (`middleware/filesystem.py:382-416`). This replaces most of hand-rolled C3.3 |
| U.8 | WONTFIX | `RubricMiddleware` (`middleware/rubric.py`) — **not adopted** per D9. Grader-subagent loop overlaps the reviewer subagent (C6.3) and would gate termination on an LLM verdict. Rationale + revisit trigger in §0.2. Left here so a future session does not "discover" it and re-litigate |
| U.9 | PENDING | Adopt provider profiles (`deepagents.profiles.provider`): `register_provider_profile` + built-in OpenRouter / NVIDIA / OpenAI profiles feed `init_chat_model` kwargs. Feeds directly into Phase 1 |
| U.10 | PENDING | Evaluate harness profiles (`HarnessProfile`, `register_harness_profile`, `excluded_tools`) for per-model tool visibility and prompt tuning — a clean home for per-model quirks instead of Rudra middleware |
| U.11 | PENDING | Note `AsyncSubAgentMiddleware` / `AsyncSubAgent` (`graph_id`-keyed) exists for out-of-process subagents. Not needed now; record for Phase 6 |
| U.12 | **DONE** 2026-08-05 | **Live smoke run executed end-to-end against `gemma4:31b-cloud` via Ollama cloud (Task 8).** Cloud signin is active (contrary to the stale note this row carried); Step 1's `curl /api/chat` with a `calc` tool returned `"tool_calls":[{"function":{"name":"calc","arguments":{"expr":"17 * 23"}}}]` — tool-calling confirmed before spending a full agent run on it. **First attempt failed** (see A5.1, new PENDING below) with `ollama._types.ResponseError: max_tokens must be positive, got: -1`, traced to the repo-root `.env` (gitignored, `git check-ignore -v .env` → `.gitignore:139`) setting `OLLAMA_NUM_PREDICT=-1`; `load_dotenv()` (`config.py:9`) resolves `.env` from `config.py`'s own module location and walks upward, not from the invoking cwd, so even a run from a tempdir outside the repo inherited it. **Second attempt, with `OLLAMA_NUM_PREDICT=131072` added to the env-var overrides to restore `config.py:22`'s documented default, exited 0.** Command: `OLLAMA_MODEL_PLANNER=gemma4:31b-cloud OLLAMA_MODEL_CODER=gemma4:31b-cloud OLLAMA_BASE_URL=http://localhost:11434 OLLAMA_NUM_PREDICT=131072 timeout 900 .venv/bin/rudra "build a python CLI that reverses a string"` run from `/tmp/tmp.sWHZEcMhl3` (mktemp -d, outside the repo). `ChatOllama` construction under `langchain-ollama 1.1.0` (`planner_agent.py:79-85`, `coder_agent.py:61-67`, incl. `reasoning=True` and `num_predict=`) worked without error in both attempts — the crash was a request-payload value, not a construction failure. Planner ran `update_plan({'plan_markdown': '- [ ] main.py'})` then `write_task_assignment(...)`; coder ran `read_file(.rudra/current_task.md)` → `read_file(.rudra/tech_stack.md)` → `write_file(main.py, ...)`. Artifacts: `.rudra/PLAN.md` = 13 bytes (`- [x] main.py`), `main.py` = 564 bytes of valid, complete `argparse`-based Python (not judged for quality per Task 8 scope). Console summary: `✓ 1/1 files generated · Files created: 1 · Files modified: 0 · Iterations: 1`. **No zero-byte files** — confirms `FilesystemBackend(root_dir=..., virtual_mode=True)` (`main_agent.py:558-561`, post-U.3) still writes through to disk with a real model, closing the last open question from U.3/U.16's static probe. Step 4 full-checklist run (same session): `deepagents 0.7.4`; `pytest tests/ -v` → `24 passed in 0.50s`; `rudra --version` → `Rudra v0.1.0` (A1.1, expected, not a bug); `ruff check src/` → `164` errors (≤213 bar); `git status --short` → clean before this edit | Task 8 report: `.superpowers/sdd/2026-08-05-deepagents-074-upgrade/task-8-report.md`; smoke logs at `/tmp/tmp.DjLfiBMKtv/smoke.log` (failed attempt) and `/tmp/tmp.sWHZEcMhl3/smoke.log` + `/tmp/tmp.sWHZEcMhl3/main.py` + `/tmp/tmp.sWHZEcMhl3/.rudra/PLAN.md` (passing attempt). **Note added by final whole-branch review 2026-08-05:** these evidence pointers are ephemeral and do not resolve — the task report lives under gitignored `.superpowers/`, and the `/tmp/tmp.*` smoke-log directories no longer exist on this machine. The substance of what they showed is pasted inline in this row's own text, so the entry still stands alone; this is a flag on the citations, not a retraction of the finding |
| U.13 | **DONE** 2026-08-05 | **Private deepagents API import, now guarded.** `task_anchor.py:20` did `from deepagents.middleware._utils import append_to_system_message`. A leading-underscore module carries no stability guarantee, and `TaskAnchorMiddleware` is KEEP-opt-in per D4, wired into **both** agents (`planner_agent.py:93`, `coder_agent.py:71`). **Guarded:** `src/rudra/middleware/task_anchor.py:20-24` now binds `append_to_system_message = require_deepagents_attr("deepagents.middleware._utils", "append_to_system_message", "U.13")` from the new `src/rudra/compat/version_guard.py`; usage at `task_anchor.py:59` (formerly `:54`) is unchanged. In-situ check: `.venv/bin/python -c "from rudra.middleware.task_anchor import append_to_system_message; print(callable(append_to_system_message))"` → `task_anchor import OK: True`. Covered by `tests/test_version_guard.py` (6 tests) | `src/rudra/middleware/task_anchor.py:20-24`, `src/rudra/compat/version_guard.py`, `tests/test_version_guard.py` |
| U.14 | **DONE** 2026-08-05 | **Planner loses fence-stripping once U.3 lands.** `FixWriteParamsMiddleware` is on the coder only (`coder_agent.py:70`); the planner carries only `TaskAnchorMiddleware` + `BlockPrematureAskMiddleware`. Today `OverwriteFilesystemBackend` covers the planner; after deletion nothing does. Add `FixWriteParamsMiddleware()` to the planner middleware list. **Verified:** `ast.parse` passes, `FixWriteParamsMiddleware()` present in source, `ruff check src/rudra/agent/planner_agent.py` passes with no new errors. Import widened to multi-line, middleware added at start of list with explanatory comment explaining the backend deletion in U.3. | `src/rudra/agent/planner_agent.py:92-95` vs `coder_agent.py:70`; verification: `ast.parse src/rudra/agent/planner_agent.py` OK, `ruff check` OK |
| U.15 | **DONE** 2026-08-05 | **Fence regex narrower than the backend's.** Middleware `_FENCE_RE` uses `[a-zA-Z0-9_\-]*` for the info string; the backend used `[^\n]*`. Fenced blocks with attributes (` ```py title="x" `) match the backend but not the middleware → coverage shrinks at U.3. Widened to `[^\n]*` in `fix_write_params.py:19` with explanatory comment. Verified via `.venv/bin/pytest tests/test_fix_write_params.py -v` → all 7/7 passing (test_strips_fence_with_info_string_attributes was RED before, GREEN after regex widening). **Note added by final whole-branch review 2026-08-05:** the widening also changed a trailing-newline byte. The deleted backend's regex was `` ^```[^\n]*\n(.*)\n```$ `` — the `\n` immediately before the closing fence is consumed as a delimiter, excluded from the captured group. The middleware's widened regex is `` ^```[^\n]*\n?(.*?)```\s*$ `` (`fix_write_params.py:22`) — the leading `\n` is now optional and the non-greedy capture runs right up to the closing fence, so it *includes* the pre-closing newline. Verified directly: both regexes against `` ```python\nprint("hi")\n``` `` — old capture is `'print("hi")'`, new capture is `'print("hi")\n'`. One byte, and arguably more correct (matches what a human would call "the content"), but it means planner-path file content written after U.15 gains one trailing newline it would not have gained before. Recorded so it is not rediscovered as a mystery | `middleware/fix_write_params.py:19,22` vs `compat/overwrite_backend.py:43` (deleted; regex quoted here and in A4.9) |
| U.16 | **DONE** 2026-08-05 | **0.7.4 API discovery, verified against a disposable `.venv-probe`.** `uv pip install --dry-run` resolved 78 packages cleanly, exit 0 (deepagents 0.7.4, langchain 1.3.14, langchain-core 1.5.3, langchain-ollama 1.1.0, langgraph 1.2.10) — real install also exit 0, no constraint conflict, so no version loosening needed. `validate_path` is present and `callable` in both `deepagents.backends.utils` and `deepagents.middleware.filesystem`, and `is` confirms it is the same object → U.4's two-target monkeypatch is still required. `deepagents.middleware._utils.append_to_system_message` is present with signature `(system_message: SystemMessage \| None, text: str) -> SystemMessage` → U.13's import path is stable on 0.7.4. `FilesystemBackend.write()` overwrites (write #1 `a`, write #2 `b`, on-disk content `b`) and preserves markdown fences verbatim (fence-preserved check: `True`, i.e. 0.7.4 does **not** strip fences itself) → confirms U.3's "nothing needs porting" and means U.15's fence-stripping fix is still load-bearing, not cosmetic (**Blocker B did not trip**). `virtual_mode=True` writes through to disk — `landed on disk: True`, content `x` read back correctly (**Blocker A did not trip**; `main_agent.py:556`'s straight swap to plain `FilesystemBackend(virtual_mode=True)` is safe). `create_deep_agent` signature now has 18 params incl. new `permissions`, `skills`, `interrupt_on`, `cache` (U.6/U.7/U.9 raw material). `write_todos` is absent from the compiled agent's default tool stack — the `'tools'` node (accessor: `agent.nodes['tools'].bound.tools_by_name`) lists exactly `['delete', 'edit_file', 'execute', 'glob', 'grep', 'ls', 'read_file', 'task', 'write_file']` → confirms U.5. `ChatOllama` + `create_deep_agent` construction made no network call (script ran to completion, no hang) even with an unreachable `base_url`. Raw output: scratchpad `probe_074.out` | `.venv-probe`, `probe_074.py` |
| U.17 | PENDING | **`execute` IS registered on a plain `FilesystemBackend` in 0.7.4 — re-test the premise behind C3.1.** Task 1's probe built an agent with `backend=FilesystemBackend(...)` (no shell backend, no composite) and the default tool stack came back as `['delete', 'edit_file', 'execute', 'glob', 'grep', 'ls', 'read_file', 'task', 'write_file']` — `execute` present. `CLAUDE.md` §4 asserts the opposite ("Rudra's `OverwriteFilesystemBackend` extends plain `FilesystemBackend` → the `execute` tool returns an error → Rudra's agents cannot run tests, linters, builds, or git"), and that assertion is the stated justification for C3.1 / Section E Step 7. **Registered ≠ functional** — it may still error at call time on a backend that is not a `SandboxBackendProtocol`, which would leave C3.1 intact. Nobody has *invoked* it. Before Step 7 is planned, actually call `execute` on a plain `FilesystemBackend` under 0.7.4 and record the result; then correct either `CLAUDE.md` §4 or this note. Out of scope for Step 1 — recorded here so the premise is re-tested rather than inherited | Task 1 probe output, scratchpad `probe_074.out:33`; `CLAUDE.md` §4 |
| U.18 | **DONE** 2026-08-05 | **Contract tests written and executed on both venvs — Task 1's §F claims turned into executable assertions.** `tests/test_deepagents_contract.py` (11 tests) locks: `validate_path` present + shared-by-identity across `deepagents.backends.utils` and `deepagents.middleware.filesystem` (U.4), `deepagents.middleware._utils.append_to_system_message` present with a 2-param signature (U.13), `langchain.agents.middleware.types.AgentMiddleware` importable, `FilesystemBackend.write()` overwrite + fence-preservation + `virtual_mode=True` write-through semantics (U.3), the `create_deep_agent` kwarg surface (`model`, `tools`, `system_prompt`, `backend`, `checkpointer`, `middleware`, `memory`), backend-routing proven behaviorally by actually running the graph (U.6 — see U.19), and the absence of `write_todos` from the default tool stack (U.5). **Final split after the U.19 fix round, under `.venv` (0.4.12): 4 failed / 7 passed** — `test_deepagents_version_is_exactly_pinned`, `test_write_overwrites_existing_file`, `test_default_stack_has_no_write_todos_tool` (all as originally reported), plus a fourth: `test_create_deep_agent_routes_writes_to_the_passed_backend` now fails too, because running the graph surfaced a real 0.4.12 path-scoping gap (see U.19's addendum) that the earlier tool-name-only version of this test could not see. Under `.venv-probe` (0.7.4): **11/11 pass**, output pristine, no warnings. No §F claim was found wrong beyond what U.19 already explains; no further new PENDING item required | `tests/test_deepagents_contract.py`; `.venv` run 4 failed/7 passed; `.venv-probe` run 11/11 passed |
| U.19 | **DONE** 2026-08-05 | **`write_file` (and the other filesystem tools) cannot be invoked directly via a bare `tool.invoke({...})` outside a compiled graph — confirmed on both venvs — resolved by actually running the graph with a scripted `BaseChatModel`, not by fabricating a `ToolRuntime`.** The tool's real signature carries an injected `runtime: ToolRuntime[NoneType, FilesystemState]` parameter that LangGraph's `ToolNode` supplies at call time inside a running graph; a direct `.invoke({"file_path": ..., "content": ...})` omits it. `.venv-probe` (0.7.4): `TypeError: sync_write_file() missing 1 required positional argument: 'runtime'`. `.venv` (0.4.12): `pydantic_core.ValidationError: 1 validation error for write_file / runtime / Field required`. Same failure mode on both versions — not a 0.7.4 regression, a property of how deepagents builds its filesystem tools generally. **Resolution:** `tests/test_deepagents_contract.py` now defines `ScriptedToolModel(BaseChatModel)` — `bind_tools` returns `self`, `_generate` emits one canned `write_file` tool call then `"done"` — and `test_create_deep_agent_routes_writes_to_the_passed_backend` drives the real compiled graph with it via `agent.invoke({"messages": [...]})`, then reads the file back from `tmp_path`. No network call; no provider reachability assumed; every `_local_model()`/`ChatOllama`/`gemma4` reference removed from the file so the suite runs on a machine with no Ollama at all. **New finding surfaced by actually running the graph (not visible to the old tool-name-only test):** under `.venv` (0.4.12), the scripted `write_file("wired.txt", "ok")` call resolves to `/wired.txt` — anchored at the real OS root, not `tmp_path` — and fails with `PermissionError: [Errno 13] Permission denied: '/wired.txt'`, silently caught and returned as a `ToolMessage` rather than raised, so the graph completes but the file never lands. This matches the `DeprecationWarning` 0.4.12 emits on every `FilesystemBackend(root_dir=...)` construction without an explicit `virtual_mode`: "leaving `virtual_mode=False` allows absolute paths and `..` to bypass `root_dir`." 0.7.4's default backend scopes relative paths to `root_dir` correctly with no such gap (test passes clean). This is a real, additional 0.4.12→0.7.4 behavior difference beyond what U.3 already documented — U.3's `virtual_mode=True` write-through claim was tested directly against the backend and remains correct, but the *default* (`virtual_mode` unset) backend's path scoping is a separate, previously-undiscovered gap that only running the actual graph revealed | `tests/test_deepagents_contract.py:51-87` (`ScriptedToolModel`), `:193-218` (`test_create_deep_agent_routes_writes_to_the_passed_backend`); confirmed via ad hoc probe script against both `.venv` and `.venv-probe`; `.venv` failure trace: `ToolMessage("Error writing file '/wired.txt': [Errno 13] Permission denied: '/wired.txt'")` |
| U.20 | **DONE** 2026-08-05 (found by final whole-branch review) | **Contract tests never locked parent-directory creation.** `tests/test_deepagents_contract.py` locked overwrite, fence-preservation, and `virtual_mode` write-through, but nothing asserted `mkdir(parents=True)`. The deleted `overwrite_backend.py` did this explicitly; upstream `FilesystemBackend.write()` happens to do it too (`.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:505`). The U.12 smoke run only produced a root-level `main.py`, so it never exercised the path either — a future deepagents bump that drops parent-dir creation would break every project Rudra builds with a `src/` layout, and the suite would stay green. **Fixed:** added `test_write_creates_parent_directories` to `tests/test_deepagents_contract.py`, writing `src/pkg/mod.py` and asserting the nested path lands on disk. Verified: `.venv/bin/pytest tests/ -v` → all passing (see U.23 row for the full count) | `tests/test_deepagents_contract.py::test_write_creates_parent_directories`; `.venv/lib/python3.12/site-packages/deepagents/backends/filesystem.py:505` |
| U.21 | **DONE** 2026-08-05 (found by final whole-branch review) | **`test_validate_path_is_shared_object` could pass vacuously, and the module docstring's reasoning was inverted.** `install_path_normalizer` assigns the **same** function object to both patch targets (`src/rudra/compat/deepagents_path.py:168-169`). Verified directly: the identity assertion passes both *before and after* `install_path_normalizer` runs. So if anything ever calls it before this test, the test goes **quiet** rather than failing — strictly worse than breaking, since it would keep "passing" while no longer proving what it claims to prove. The module docstring at `tests/test_deepagents_contract.py:9-14` stated the opposite — that calling `install_path_normalizer` "would invalidate the identity assertion" — which is backwards. **Fixed:** `test_validate_path_is_shared_object` now asserts `getattr(deepagents.backends.utils, "_rudra_original_validate_path", None) is None` first, refusing to (mis)prove the identity claim if the patch already ran; module docstring corrected to state the real hazard (silent quiet-pass, not breakage). Verified: `.venv/bin/pytest tests/ -v` → all passing, run as the full suite together (not in isolation), which is what exercises the ordering hazard this guard exists for | `tests/test_deepagents_contract.py:9-14` (docstring), `::test_validate_path_is_shared_object`; `src/rudra/compat/deepagents_path.py:88-91,168-169` |
| U.22 | **DONE** 2026-08-05 (found by final whole-branch review) | **Neither of the branch's two real source changes was locked by a test.** `main_agent.py:558-561` (swap to plain `FilesystemBackend(virtual_mode=True)`, U.3) and `planner_agent.py:100` (`FixWriteParamsMiddleware()` added to the planner, U.14) are the entire behavioral payload of this upgrade branch's non-mechanical changes, verified only by one live cloud smoke run (U.12) whose evidence is now unreachable (see U.12's own note, this review). No test imported `rudra.agent` at all before this. **Fixed:** new `tests/test_agent_wiring.py` — `test_planner_wires_fix_write_params_middleware_first` parses `planner_agent.py`'s AST to confirm `FixWriteParamsMiddleware` is in the `middleware=` list passed to `create_deep_agent` and is first; `test_main_agent_constructs_filesystem_backend_with_virtual_mode` parses `main_agent.py`'s AST to confirm a `FilesystemBackend(...)` call carries `virtual_mode=True` and that no import or construction of the deleted `OverwriteFilesystemBackend` remains (a historical prose mention in a comment is fine, per A4.9, and is explicitly allowed by checking imports/calls, not a bare substring). Uses source inspection rather than executing `create_main_agent()`, because that function calls `install_path_normalizer` and would trip U.21's new guard if run in the same suite; `planner_agent.py` itself never calls `install_path_normalizer` (checked by grep and asserted by `test_planner_agent_module_does_not_call_install_path_normalizer`), so importing it to read its source is safe. Verified: `.venv/bin/pytest tests/ -v` → all passing, whole suite together | `tests/test_agent_wiring.py`; `src/rudra/agent/main_agent.py:558-561`; `src/rudra/agent/planner_agent.py:96-104` |
| U.23 | **DONE** 2026-08-05 (found by final whole-branch review) | **Stale lint baseline in `CLAUDE.md` §8.** It said `ruff check src/` is "currently 213 errors" — that was the pre-upgrade count (A3.1). A worktree at merge-base `b322978` measures **164**, identical to HEAD, so the branch itself is exactly flat — but the `≤213` bar the plan inherited had ~30% slack and would not have caught a regression up to 48 new errors. **Fixed:** `CLAUDE.md:179` now reads "164 errors at merge-base `b322978`; standard is NO INCREASE vs that baseline, not an absolute ceiling." Verified: `.venv/bin/ruff check src/` → `Found 164 errors` (unchanged by this fix wave's own edits, confirmed again after all fixes below) | `CLAUDE.md:179`; `.venv/bin/ruff check src/` → 164 |
| U.24 | **DONE** 2026-08-05 (found by final whole-branch review) | **Stale numeric line citation into a live file.** `tests/test_deepagents_contract.py:164` (docstring of `test_virtual_mode_writes_through_to_disk`) cited `main_agent.py:556` for `virtual_mode=True`; the constructor moved during earlier work in this branch and `:556` is now a comment line — the argument is at `:560`. A different category from A4.9's citations into a *deleted* file: this one points into a file that still exists, just at the wrong line. **Fixed:** corrected the docstring to `main_agent.py:560`, verified by reading `src/rudra/agent/main_agent.py:558-561` directly rather than trusting the prior citation. (The matching citation drift inside `TODO.md`'s own U.3 row — `main_agent.py:554` / `:554-560` vs actual `:558` / `:558-561` — was corrected in place in the U.3 row above, same review, same commit.) | `tests/test_deepagents_contract.py:164`; `src/rudra/agent/main_agent.py:558-561` |
| U.25 | **DONE** 2026-08-05 (found by final whole-branch review) | **`version_guard.py`'s exception handlers could mask the real error.** Both `except` blocks in `require_deepagents_attr` called `version("deepagents")` **inside** the handler to build the error message (`src/rudra/compat/version_guard.py`, pre-fix: inside the `except ImportError` and `except AttributeError` blocks). If deepagents were not installed at all, that call raises `importlib.metadata.PackageNotFoundError` from within the handler, which — via implicit exception chaining — obscures the original `ImportError`/`AttributeError` that triggered the handler in the first place, defeating the whole point of a guard whose job is to fail legibly. **Fixed:** compute `deepagents_version` once, before the `try`, wrapped in its own `try/except Exception` that falls back to the literal string `"<not installed>"` so it can never itself raise; both handlers now reference that precomputed value. Verified: `.venv/bin/pytest tests/test_version_guard.py -v` → all 6 passing (message-content assertions unaffected, since no test asserts the exact version substring for the attr-guard's own version-in-message case) | `src/rudra/compat/version_guard.py:53-77` |

### Phase 0 — Stabilize

| # | Status | Item |
|---|---|---|
| C0.1 | PENDING | Fix all of A1 not already resolved by U/D7 |
| C0.2 | **DONE** (partial) 2026-08-06 | Execute D4 + D7 deletions: `execution_tools.py`, `filesystem/sync.py`, `filesystem/virtual_fs.py`, `block_premature_ask.py`, `block_task_tool.py`, `continue_after_write.py`, `enforce_target_file.py`, `compat/overwrite_backend.py`. **Note:** deleting `block_premature_ask.py` does *not* end the ask-blocking — its prompt-level twin at `planner_agent.py:63` survives. Tracked as §0.5 surface #6<br><br>Four D4 middlewares deleted. **Not fully closed:** the prompt-level ask-block at `planner_agent.py:63` (`"Do NOT call ask_user() if the task already specifies a framework or language"`) survives by design — it is §0.5 surface #6 and dies with C6.8a. `execution_tools.py`, `filesystem/sync.py`, `filesystem/virtual_fs.py` deleted here; `compat/overwrite_backend.py` was already deleted in U.3. |
| C0.3 | **DONE** 2026-08-06 | Replace VFS with a capped `project_tree()` helper: names only, depth + entry cap, `wcmatch` gitignore semantics, **never loads file content**. **Verified 2026-08-06 (Task 7):** `project_tree()` (`src/rudra/filesystem/tree.py:41`) — depth cap (`max_depth` param, tested by `test_max_depth_drops_deeper_paths`), entry cap (`max_entries` param, tested by `test_max_entries_truncates_and_reports_remainder`), `wcmatch`-based gitignore translation (`_to_wcmatch`, `tree.py:169`; `_matches`, `tree.py:192`), and content-blindness locked by two dedicated tests, `test_never_reads_the_content_of_a_listed_file_fallback` and `test_never_reads_the_content_of_a_listed_file_git`. `.venv/bin/pytest tests/test_project_tree.py -v` → 8 passed. `src/rudra/filesystem/virtual_fs.py` no longer exists |
| C0.4 | **DONE** 2026-08-07 | `ruff check --fix` + resolve remainder; add `ruff format`. **Verified 2026-08-07 (Step 3, Task 6):** the brief's 30-error baseline was stale (written before Tasks 3-5 added three test files and rewrote `config.py`); fresh measurement was **26** (`I001`:9, `W293`:9, `F541`:3, `E741`:2, `W291`:2, `F401`:1). `.venv/bin/ruff check src/ tests/ --fix` cleared 21 (this ruff version — 0.15.8 — treats `F541` as a safe fix, not hidden behind `--unsafe-fixes` as the brief assumed; verified by diff review that the 3 `print_banner()` sites in `src/rudra/cli.py` were genuinely stray `f` prefixes on strings with no `{}`, and the `{__version__}` line at `cli.py:82-85` kept its prefix). `--unsafe-fixes --diff` then showed only 3 `W293` (trailing whitespace on blank lines inside docstrings in `src/rudra/state/project_config.py:34,43,57`, content-preserving) — applied via `--unsafe-fixes --fix`. Remaining 2 `E741` (`l` ambiguous with `1`/`I`) fixed by hand at `src/rudra/tools/planning_tools.py:84-85`, renamed to `line`; confirmed no shadowing conflict with the unrelated `for line in plan_markdown.splitlines()` loop later in the same function (generator-expression scope is isolated, and that loop runs after the shrink-rejection block completes). **Citation corrected 2026-08-07 (Step 3, final review):** `ruff format`, applied later in this same task, shifted these two lines down from their original `:84-85`; the renamed `line` variables now sit at `src/rudra/tools/planning_tools.py:85` and `:88`. `.venv/bin/ruff check src/ tests/` → `All checks passed!`. Added `[tool.ruff.format]` with `docstring-code-format = true` to `pyproject.toml`, after `[tool.ruff.lint]`; did not touch `line-length`/`target-version`/`select`/`ignore`. `.venv/bin/pytest -q` → **62 passed** immediately before `ruff format src/ tests/`; format reformatted 13 files (18 unchanged); `--check` afterward → `31 files already formatted`; `.venv/bin/pytest -q` → **62 passed** again, identical count, confirming the format pass was behavior-preserving. **New absolute standard, supersedes `CLAUDE.md` §8's relative "no increase vs the 164-error baseline at merge-base `b322978`" rule: `ruff check src/ tests/` must exit 0.** `CLAUDE.md` §8's wording was intentionally left unedited here (out of scope for this task) — it needs the same update when Step 4's CI lands |
| C0.5 | PENDING | pytest suite: path normalizer, PLAN parsing, config layering, model factory, tree helper |
| C0.6 | PENDING | GitHub Actions CI: ruff + pytest on 3.12/3.13 |
| C0.7 | **DONE** 2026-08-07 | Move `ruff`/`pytest` to `[dependency-groups] dev`; drop duplicate dep; add `aiosqlite`. **Verified 2026-08-07 (Step 3, Task 2):** `pyproject.toml` `[dependency-groups] dev` now holds `ruff>=0.15.8` and `pytest>=9.0.2`; `uv sync` (default, no `--group` flag needed) installs the group. `.venv/bin/ruff --version` → `ruff 0.15.8`; `.venv/bin/pytest -q` → `37 passed in 0.68s`. Distribution count dropped 105 → 73 |
| C0.9 | PENDING | **Implement the D15 `.rudra/` layout** — durable vs `run/` volatile split, plus Rudra writing `.rudra/.gitignore`. Do it in Stage I: every later phase (checkpoints C7.2, palace C8.1a, logs C3.2, facts C6.8a) writes into this tree, so settling paths now avoids migrating them four times |
| C0.10 | **DONE** 2026-08-05 | **Deleted dev debris:** `.rudra/` and `coding-files/` (10 files, 376 KB) — output from test runs of `rudra` against its own checkout. All untracked (`git ls-files -- .rudra coding-files` → empty), so no history impact. Verified gone; `git status --short` shows only the new `CLAUDE.md` / `TODO.md` |
| C0.8 | **DONE** 2026-08-07 | Pin `deepagents==0.7.4` exactly while `compat/deepagents_path.py` monkeypatches exist, with a comment pointing at U.4. **Verified 2026-08-07 (Step 3, Task 2): required no edit.** `"deepagents==0.7.4"` already at `pyproject.toml:30`, with the explanatory comment (`pyproject.toml:27-29`) pointing at `TODO.md U.4, U.13, C0.8` — it landed during Step 1 and was simply never marked DONE. Left untouched |

### Phase 1 — Provider-agnostic model layer

| # | Status | Item |
|---|---|---|
| C1.1 | PENDING | `src/rudra/llm/factory.py`: `build_model(role) -> BaseChatModel`. Providers: `ollama`, `openai_compatible` (vLLM / OpenRouter / LM Studio / Groq / Together), `anthropic`, `openai`, `google`. Layer on `register_provider_profile` (U.9) rather than reimplementing |
| C1.2 | PENDING | Add `langchain-openai>=1.4.1` — required for every OpenAI-compatible endpoint |
| C1.3 | PENDING | Delete `OllamaConfig`; replace with role-based `ModelConfig` (`planner`, `coder`, `reviewer`, `summarizer`). Keep `OLLAMA_*` env working one release with a deprecation warning |
| C1.4 | PENDING | Move Ollama-only kwargs (`num_predict`, `reasoning=True`) behind a provider check — invalid elsewhere and will raise on Anthropic/OpenAI (`planner_agent.py:78-80`, `coder_agent.py:64-66`). **Forwarded from A5.1 (2026-08-07):** when this lands, also decide `num_predict` value legality per provider — `-1` is valid for plain local Ollama ("unbounded") but rejected by Ollama's cloud passthrough with `max_tokens must be positive, got: -1`. A1.15/A5.1's fix (Step 3, Task 4) deliberately did not add a blanket `num_predict > 0` validator to `OllamaConfig` for exactly this reason; that check belongs here, where the provider is known |
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

### Phase 11 — Multi-language stack detection (D18)

| # | Status | Item |
|---|---|---|
| C11.1 | **DONE** 2026-08-07 | Build `src/rudra/stacks/` — `StackProfile`, registry, `detect()`, `resolve_test_command()`. Reads files and returns data; **never executes a subprocess** (execution is C3.6's, in Step 8). **Verified 2026-08-07 (Step 4, Task 3):** `detect()` (`src/rudra/stacks/detect.py:50`) reads marker/require/dependency files off disk and returns a list of matching `StackProfile`s sorted most-specific-first (`-specificity, name`), `[]` on greenfield or a missing directory. `resolve_test_command()` (`detect.py:68`) returns the profile's own `test_command` when declared, else reads Node's `package.json` `scripts.test`, else `None` — a real "no test command" answer, never a guessed default. Malformed `package.json` degrades to `{}` via `_load_package_json`'s `except (OSError, ValueError)` rather than raising, since a half-written manifest is a normal mid-task state. Exported from `src/rudra/stacks/__init__.py`. `grep -rn "subprocess\|os.system\|popen\|Popen" src/rudra/stacks/` → no output, confirming the no-subprocess constraint. `.venv/bin/pytest tests/test_stacks.py -v` → 23 passed (8 from Task 2 + 15 new); `.venv/bin/pytest -q` → **85 passed**. `.venv/bin/ruff check src/ tests/` → all checks passed. **Fixed 2026-08-07 (review):** `_load_package_json` (`detect.py:17-28`) only caught `(OSError, ValueError)`, so syntactically-valid-but-wrong-shape JSON (a bare list or scalar) parsed successfully and was then handed to `.get()` calls in `_declares_dependency` and `resolve_test_command`, raising `AttributeError`. Fixed by returning `parsed if isinstance(parsed, dict) else {}`; docstring now says "unparseable, or not a JSON object". Reproduced both shapes (`["react","vue"]` and `"just a string"`) degrading to `[]`/`None` instead of raising, before and after confirmed via direct script. Two new tests per function pin both shapes: `test_list_shaped_package_json_does_not_crash_detection`, `test_scalar_shaped_package_json_does_not_crash_detection`, `test_resolve_test_command_is_none_when_package_json_is_list_shaped`, `test_resolve_test_command_is_none_when_package_json_is_scalar_shaped`. `.venv/bin/pytest tests/test_stacks.py -v` → **27 passed**; `.venv/bin/pytest -q` → **89 passed**; `.venv/bin/ruff check src/ tests/` → all checks passed |
| C11.2 | PENDING | Acceptance matrix: one end-to-end case per stack (Rust, Python, Node, React, Angular), greenfield and brownfield, replacing §0.5's single Rust example. Needs a live model backend — cannot run until one is available |
| C11.3 | PENDING | Angular unit tests require a headless browser (Angular CLI's default builder runs Karma against Chrome). Detect its absence and fail fast with a message naming the missing dependency, rather than hanging on a browser that never launches — inside Step 9's fix loop a hang stalls the loop instead of failing a round |

---

## SECTION E — Execution Order

Each numbered step is one fresh session with its own self-contained plan under `plans/`. Do not start a step until its dependencies are `DONE`.

### Stage I — Make the base sound (no new features)

| Step | Work | Depends on | Why here |
|---|---|---|---|
| **1** | **U.1 – U.6, U.12 – U.15** — deepagents 0.7.4 upgrade + smoke test. Design spec: `docs/superpowers/specs/2026-08-05-deepagents-074-upgrade-design.md` — **STEP 1 COMPLETE 2026-08-05.** All of U.1–U.6, U.12–U.16, U.18 are DONE (U.5/U.6/U.12 closed in Task 8; the rest in Tasks 1–7); U.8/U.11 are WONTFIX/deferred-by-design, not blockers; U.7/U.9/U.10/U.17 are deliberately deferred to later steps (U.7→step 7, U.9→step 5, U.10 evaluate-later, U.17 re-test-later) and were never in this step's scope. `.venv` is on `deepagents==0.7.4`, `24/24` tests pass, and a real agent construction + execution smoke run against `gemma4:31b-cloud` completed with non-empty `PLAN.md` and non-empty generated `main.py` (U.12, Task 8). **Step 2 (`C0.2, C0.3, A2.1–A2.6, D7`) is unblocked.** | A4.8 (**DONE**) | Riskiest change in the repo. Every later phase builds on 0.7.4 APIs. Doing it after new code means porting twice. A4.8 was pulled forward and fixed first — `tests/` was gitignored, so the contract tests could not be committed |
| **2** | **C0.2, C0.3, A2.1–A2.10, A2.12, A2.13, D7** — delete dead code, delete VFS, add capped `project_tree()` | 1 | Removes ~1,100 lines. Everything after touches less surface. Memory budget fix (D7) — **STEP 2 COMPLETE 2026-08-06.** `C0.2` (partial — `planner_agent.py:63` survives per §0.5 #6), `C0.3`, `A2.1`–`A2.10`, `A2.12`, `A2.13`, `D7`, `A1.4`, `A1.6`, `A1.10` are DONE. `A2.7` also DONE (verified removed in Task 6's sweep, commit `a484551`). `A2.11` stays PENDING by design, deferred to C0.7. `git diff --stat 040dae4..HEAD -- src/ execution_tools.py` → 18 files changed, 225 insertions, **1,520 deletions**; `project_tree()` (`src/rudra/filesystem/tree.py:41`) replaces `VirtualFileSystem.get_tree()`. `.venv/bin/ruff check src/` → **30 errors** (down from the 164 baseline at merge-base `b322978`). `.venv/bin/pytest tests/ -q` → **36 passed**. Live smoke run against `gemma4:31b-cloud` from a `mktemp -d` tempdir outside the repo (`OLLAMA_NUM_PREDICT=131072` override per A5.1) exited 0: console summary `✓ 2/2 files generated · Files created: 2 · Files modified: 0 · Iterations: 2`; `.rudra/PLAN.md` = `- [x] main.py\n- [x] README.md`; generated `main.py` = **690 bytes**, a complete `argparse`-based CLI, executed directly and confirmed correct (`python main.py "hello world"` → `dlrow olleh`). Full transcript in `.superpowers/sdd/2026-08-06-step2-dead-code-removal/task-7-report.md`. **Step 3 (`A1.1, A1.5, A1.7, A1.12–A1.15, C0.4, C0.7, C0.8`) is unblocked** — A1.11 dropped from that set 2026-08-06, closed by D17's deletion of `watch`. |
| **3** | **A1.1, A1.5, A1.7, A1.12–A1.15, C0.4, C0.7, C0.8** — correctness bugs + ruff + dep hygiene (A1.11 dropped 2026-08-06: closed by D17's deletion of `watch`, so there is nothing left to fix) | 2 | Cheap, mechanical, unblocks CI — **STEP 3 COMPLETE 2026-08-07 (with a recorded gap — see below).** Rows closed across Tasks 1–7: `A1.1`, `A1.5`, `A1.7`, `A1.12`, `A1.13`, `A1.14`, `A1.15`, `A2.11`, `A5.1`, `C0.4`, `C0.7`, `C0.8`, and Task 1's allocated IDs `A2.16` (unused runtime deps), `A1.22` (`.env.example` temperature drift), `A1.23` (`.env.example` model-below-32B drift) — i.e. the brief's `<N1>`–`<N3>`. `A1.24` (the brief's `<N4>`, `looks_like_path`'s single-word-prose gap) stays **PENDING as an accepted risk**, deliberately not closed here — deferred to `C6.6`'s deterministic completion gate. `A2.11` and `A5.1` are pulled into this list from outside this step's originally-listed range (`A1.1, A1.5, A1.7, A1.12–A1.15, C0.4, C0.7, C0.8`) because their own row text self-directs here: `A2.11`'s row says "drop it with the rest of the dependency hygiene work (C0.7)"; `A5.1`'s row says "fix belongs with A1.5/A1.15." `A5.1` closed on the **`load_dotenv` half only** — `config.py`'s `Config.load()` now resolves `.env` from `Path.cwd()` explicitly rather than walking upward from the module's own location, with `override=False` retained. The second leg (validating `num_predict > 0` in `OllamaConfig`) was **declined, not deferred** — `-1` is a legal value for plain local Ollama ("unbounded") and only Ollama's cloud passthrough rejects it, so value-legality is provider-specific and belongs with the Step 5 model factory (`C1.4`), the first place in the codebase that knows which provider it's talking to. `C0.8` closed **verified-no-edit** — `deepagents==0.7.4`'s exact pin and its explanatory comment already landed in Step 1 and were simply never marked DONE; Task 2 confirmed the text was already correct and touched nothing. Additional ledger item from a Task 2 reviewer, closed here: `A3.4` (ruff/pytest as runtime deps) — verified `[dependency-groups] dev` (`pyproject.toml:57-61`) already holds both, `[project] dependencies` holds neither. **Task 7 local verification (this task):** `.venv/bin/ruff check src/ tests/` → `All checks passed!`; `.venv/bin/ruff format --check src/ tests/` → `31 files already formatted`; `.venv/bin/pytest -q` → `62 passed in 0.57s`; `.venv/bin/rudra --version` → `Rudra v0.2.0`; `.venv/bin/rudra --help` → help text, no traceback. **Measured diff:** `git diff --stat 6956685..HEAD -- src/ tests/ pyproject.toml .env.example` → 25 files changed, 572 insertions(+), 195 deletions(-). **Step 3 (brief §Step 3, live smoke run) was NOT performed — Ollama is not installed on this machine** (`which ollama` → not found; `curl http://localhost:11434/api/tags` → no response), verified before dispatch, so no live model backend of any kind was reachable. **This leaves two pieces of evidence genuinely outstanding, not merely unverified in form:** (1) **A5.1's no-override acceptance evidence** — the brief's Step 3 required a bare `rudra` run from a tempdir, with no `OLLAMA_NUM_PREDICT=131072` export, to prove the fixed `load_dotenv(Path.cwd()/".env")` scoping (not the env-var workaround Steps 1/2's smoke runs both needed) is what makes `gemma4:31b-cloud` succeed. That inference-dependent proof did not run. (2) **The definitive dependency-drop proof for A2.16** — a full agent run imports far more of the dependency graph than `rudra --help` does, and was meant to be the conclusive check that none of the six dropped packages (`langchain-community`, `fastapi`, `uvicorn`, `tavily-python`, `duckduckgo-search`, `requests`) is needed transitively. **Two offline substitutes were run instead, and cover only what they can without inference:** (a) A throwaway script (`/private/tmp/.../scratchpad/construct_agents.py`, not committed) called the real `create_main_agent()` (`src/rudra/agent/main_agent.py:521`) and `create_coder_agent()` (`src/rudra/agent/coder_agent.py:44`) factory functions against a `tempfile.TemporaryDirectory()`, exercising the full deepagents/langchain/langgraph import graph and constructing real `ChatOllama` + compiled `CompiledStateGraph` objects for both planner and coder — with no model invoked (`ChatOllama.__init__` makes no network call; only `.invoke()`/`.stream()` would). Result: **PASS, no `ModuleNotFoundError`** — the import graph is intact end to end, which is positive evidence for A2.16 but not the "full agent run" proof the brief specified, since no graph execution happened. (b) From a `mktemp -d` directory outside the repo, `.venv/bin/python -c "..."` called the installed package's `get_config()` directly: `cfg.ollama.num_predict` → `131072` (the coded default, `config.py:22`), confirming no `.env` leaked in from outside the tempdir. This exercises the A5.1 `load_dotenv` fix through the real installed package, but it is a config-resolution check, not an inference-succeeding-without-the-override check — it does not touch `ChatOllama` construction with real request parameters or an actual `/api/chat` call. **Net: (a) and (b) cover the import graph and config resolution; neither substitutes for real inference. A5.1's no-override acceptance and A2.16's definitive dependency-drop proof are both still open and must be the first thing run once a model backend (local Ollama, or cloud with credentials) is available — re-run the brief's Step 3 exactly as written, unmodified.** **Step 4 (`C0.5, C0.6, A3.1–A3.4`) is unblocked** by everything above regardless — CI setup and the pytest suite gate do not depend on live inference. |
| **4** | **C0.5, C0.6, A3.1–A3.4** — pytest suite + CI green | 3 | **Gate.** No further work without a regression net. **Note (final review, 2026-08-07):** `tests/test_package_version.py` compares `rudra.__version__` against installed distribution metadata, so this step's CI must `uv sync`/install the package before running pytest — an uninstalled source tree yields `0.0.0+unknown` and the test fails |

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
