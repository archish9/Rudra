# CLAUDE.md — Rudra

Context loaded into every fresh Claude Code session. Read `TODO.md` next — it is the live ledger of what is done and what is pending.

**Three ledgers, and they are not interchangeable.**

| File | Owns |
|---|---|
| `TODO.md` | **What is open now, and nothing else.** Read it first. It opens with the order to fix the open items in, and carries everything a fresh session needs to reproduce them — baseline, how to configure a throwaway test project, model availability, where the logs are |
| `TODO-closed.md` | Every `DONE` and `WONTFIX` item — OPEN-1…OPEN-33 and the 75 `CR-*` code-review findings of 2026-08-21, with their reproductions. Split out 2026-08-25; OPEN-23…OPEN-33 moved here 2026-08-26 |
| `TODO-old.md` | The Steps 0–16 build ledger: **§0 (locked decisions D1–D19), §0.1 (middleware disposition), §E (execution order), §F (deepagents 0.7.4 findings)** and every `A*`/`C*`/`S*`/`U*` item this file cites |

When a reference below says §0, §F, U.7 or A1.46, it means `TODO-old.md`.
When it names an `OPEN-N` that is not in `TODO.md`, it means `TODO-closed.md`.

**All three are gitignored** (`.gitignore` ignores `TODO*`), so git holds no
copy of any of them. Move content between them; never delete it. That is
also why §2 rule 6 can only mean *alongside* the code commit, not in it.

Corrected 2026-08-21 (CR-DOC1): this file used to send every session to
`TODO.md` §E for "which step to do next", and that section has not been in
`TODO.md` since the ledger was replaced.

---

## 1. What Rudra Is

Rudra (रुद्र) is an **autonomous coding agent CLI** — a local-first alternative to Claude Code. It plans work, writes code, tests it, reviews it, and fixes it, while the user stays in control.

- **Language:** Python 3.12+
- **Agent framework:** [LangChain `deepagents`](https://github.com/langchain-ai/deepagents) — **`0.7.4`, installed and pinned exactly** (`pyproject.toml:30`), because `compat/deepagents_path.py` monkeypatches internals and `middleware/task_anchor.py` imports a private module. The upgrade from 0.4.12 is **done**; `TODO-old.md` §F records what changed. Corrected 2026-08-21 (CR-F3) — this line claimed 0.4.12 was installed and the upgrade was pending, and it is read by every session.
- **CLI:** Typer + Rich + prompt_toolkit
- **Package:** `rudra`, entry point `rudra.cli:app` (`pyproject.toml:62`)
- **License:** Apache-2.0 declared in `pyproject.toml:10`; full text on disk at `LICENSE` (added 2026-08-10, A4.2). `NOTICE` landed with Step 11a (A4.7), generated from the bundle registry; `CONTRIBUTING.md` and `SECURITY.md` landed with Step 16
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
8. **Runs on Windows, macOS and Linux.** Owner requirement, stated 2026-08-21
   and non-negotiable. Portability is a correctness property, not a
   nice-to-have, and the way it is kept is *structural* rather than tested:
   path handling resolves by **shape** rather than host OS
   (`compat/virtual_paths.py` — `C:\...`, UNC, `\x` and `/x` all resolve
   the same everywhere), process control picks its spelling by **capability**
   rather than `sys.platform` (`shell/runner.py` asks whether
   `CREATE_NEW_PROCESS_GROUP` exists, which is the same question as asking
   the OS), and `.gitattributes` pins line endings because the vendored skill
   corpus is verified by hashing raw bytes.

   **Write new code the same way: branch on capability or on the shape of
   the input, never on `sys.platform`.** A capability check is testable on
   every machine — the Windows branch of `shell/runner.py` is exercised on
   macOS by deleting `os.killpg` — and a `sys.platform` branch is not.

   **The honest limit:** the suite has only ever been executed on macOS, and
   there is **no CI and none is planned** (the owner declined it on
   2026-08-21; `CONTRIBUTING.md` accepts no pull requests, so a PR-triggered
   gate would guard nothing). Portability here is held by construction and by
   simulation, not by execution on three platforms — see `TODO.md` CR-X3.

---

## 2. Session Rules (for Claude Code, not for Rudra)

1. **Read `TODO.md` at the start of every session** — it holds only what is open, and opens with the order to work it in. For a closed item's reproduction read `TODO-closed.md`; for historical decisions and step order read `TODO-old.md` (§0 = locked decisions, §E = execution order, §F = deepagents 0.7.4 findings). See the table at the top of this file about which ledger owns what.
2. **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with file:line evidence first. Then fix it. Then mark `DONE`. This ordering is non-negotiable — the owner asked for it explicitly.
3. **Evidence-based only.** Every claim about the codebase must cite `file.py:line`. No assumptions, no guessing. If you cannot verify, say so.
4. **Verify before claiming done.** Run the command, show the output. `ruff check`, `pytest`, actual CLI invocation.
5. **Planning sessions produce self-contained implementation plans** that a fresh session can execute without this conversation's context. Write them under `docs/superpowers/plans/`, which is where the existing ones live — `plans/` was named here for a directory that has never existed (CR-DOC6).
6. When done with a work item, update `TODO.md` in the same commit as the code.

---

## 3. Current Architecture (through Step 16, plus the 2026-08-21 review)

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
├── loop/                   The agentic loop (Step 9c). ledger · bounds ·
│                           regressions · tools · engine. ledger.py,
│                           bounds.py and regressions.py import nothing from
│                           Rudra. regressions.py answers the loop's hardest
│                           question — which failures a task answers for
│                           (OPEN-23) — and is worth reading without a graph. No agent-facing tool can write DONE — only
│                           engine.py, and only on VerifyReport.passed or a
│                           failure that predates the task (regressions.py)
├── memory/                 Long-term memory (Steps 14a/14b). store.py is the
│                           ONLY module that imports mempalace, and imports it
│                           lazily -- chromadb pulls onnxruntime, grpcio and
│                           opentelemetry, which at module scope would land on
│                           `rudra --version`. entry/taxonomy/degrade import
│                           nothing from Rudra. Every mempalace call passes
│                           palace_path, collection_name and backend
│                           explicitly, because two of the three cannot be
│                           overridden by env at all (C8.1b). render.py builds
│                           the recall block; the write spine lives in
│                           loop/engine.py beside the AGENTS.md writer, which
│                           is what makes C8.9 structural (Step 14b). export.py
│                           owns BOTH halves of the round trip because
│                           mempalace's exporter resolves collection and
│                           backend from the user's global config (A1.87);
│                           prefetch.py warms chromadb's ONNX MiniLM through
│                           mempalace's own code path (Step 14c)
├── cli_repl.py             The REPL's input layer (Step 15b): history,
│                           multiline, `/` and `@` completion, mention
│                           expansion. Every function is of (text,
│                           project_path) and needs no terminal, which is
│                           what makes it testable — the REPL had no tests
│                           at all before. REPL_COMMANDS is ONE table, read
│                           by both `_print_help` and the completer
├── trace/                  The run trace (Step 15a). events · render · stream ·
│                           sink · debug. Imports nothing from agent/, loop/ or
│                           subagents/ — pinned by tests/test_trace_wiring.py.
│                           ONE event vocabulary with three consumers: the
│                           console, the always-on JSONL run log, and the
│                           transcript. stream.py keys its position by subgraph
│                           NAMESPACE, which is what closed A1.20 — both stream
│                           loops kept one counter across namespaces and
│                           silently dropped messages. render.py escapes every
│                           payload (A1.67, A1.48, A1.91): model and config text
│                           printed raw through Rich loses anything in brackets
├── agent/
│   ├── main_agent.py       RudraAgent — run setup; run() delegates to run_loop,
│   │                       build_backend() → CompositeBackend(default=LocalShellBackend)
│   └── planner_agent.py    THREE staged agents (Step 10b): clarify · architect ·
│                           breakdown, one tool set each via _tools_for_stage.
│                           consult_planner + the lifted _stream_planner_turn.
│                           Indexes NO skills (OPEN-17): the corpus is written
│                           for ONE agent that clarifies, designs and builds in
│                           one conversation, so a stage that read it tried to
│                           run all four of its steps holding the tools for
│                           one — emitting "write a design doc" as a TASK. Its
│                           methodology is merged into the three stage bodies
│                           instead, so it applies to every run on every model
│                           rather than when a model opens a file
├── middleware/             2 survivors of D4, both opt-in behind [compat],
│                           plus repeat_guard.py (OPEN-10, always on): an
│                           agent may not re-run a READ that already failed
│                           twice with identical args and nothing in between.
│                           execute is excluded — a command can legitimately
│                           succeed on retry, and guarding it would turn a
│                           token cost into a correctness bug
├── tools/                  EVERY tool the model can call, and nothing else:
│                           interaction (record_fact · ask_user) · git_tools ·
│                           testing_tools · memory_tools (remember ·
│                           search_memory). `remember` is CONTROL_PLANE, not
│                           MUTATING: it writes under .rudra/ only, and
│                           outside that set the gate would deny it on every
│                           call (A1.75).
│                           The last two are thin wrappers over git/ and
│                           testing/, whose APIs the orchestrator calls directly
├── filesystem/             capped project_tree() — VFS deleted in Step 2 (D7)
├── state/                  paths.py (D15 layout), session id (unused).
│                           ProjectConfigManager died with C6.8a
├── mcp/                    MCP client + config (Step 13). `.mcp.json` in
│                           Claude Code's schema, so an existing config
│                           pastes in unchanged; three meta-tools rather
│                           than one tool per server, so a server's whole
│                           surface is not a fixed prompt cost
├── skills/                 The vendored superpowers corpus and its delivery
│                           (Step 11/11a): bundle · registry · manifest ·
│                           transform · validate · cache · sources · notice ·
│                           rudra_tools. Rendered into a user-level cache and
│                           reached through a CompositeBackend route, because
│                           skills live outside the project root while the
│                           backend is rooted at it (D13). Consumers are the
│                           CODER and TESTER only — the planner indexes none.
│                           transform.py rewrites the rendered copy three ways,
│                           and NOTICE names all three: cross-refs → readable
│                           paths, Rudra's reference file added, and two
│                           frontmatter `description`s replaced. That third one
│                           is not cosmetic — deepagents copies `description`
│                           verbatim into every indexing agent's system prompt
│                           (middleware/skills.py:870), so upstream's "You MUST
│                           use this before any creative work" was an unasked-for
│                           order, not documentation (OPEN-17). Bodies are never
│                           touched, and a test holds that line
└── compat/                 monkeypatches + version guard into deepagents
                            internals, and virtual_paths.py — the ONE
                            function that says which real file a
                            model-written path names, shared by the gate,
                            the approval preview and the backend so they
                            cannot disagree (CR-B4). Cross-platform by
                            shape, not by host OS: `C:\...`, UNC, `\x`
                            and `/x` all resolve on every platform
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

**The approval prompt has five answers, and the fifth is a grant, not a
mode (OPEN-30).** `a` approves one call, `r` rejects, `A` grants one rule
(`suggest_grant` — one path, or one command's first word), `!` **auto-accept**
stops asking for the rest of the session, `d` shows the full diff and asks
again. `!` sets `SessionGrants.approve_all`, which `decide` reads at the
*session grants* step — so it is third in the precedence list above, and the
deny floor and `permissions.deny` have already returned by the time it is
consulted. `git-dir`, `catastrophic-command` and every user deny rule still
refuse after `!`; every call is still audited, with `source:
"session-grant-all"`.

Setting `engine.mode = "auto"` would have been the obvious implementation
and is wrong: the auto branch denies `execute` unless `shell_in_auto` and
`call_mcp_tool` unless `mcp_in_auto` (`rules.py`), so the prompt the user
just answered would become a *denial* on the next command.

**Session grants outlive the run.** `build_gate` and `create_main_agent`
both take an optional `grants=`, and `_repl_session` builds ONE
`SessionGrants` before its loop — the REPL builds a fresh agent per input
(S15.4), so without this `always` meant "always, until you press enter".
Single-shot passes nothing and gets its own. The REPL's task panel reads it
too: `_permission_notice(cfg, grants)` appends `auto-accept on for this
session`, because a panel still promising "prompting before each write"
after `!` is a lie printed once per turn.

### Control flow (`loop/engine.py`)
1. **Planning runs in three stages before any code** (Step 10b, C6.7): `clarify` settles the facts, `architect` records layout and boundaries, `breakdown` calls `add_tasks` with units of **work**, not filenames. Each stage is a separate agent with its own tool set — clarify cannot `add_tasks`, breakdown cannot `ask_user` or `record_fact` — so a stage cannot do another stage's job. The fact store is the only channel between them, and no stage has a tool that can mark anything done.

   **No planner stage indexes a skill, and that is load-bearing (OPEN-17).**
   The three stages *are* a planning methodology, implemented in Python. The
   superpowers corpus is a planning methodology written for **one** agent
   that clarifies, designs and implements in a single conversation. Handed
   both, every stage ran the corpus's version: measured four times on a 550B
   model, the planner emitted `Ask clarifying questions`, `Propose 2-3
   architectural approaches`, `Write design doc to docs/superpowers/specs/`
   and `Invoke writing-plans skill` **as the task list**, or declared no
   tasks at all.

   **Three fixes written as prompt text all failed**, because the
   `using-superpowers` bootstrap was injected into the same prompt saying
   `IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE
   IT.` — *a prompt cannot outrank a prompt.* Do not retry that; the ledger
   records the three attempts so a future session does not.

   What worked is `_tools_for_stage`'s own argument applied to skills:
   **absence is the enforcement.** The methodology was merged into the stage
   bodies rather than discarded — clarify gained scope assessment and
   purpose/constraints/success-criteria, architect gained
   weigh-2-3-approaches-and-record-why-the-others-lost, YAGNI and the
   isolation test, breakdown gained the spec self-review retargeted to the
   ledger. It now applies to every planning run on every model instead of
   when a model chooses to open a file. Attribution is in
   `planner_agent.py`'s docstring and in `NOTICE`; the coder and tester still
   index the full corpus, being single agents doing one job.
2. **The plan is presented and approved** (Step 10c, C6.9): facts with their source, then the tasks. In `ask` mode the user approves, revises — which re-enters `breakdown` with their words **verbatim**, up to `MAX_REVISIONS` (3) times — or cancels. `--plan` presents and stops. `--auto` skips the gate. **EOF and Ctrl-C are cancel, never approve.** The gate lives in `RudraAgent`, between `plan()` and `work()`; the loop does not know what a terminal is.
3. `work()` takes the next pending task and calls `run_task`.
4. `run_task`: coder subagent writes → `verify_project` gates → on failure the blocker goes back **verbatim** and it retries, up to `[agent] max_fix_attempts`.
5. Success = **`VerifyReport.passed`, or a failing gate whose every located
   failure was already failing before this task ran** (OPEN-23,
   `loop/regressions.py`). Only `engine.py` writes `DONE`, and only there —
   D9 is untouched, because Python still decides, not a model.

   The second clause exists because the gate is project-wide and the coder is
   task-scoped, and before 2026-08-26 the composition of the two deadlocked:
   a test written by an earlier task's tester against code a *later* task
   would build failed the whole suite, came back verbatim to a coder not
   allowed to touch it, and blocked six consecutive tasks until
   `MAX_BLOCKED_CONSULTS` ended the run — with the task that would have
   fixed it among the never-attempted (run `ee29dd3ebf51`).

   **It is a time comparison, never a file comparison.** "Is this finding in
   a file my task touched" is the obvious rule and it is wrong: in that same
   run `tests/test_cli.py` failed *through* a change to `todo/storage.py`, so
   ownership-by-filename would have passed a real regression. A **new**
   failure blocks the task whether or not it touched the file, which keeps
   "a task that breaks a sibling's tests must not pass" strictly.

   The baseline is per-run and in memory (`LoopContext.failure_baseline`),
   never persisted: a fresh process — the first task, or `--continue` — has
   none, every failure reads as new, and the loop behaves exactly as it did
   before. The degraded mode is the old blocking one, never the passing one.
6. Two identical failure signatures in a row → `BLOCKED` (C6.5a). A gate `escalate` → the whole run stops.
7. The planner is consulted again **only** on a block or an empty ledger — never after an ordinary success, and only the `breakdown` stage is re-entered (S10b.3). Clarify and architect run once: re-opening the questions after code exists churns decisions the coder already built on.
8. Reviewer runs once at the end, advisory, printed, gating nothing.

**The split that makes this work (S9c.1):** the model decides what work exists;
Python decides when a task is done and when to stop. `C6.1` wanted an agent that
owns the todo list, D9 forbids an LLM deciding termination — both hold because
the ledger tools *cannot express* `DONE`, not because a prompt asks nicely.

Two edges worth knowing before touching it: an **empty diff is a failed attempt**
(with no changed files the gate reports "0 files parsed" and would pass an
untouched task), and `files_touched` comes from the **filesystem**, not from the
model — `git status` where there is a repo, a fingerprinted `source_files` walk
where there is not. Corrected 2026-08-24 (OPEN-12/OPEN-13): this said "from
**git**", full stop, and the code agreed — `git_snapshot` returned `None`
outside a repo and the empty-diff guard was written `before is not None and
not files_touched`, so in a project with no `.git` the guard above never ran
and two tasks were marked `DONE` having created no file. `attempt_snapshot`
is now the single entry point and never returns `None`.

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
| `run/repl_history` | volatile | `cli_repl.build_session` | prompt_toolkit | Per project, because a Rust project's prompts are not a Python project's. A read-only project loses history, never the REPL (Step 15b) |
| `run/transcripts/<id>.jsonl` | volatile | `trace/transcript.py`, every run | humans, `rudra log` | One JSON object per `TraceEvent`, appended and flushed per event so a killed run still leaves a readable record. Payloads capped at 2000 chars, newest 20 runs kept. **Written by default**, which is safe only because redaction happens where the event is built (A1.95) — moving redaction into the renderer would silently refill this file with credentials (Step 15c, C9.5) |
| `run/logs/debug-<id>.jsonl` | volatile | `trace/debug.py`, **every run** | humans, bug reports | One JSON object per line: every `TraceEvent` plus every `rudra.*` log record and traceback. **The complete record** — registered with `TraceSink.add_recorder`, so unlike the transcript no trace level filters it and payloads are uncapped. One file per run, newest 20 kept by `prune_debug_logs`, which globs `debug-*` so it cannot eat `permissions.jsonl`. `[agent] debug_log = false` or `--no-debug` turns it off. Corrected 2026-08-24 (OPEN-7): was one appending `debug.jsonl` written only under `--debug`, and registered as an ordinary consumer, so `--no-verbose` cut it down to errors. An unopenable file disables the log rather than failing the run (Step 15a, C9.7) |
| `run/logs/usage.json` | volatile | `write_usage_log` | humans | Per-role tokens and compactions for the last run. Written at run end, never mid-run, and a write failure is swallowed — a finished run must not be reported failed over bookkeeping (C7.5) |
| `run/artifacts/` | volatile | deepagents eviction + summarization | the agent, via the `/artifacts/` route | Kept out of the project by `artifacts_root` (A1.45) |
| `memory/export/` | durable | `rudra memory export` | `rudra memory import` | The palace is binary and churns, so the markdown export is the portable copy. `exporter.export_palace` is **not** used — it resolves collection and backend from the user's global config (**A1.87**) |
| `memory/palace/` | volatile | `rudra.memory.store` | `rudra.memory.store` | ChromaDB, project-scoped per D14/S14.5. Opened only through `MemoryStore`; a failure degrades loudly and never fails a task (C8.6) |

Agent-facing prompts used to name these paths as literal strings, and a path
that moved without its prompt meant the coder wrote where nothing reads. Step 9c
removed the hazard rather than guarding it: the ledger is reached only through
tools, so **no prompt names a state path at all**.
`tests/test_rudra_dir_migration.py` now asserts that absence.

---

## 4. What deepagents Already Provides (and Rudra ignores)

Table below is verified against **installed 0.7.4**. The 0.4.12 → 0.7.4 delta — new `permissions=`, `RubricMiddleware`, provider/harness profiles, `TodoListMiddleware` no longer auto-added, `write()` now overwrites — is recorded in `TODO-old.md` §F. `create_deep_agent()` accepts:

| Param | What it gives | Rudra uses it? |
|---|---|---|
| `model: str \| BaseChatModel` | `provider:model` resolved via `init_chat_model` (`_models.py:11`) | ⚠️ passes a `BaseChatModel` instance from `llm/factory.py` (Step 5) |
| `skills: list[str]` | `SkillsMiddleware` — Anthropic Agent Skills spec, `<dir>/SKILL.md` + YAML frontmatter | ⚠️ **coder and tester only** (`subagents/build.py::_skills_for`, `registry.py:162,177`). The planner passes `skills=None` — corrected 2026-08-25 (OPEN-17), where this cited `planner_agent.py:463` as a live call site. Corrected 2026-08-21 (CR-DOC4) — before that it read "never used" |
| `subagents: list[SubAgent]` | `SubAgentMiddleware` + the `task` tool | ✅ Step 9b: four specs in `subagents/registry.py`. **Rudra ships its own `general-purpose` to suppress the ungated one deepagents auto-adds** (`graph.py:751`). Passed at `agent/main_agent.py:687`. Corrected 2026-08-21 (CR-DOC5) — this read "nothing passes `subagents=` yet", which stopped being true when 9c shipped |
| `memory: list[str]` | `MemoryMiddleware`, AGENTS.md into system prompt | ⚠️ planner only |
| `permissions: list[FilesystemPermission]` | `allow` / `deny` / `interrupt` path rules | ❌ **cannot be used** — raises on any execute-capable backend (U.7) |
| `interrupt_on: dict` | `HumanInTheLoopMiddleware` — approval gates | ✅ Step 7: one entry per mutating tool, `when` calling Rudra's `PermissionEngine` |
| `backend` | `FilesystemBackend` / `LocalShellBackend` / `CompositeBackend` | ✅ Step 7: `CompositeBackend(default=LocalShellBackend)` |
| (automatic) | `create_summarization_middleware` — offloads history to `/conversation_history/{thread_id}.md` | ✅ inherited, not designed |
| (automatic) | `TodoListMiddleware`, `PatchToolCallsMiddleware` | ✅ inherited |

**Critical (settled in Step 7):** `execute` only works on a backend implementing `SandboxBackendProtocol`. `LocalShellBackend` does; plain `FilesystemBackend` does not — the tool is still *registered* in the default stack, and returns `"Error: Execution not available. This agent's backend does not support command execution (SandboxBackendProtocol)."` when called. Measured through a real graph run, closing **U.17**: registered ≠ functional, and `CLAUDE.md`'s original claim was right.

**Since Step 7 Rudra ships `CompositeBackend(default=LocalShellBackend(...))`, so `execute` works** and agents can run tests, linters, builds, and git. The composite also carries an `/artifacts/` route with `artifacts_root="/artifacts"`, because that root otherwise defaults to the backend root and deepagents would write `large_tool_results/` and `conversation_history/` into the user's project (**A1.45**).

**`permissions=` is deliberately never passed.** It raises `NotImplementedError` on any execute-capable backend, and its `FilesystemOperation` is `('read','write')` only, so it never covered `execute` regardless. Rudra's own gate in `src/rudra/permissions/` does the whole job. See `TODO-old.md` **U.7** and **A1.46**; `tests/test_deepagents_contract.py::test_permissions_still_rejected_with_execute_backend` is the trigger that reopens U.7 if upstream lifts the restriction.

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
| Recalled memories in every prompt | **designed** | `memory/render.py` + `recall_limit`. **One number now feeds three consumers** — summarization, eviction, and recall. `usage.json` carries `recall_chars` per role so the fraction can be revised with evidence (Step 14b) |
| Per-task memory in `AGENTS.md` | **designed** | `src/rudra/context/agents_md.py` + the two writers in `loop/engine.py`. Session Log capped at 20 entries (C7.3) |
| Per-role and per-task wall clock | **designed** | `UsageMiddleware` times every model call; `run_task` times every task (Step 15a, C9.6). **No cost figure, ever — S15.2.** Latency is the one number a local backend always has: token counts are frequently `not reported`, and a failed call still costs the wait |
| The run trace | **designed** | `src/rudra/trace/`. Before Step 15a the subagents printed nothing at all — `runner.py` consumed every chunk to drive its guards and rendered none — so a run went quiet exactly while the coder worked. `--verbose` reached nothing (A1.90) |

**One number, two consumers.** `[model.<role>] context_tokens` feeds both
the summarization trigger and the eviction threshold. That is deliberate:
two independently configured numbers would drift, and the pair only makes
sense read together.

**The standing gap, stated rather than implied: nothing measures system
prompt growth.** Skills, facts and the merged planning methodology are a
fixed cost paid on every call, and no mechanism here trims them.
Summarization compacts the *conversation*; the prompt is rebuilt in full
each time. The planner's share of that got *smaller* on 2026-08-25: dropping
the skills index also dropped `SkillsMiddleware`'s boilerplate and the
~780-token `using-superpowers` bootstrap, which more than pays for the
methodology now inlined in the three stage bodies (OPEN-17).

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

Owner decisions are recorded in `TODO-old.md` §0. Summary: TOML config, vendored superpowers, MemPalace Python API, permission mode `ask` by default with an `--auto` escape hatch, minimum local model 32B, `VirtualFileSystem` deleted.

**Layered TOML, later layers override earlier:**
1. Built-in defaults (shipped in package)
2. `~/.config/rudra/config.toml` (user global)
3. `<project>/.rudra/config.toml` (project)
4. Environment variables (`RUDRA_*`)
5. CLI flags

**Every section is live:** `[model.*]`, `[agent]`, `[permissions]`,
`[compat]`, `[tools]`, `[skills]`, `[mcp]`, `[memory]`. Nothing is reserved
any more — `RESERVED_SECTIONS` is `{}`. Corrected 2026-08-21 (CR-DOC2): this
said `[skills]` and `[memory]` were reserved and writing one was a hard
error, which stopped being true when Steps 11 and 14 shipped. MCP *servers*
still live in `.mcp.json` (Step 13); the `[mcp]` config section is about
which of them Rudra will use. Unknown keys are fatal and suggest the nearest
valid name, because the common case is a typo.

`[tools]` carries `shell`, `shell_in_auto`, `auto_branch` and `test_timeout`
— corrected 2026-08-21 (CR-DOC3), which said "`shell` and nothing else" a
dozen lines above a block listing all four. The oversized-tool-result
threshold that would naturally sit beside them is unreachable through
`create_deep_agent` (**A1.47**), and an inert config key is worse than no
key.

`rudra init` scaffolds the file; `rudra config list` shows every effective
value and the layer that set it. There is no `rudra config set` — see
TODO-old.md S6.1.

```toml
# .rudra/config.toml
[model.planner]
provider  = "openai_compatible"        # ollama | openai_compatible | anthropic | google | openai
base_url  = "http://localhost:8000/v1"
model     = "qwen3-30b"
api_key_env = "RUDRA_PLANNER_KEY"      # name of an env var; or api_key = "..." for the key
temperature = 0.3

[model.coder]
provider = "ollama"
base_url = "http://localhost:11434"
model    = "qwen3-coder:30b"

[agent]
verbose = false                         # prose + untruncated payloads in the trace (Step 15a)
stream_tokens = false                   # stream that prose token by token; --stream for one run
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
# A pattern is matched against THREE spellings of the path -- the model's
# own, the project-relative one, and the resolved host one -- so a rule
# fires however the model wrote it. For execute it matches each shell
# segment of the command. deny beats allow.
#
# Paths are VIRTUAL (CR-B4). Every backend is virtual_mode=True, so the
# model's "/src/app.py" is <project>/src/app.py and its "C:\x\y.py" is
# <project>/x/y.py -- the host's files are never reachable through the file
# tools, on any OS. `compat/virtual_paths.py` is the single function that
# answers this, and the gate, the approval preview and the backend all use
# it, so they cannot disagree about which file a call touches.
allow = ["execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *", "write_file:.env"]

# Built-in rules denied in EVERY mode, including --auto. Name one to switch
# it off; the run says so, and calls it would have blocked are still audited.
#   git-dir · catastrophic-command
# ("outside-root" is rejected here: the backend confines writes, not this
#  rule, so disabling it would change nothing — see A1.50. Since CR-B4 it
#  fires only on a REAL escape — `../..` traversal, or a symlink inside the
#  project pointing out — never on a virtual absolute path, because those
#  do not leave the project in the first place)
floor_disable = []

# Reserved, not yet accepted — listed to show where they will go:
#   [skills] Step 11    [memory] Step 14
```

**MCP servers in a separate `.mcp.json`, Claude-Code-compatible schema**, so users reuse existing configs verbatim:
```json
{ "mcpServers": { "mempalace": { "command": "mempalace", "args": ["mcp"] } } }
```
Loaded via `langchain-mcp-adapters` → tools handed to `create_deep_agent(tools=[...])`.

**API keys may be written in TOML, and are never displayed.** Corrected
2026-08-24 (OPEN-6): this read *"API keys never in TOML"*, which made
`api_key_env` — a field holding a variable *name* — the only route, and
pushed every user toward a project `.env`: a per-project file for a
per-machine secret. `ModelConfig` now carries a literal `api_key`, checked
by `_resolve_api_key` **before** `api_key_env` (`llm/factory.py:50`).

The rule that replaced it is narrower and holds in both directions: a key
may be **stored**, and is never **shown**. Shown-ness is structural, not a
convention — `api_key` is declared `field(repr=False)`, so it cannot reach
a repr, log line or traceback frame; `cli.py::_safe_value` masks it in
`config list` with no inspection at all.

**A user's project is configured by `.rudra/config.toml`, not by a `.env`.**
`rudra init` writes that file and no `.env`; it carries provider, model,
`base_url` and either `api_key` or `api_key_env`, which is everything a
`.env` would have carried. `.env` remains a supported layer-4 input — this
repo's own dev config is one — but nothing asks a user to create one, and
`rudra init` never does. See §9 for why that distinction has already cost a
session once.

**Which file matters, and it is the only thing that does.**
`~/.config/rudra/config.toml` is a home-directory file in nobody's
repository (the `~/.aws/credentials` shape) — a key there is fine.
`<project>/.rudra/config.toml` is the file `README.md` tells users to
commit, so `committed_api_key_notice` (`config/loader.py`) warns once per
run when provenance says the key came from that layer. A warning and not a
refusal: it may be a private repo or a throwaway key, and overruling the
user is what OPEN-6 was filed against.

---

## 7. Hard-Won Knowledge (don't relearn)

- `install_path_normalizer` (`compat/deepagents_path.py`) monkeypatches `validate_path` in **three** modules — `deepagents.backends.utils`, `deepagents.middleware.filesystem`, and `deepagents.middleware._fs_interrupt` — because each does `from ... import validate_path` and resolves the name in its own globals, so patching one is not enough. Corrected 2026-08-21 (CR-D8): this said two, and the module docstring claimed a grep had confirmed there were only two. `_fs_interrupt` is reached only when `permissions=` is passed, which Rudra never does (U.7), so patching it changes nothing today — it is patched so that U.7 does not reopen onto a half-applied file. `pyproject.toml:30` pins `deepagents==0.7.4` **exactly**, for this reason.
- `OverwriteFilesystemBackend` **no longer exists** — `ls src/rudra/compat/` is `__init__.py`, `deepagents_path.py`, `path_constants.py`, `version_guard.py`. It was deleted with the 0.7.4 upgrade, whose `write()` overwrites by default (`backends/filesystem.py:489`); 0.4.12's refusal to overwrite was what sent small local models into a `write → error → edit no-op → write` loop. Its markdown-fence stripping lives on in `middleware/fix_write_params.py`. Corrected 2026-08-21 (CR-F4) — this bullet described the deleted module as live.
- The 6 middlewares are all patches for qwen3:14b failure modes documented in their own docstrings. Minimum target model is now **32B** → 4 of the 6 were deleted in Step 2, and the 2 survivors are **opt-in behind `[compat]`, default off, as of Step 6** (C1.8). Disposition table: `TODO-old.md` §0.1. `FixWriteParamsMiddleware` is split rather than gated wholesale: fence-stripping and `filename`/`path` → `file_path` aliasing are **always on** (required since U.3 — 0.7.4's `write()` no longer strips), while sandbox-prefix stripping sits behind `[compat] sandbox_paths` because `/src/` and `/tmp/` are sandbox prefixes *and* ordinary absolute directories.
- **Configuration precedence lives in exactly one function**, `config/loader.py::deep_merge`, and every value records the layer that set it. This shape was chosen because A5.1 and A5.2 were both "which source won?" defects that a per-field `x or y or default` chain structurally cannot answer. Do not reintroduce per-field resolution.
- **Role inheritance runs after the cross-layer merge**, not inside a layer. Inside a layer, a project-level `[model.planner]` would fail to inherit a user-level `[model.default]`.
- Retired planning material lives in `docs/archive/`, which is **gitignored** — if you cannot find these files, that is why, not because they were deleted (Step 16, `S16.6`). `road-map.md` argues against trajectory fine-tuning and for planning-data fine-tuning; `improvements.txt` and `context_management_implementation_plan.md` are prior planning docs, partially implemented.

---

## 8. Commands

**`Documentation/07-development.md` owns the development workflow** — setup,
running the tests, the pre-push gate, the layout, the house rules.
`CONTRIBUTING.md` deliberately does not repeat it, for a stated reason: *"One
fact, one owner — a second copy would drift, which this project has a ledger
full of examples of."* This section is the agent-facing cheat sheet for
*driving Rudra*; when it and `Documentation/07-development.md` disagree about
the workflow, that file wins. Corrected 2026-08-21 (CR-DOC9) — the test count
below had already drifted twice, which is that warning coming true.

**Contributions: pull requests are not accepted** (`CONTRIBUTING.md`). Bug
reports and feature requests are welcome as issues; security problems go
privately via `SECURITY.md`, never an issue. Do not propose a PR-based
workflow, and do not add CI to gate one — the owner declined CI on
2026-08-21, and with no PRs there is nothing for a `pull_request` trigger to
guard.

```bash
.venv/bin/ruff check src/ tests/     # must print "All checks passed!" — absolute gate since Step 3
.venv/bin/ruff format --check src/ tests/
uv run pytest -q                     # must never go down; see Documentation/07-development.md
git config core.hooksPath .githooks  # once per clone: run all three gates on push (A3.7)
.venv/bin/rudra --version            # Rudra v0.2.0

.venv/bin/rudra init                 # scaffold .rudra/config.toml + the D15 layout
.venv/bin/rudra config list          # every effective value + which layer set it
.venv/bin/rudra doctor --offline     # diagnose config, layout, deps; --offline skips network
.venv/bin/rudra models test          # verify each role is reachable and can call tools
.venv/bin/rudra memory list          # what this project has learned, and who recorded it
.venv/bin/rudra memory search "..."  # by meaning; `forget` prunes, `export` keeps
.venv/bin/rudra verify               # deterministic gate over the changed files (Step 9a)
.venv/bin/rudra verify --all --json  # whole project, machine-readable. Exit 0/1/2
.venv/bin/rudra "build a flask app"  # single-shot; prompts before each write and command
.venv/bin/rudra --auto "..."         # unattended: files yes, commands no (A1.49)
.venv/bin/rudra --verbose "..."      # ...and the model's prose, untruncated
.venv/bin/rudra --no-debug "..."     # skip .rudra/run/logs/debug-<id>.jsonl (on by default)
.venv/bin/rudra log --last           # replay a past run from its transcript
.venv/bin/rudra --auto --allow-shell "..."   # ...and commands too, opted in explicitly
.venv/bin/rudra --plan "..."         # show the plan and stop — writes nothing
.venv/bin/rudra                      # REPL
```

**Ctrl-C stops at a task boundary (Step 15b, C9.3).** SIGINT cancels the
run's asyncio task; `work()` catches `CancelledError` between tasks,
returns the in-flight task to `PENDING`, saves the ledger, and skips the
reviewer and the AGENTS.md summary because both are model calls. Single-shot
exits **130**. **`PENDING` and not `IN_PROGRESS` is the whole point:**
`Ledger.resumable()` excludes `IN_PROGRESS` deliberately
(`loop/ledger.py:91-100`), so the old behaviour left the interrupted task as
the one task `--continue` would never retry (A1.93). The cancel path and the
resume path are now the same path.

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

- The root noise this line used to list — `q-dev-chat-2026-03-20.md`, `filesystem-context.txt`, `improvements.txt`, `llms.txt`, `road-map.md`, `context_management_implementation_plan.md` — moved to the gitignored `docs/archive/` in Step 16 (A4.3, S16.6); `execution_tools.py` was deleted with D17 in Step 2.
- `.env` and `.rudra/` are gitignored (verified via `git check-ignore`).
- **Two different folders, and confusing them wastes a session. Read this
  before testing Rudra on anything.**

  | | **This repo** (`~/Documents/ai-ml/Rudra`) | **A user's project** (anywhere else) |
  |---|---|---|
  | What it is | Rudra's own source | The folder a user builds *their* app in |
  | Configured by | `.env` at the repo root | `rudra init` → `.rudra/config.toml` |
  | Holds creds? | **Yes** — the dev backend's keys | No. `config.toml` does this job |
  | Do we touch it? | **No.** Leave `.env` exactly as it is | This is what you test against |

  `.env` is Rudra's **development** config: it points this repo at a real
  provider so the maintainer can run against one. `config/loader.py:459`
  reads it as layer 4, and that is deliberate and staying. It is **not** how
  a user configures a project — `rudra init` writes `.rudra/config.toml`,
  which does the same job, and creates no `.env`.

  **So: never copy this repo's `.env` into a test project.** Configure the
  test project the way a user would — model settings in its own
  `.rudra/config.toml`, and `api_key_env` naming a variable you export into
  the environment. A `.env` in a test project is not a realistic setup, and
  reasoning from its presence produces false findings.

  That is not hypothetical. OPEN-19 was filed on 2026-08-25 claiming the
  planner leaks a project `.env` to the model provider — reasoned entirely
  from a `.env` the session itself had copied in. Re-tested on the real
  `rudra init` workflow, the planner reads `.mcp.json` and nothing else
  (run `292884ef16eb`). The item is `WONTFIX` and the correction is recorded
  there.
- ~~No LICENSE file, no CI, no CONTRIBUTING, no tests.~~ Stale as written, and stale again in the other direction. `LICENSE` landed with A4.2 and the suite is large and green — the number is not restated here, because it drifted twice in one day and `Documentation/07-development.md` owns it (CR-DOC8). **There is no CI, and none is planned**: A3.3 added `.github/workflows/ci.yml`, A3.9 narrowed it to `pull_request` only, S16.5 deleted it, and the owner declined re-adding it on 2026-08-21. That is consistent rather than an oversight — `CONTRIBUTING.md` does not accept pull requests, so a PR-triggered gate would guard nothing. `.githooks/pre-push` is the only gate; `--no-verify` or a clone that never set `core.hooksPath` is checked by nothing. `CONTRIBUTING.md` and `SECURITY.md` shipped in Step 16 (C10.4, C10.6).
- **A local `.venv` drifts from `uv.lock` and will lie to you.** Measured 2026-08-11 on unmodified `main`: `.venv/bin/pytest -q` → `14 failed, 502 passed`, against `520 passed, 2 skipped` on a clean `uv sync` clone; the venv was missing `langchain_openai`, which `pyproject.toml:47` requires. Two defects reached `main` under that noise (A3.5, A1.51). Run `uv sync` before believing a local failure, and prefer `uv run` — which reconciles first — when the answer matters.
