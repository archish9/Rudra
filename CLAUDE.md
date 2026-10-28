# CLAUDE.md — Rudra

Context loaded into every fresh Claude Code session. This file documents the
**architecture and the rules**. It is **not a ledger** — open work, closed
work, run history and bug reports live in the `TODO*` files, never here.

**Read `TODO.md` next** — it is the single live ledger: what is open and the
order to work it. It opens with a **Fix next** queue whose `Next:` line names the
item to work.

**`TODO.md` is the only ledger** (gitignored). A finished row is removed, not
struck; row numbers are permanent IDs. There is no separate closed or historical
ledger: a closed item's row is deleted, not moved. Older references elsewhere in
this file to `TODO-closed.md` or `TODO-old.md` (and to its §0 / §F / U.7 / A1.46
anchors) point at files that no longer exist.

**Four lessons that change how a session behaves:**

1. **Offline closure is evidence about the code, never about a model.** An
   empty board is not a finished project; it is the moment a live run is
   easiest to forget. A run only tests what the agent is given a reason to do.
2. **A plan document's recommended number is not evidence.** Check a plan's
   arithmetic against the archived run before implementing it.
3. **Check each plan against the tree anyway, even one that reads as final.**
   Re-verify the plan against the code before executing it.
4. **A comment asserting two things are the same needs a test, or it becomes
   the reason nobody checks.**

---

## 1. What Rudra Is

Rudra (रुद्र) is an **autonomous coding agent CLI** — a local-first alternative
to Claude Code. It plans work, writes code, tests it, reviews it, and fixes it,
while the user stays in control.

- **Language:** Python 3.12+
- **Agent framework:** [LangChain `deepagents`](https://github.com/langchain-ai/deepagents) — **`0.7.4`, installed and pinned exactly** (`pyproject.toml:30`), because `compat/deepagents_path.py` monkeypatches internals and `middleware/task_anchor.py` imports a private module. `TODO-old.md` §F records the 0.4.12 → 0.7.4 delta
- **CLI:** Typer + Rich + prompt_toolkit
- **Package:** `rudra`, entry point `rudra.cli:app` (`pyproject.toml:62`)
- **License:** Apache-2.0 declared in `pyproject.toml:10`; full text at `LICENSE`. `NOTICE` is generated from the bundle registry; `CONTRIBUTING.md` and `SECURITY.md` are present
- **Repo:** remote is `git@github.com:archish9/Rudra.git`, and `[project.urls]` matches it. `RudraAnvil` is the former name and survives only in dated `docs/superpowers/` records
- **Ships as:** open-source GitHub project

### Product goals (owner's stated requirements)
1. **Provider-agnostic.** User configures API URL + model + API key. Must work with Ollama, vLLM, OpenRouter, Anthropic, OpenAI, or anything else. **The main target is Vaayu, a local-first coding engine; hosted-provider rate limits are out of scope** — a hosted endpoint must work, but Rudra is not built to wait out its throttling. Run live verification against Vaayu; until it is ready, against a third-party endpoint.
2. **MCP + Tools + Skills.** Rudra ships with some; user can add their own.
3. **Loop engineering.** Code → test → review → fix, iteratively.
4. **Plan before coding.** Dynamic clarifying questions (no static Q&A scripts), tracked progress.
5. **Ship [obra/superpowers](https://github.com/obra/superpowers)** as a default, integrated methodology layer (MIT, Jesse Vincent).
6. **Ship [MemPalace](https://github.com/MemPalace/mempalace)** as default long-term memory (MIT; `pip install mempalace`; ChromaDB/SQLite/Milvus/Qdrant/pgvector backends; exposes 36 MCP tools).
7. **Local-first**, with Claude Code-grade capability for production work.
8. **Runs on Windows, macOS and Linux.** Non-negotiable owner requirement. Portability is a correctness property, kept *structurally* rather than tested:
   - path handling resolves by **shape** not host OS (`compat/virtual_paths.py` — `C:\...`, UNC, `\x` and `/x` all resolve the same everywhere)
   - process control picks its spelling by **capability** not `sys.platform` (`shell/runner.py` asks whether `CREATE_NEW_PROCESS_GROUP` exists)
   - `.gitattributes` pins line endings because the vendored skill corpus is verified by hashing raw bytes

   **Write new code the same way: branch on capability or on the shape of the
   input, never on `sys.platform`.** A capability check is testable on every
   machine (the Windows branch of `shell/runner.py` is exercised on other OSes
   by deleting `os.killpg`); a `sys.platform` branch is not.

   **The honest limit:** the suite has only ever run on macOS, and there is
   **no CI and none is planned** (`CONTRIBUTING.md` accepts no pull requests,
   so a PR-triggered gate would guard nothing). Portability is held by
   construction and simulation, not by execution on three platforms.

---

## 2. Session Rules (for Claude Code, not for Rudra)

1. **Read `TODO.md` at the start of every session** — it holds only what is open and the order to work it. Update its *Fix next* queue in the same edit as any filing, closing or live run; if anything disagrees with it, the queue wins. `TODO.md` is the only ledger: a closed item's row is removed, not moved to another file.
2. **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with file:line evidence first. Then fix it. Then mark `DONE`. Non-negotiable.
3. **Evidence-based only.** Every claim about the codebase must cite `file.py:line`. No assumptions. If you cannot verify, say so.
4. **Verify before claiming done.** Run the command, show the output. `ruff check`, `pytest`, actual CLI invocation.
5. **Planning sessions produce self-contained implementation plans** a fresh session can execute without this conversation's context. Write them under `docs/superpowers/plans/`.
6. When done with a work item, update `TODO.md` in the same commit as the code.
7. **Every diagnostic number a run produces must be written to `.rudra/run/logs/`.** Rudra's only support channel is a user emailing that folder, so a number held in memory, printed to a console panel, or written only at run end cannot answer anybody. Read **§8a** before adding code that measures, guards, retries or halts anything.

---

## 3. Architecture

```
src/rudra/
├── cli.py                  Typer app: main callback + `models`, `config`, `init`, `doctor`
├── config/                 Layered TOML config. schema / layers / loader / template
├── llm/                    Provider-agnostic model factory.
│                           retry.py is the ONLY retry policy a model call has:
│                           providers.py's `no_client_retries` switches every
│                           client SDK's own retries off (max_retries=0, 1 on
│                           google which counts ATTEMPTS). Every status 500-599
│                           is transient, `x-should-retry` decides before the
│                           status, and `retry_wait` sleeps out `Retry-After`,
│                           capped at 120s. The two calls made outside any graph
│                           (the AGENTS.md summariser and `probe_role`) call the
│                           middleware's wrap directly. The stream-level retry in
│                           permissions/approval.py is not a third layer.
│                           Both OpenAI-prefixed entries pass `stream_usage=True`
│                           and `stream_chunk_timeout = the role's timeout`, so a
│                           streamed call reports usage and is bounded by the
│                           role's own timeout rather than langchain_openai's
│                           120s default. Never 0: on a keepalive-sending server
│                           a stalled stream would have no other bound
├── stacks/                 Multi-language stack detection
├── permissions/            The gate. One pure engine, two mechanisms:
│                           rules/floor/grants decide; middleware denies;
│                           interrupts+approval ask; diff/audit/env support it.
│                           env.py sets PIP_REQUIRE_VIRTUALENV=1 in EVERY
│                           command's environment: an env var, not a deny rule,
│                           because it removes the capability rather than naming
│                           a spelling (pip3, python -m pip and a Makefile target
│                           all covered). ExecuteGuardMiddleware explains the
│                           refusal in-band and counts `installs_refused`
├── shell/                  runner.py — the ONLY subprocess call site.
│                           Renders argv, asks the engine as execute:<command>
├── git/                    core.py — Python git API. Orchestrator-facing.
│                           STATE_EXCLUDE hands git the .rudra pathspec so a
│                           tracked state file never prints in a diff
├── testing/                runner.py → TestResult, parse.py counts.
│                           project_env.py: for a PYTHON project only, builds
│                           `.venv` with the machine's interpreter and installs
│                           pytest then the project's declared deps, at the top
│                           of work() and before every gate run — never in
│                           plan(). Rudra installs, not an agent: once
│                           `.venv/bin/python` exists the gate runs under it.
│                           Leaves alone any venv without its
│                           `.venv/.rudra-sync.json` marker and any active
│                           VIRTUAL_ENV. The pytest it installs is run as
│                           `.venv/bin/python -m pytest`, never the `pytest`
│                           script, so a flat layout can import itself
├── verify/                 The deterministic gate:
│                           syntax · lint · typecheck · test · stubs.
│                           Blocking except lint — and except typecheck when it
│                           ran RUDRA's bundled mypy, whose verdict is a function
│                           of Rudra's own site-packages. A project that installs
│                           mypy into its own .venv gets the blocking verdict
│                           back. Only resolve_typecheck_command knows which mypy
│                           it picked. VerifyReport.escalate splits fix-loop input
│                           (iterate) from user action (stop). Calls run_gated and
│                           run_tests; starts no subprocess
├── subagents/              The four subagents: coder · tester · reviewer ·
│                           general-purpose. spec/registry/build/runner.
│                           `run_subagent` gives an invocation ONE extra turn
│                           when its final message announced a next action rather
│                           than a result: `announces_continuation` judges the
│                           final sentence only, requires it short, matches
│                           first-person future-tense openers. The nudge is ONE,
│                           after the guards, sharing the invocation's budget.
│                           `subagent_done` carries `nudged` and `nudge_outcome`
│                           (continued / confirmed_done / announced_again /
│                           unanswered, decided from what the nudged turn did).
│                           build.py is the ONLY assembly path, so every subagent
│                           carries the gate — deepagents inherits interrupt_on
│                           but NOT middleware. The reviewer cannot write because
│                           the tools are never registered, not because a prompt
│                           says so. `_prompt_for`: facts, then the PROJECT FILES
│                           listing (coder/tester only, rebuilt every invocation,
│                           never cached), then recalled memories, then the MCP
│                           catalog. Invocation halts: MAX_REPEATED_CALLS resets
│                           read counts on any non-read call; MAX_DEDUPES_AFTER_
│                           WRITE halts on 8 deduped reads in a row counted only
│                           after a first real write; MAX_TOTAL_CALLS bounds a run
├── facts/                  The open fact store. store.py imports nothing from
│                           Rudra. A fact is {value, why, source}; keys are
│                           enumerated nowhere in code — validation covers types
│                           and size, never names. render.py puts facts in every
│                           agent's prompt
├── context/                Context budgeting. budget.py is pure and imports
│                           nothing from Rudra. evict_limit derives the eviction
│                           threshold from the same context_tokens that feeds
│                           max_input_tokens, so eviction and summarization cannot
│                           drift. deadline.py is pure too: SpanDeadline (limit,
│                           injectable clock, expired(), tripped_at), the
│                           SPAN_DEADLINE ContextVar its middleware reads, and the
│                           span_deadline contextmanager that owns the reset.
│                           0.0 is no bound
├── loop/                   The agentic loop. ledger · bounds · regressions ·
│                           decomposition · dependencies · tools · engine.
│                           ledger/bounds/regressions/decomposition import
│                           nothing from Rudra; dependencies.py only the report
│                           types and verify/stubs.py's walk. regressions.py
│                           answers which failures a task answers for. No
│                           agent-facing tool can write DONE — only engine.py,
│                           and only on VerifyReport.passed or a failure that
│                           predates the task. decomposition.py is the SHAPE of a
│                           task list: `add_tasks` refuses a list of 3+ non-test
│                           tasks when a FACT VALUE says the deliverable is one
│                           file (reads values, never key names), refusable once
│                           per run. `forbidden_shape_refusal` is per-task and
│                           kills: a task that only checks what the gate already
│                           checks, "create the project structure", and a task
│                           that only runs the tests (first word run/rerun/
│                           execute + a test word, declined by a closed writing-
│                           verb set). Words are edge-stripped of punctuation,
│                           never split inside
├── memory/                 Long-term memory. store.py is the ONLY module that
│                           imports mempalace, and imports it lazily (chromadb
│                           pulls onnxruntime/grpcio/opentelemetry, which at
│                           module scope would land on `rudra --version`).
│                           entry/taxonomy/degrade import nothing from Rudra.
│                           Every mempalace call passes palace_path,
│                           collection_name and backend explicitly (two of three
│                           cannot be overridden by env). render.py builds the
│                           recall block; the write spine lives in loop/engine.py
│                           beside the AGENTS.md writer. export.py owns both halves
│                           of the round trip; prefetch.py warms chromadb's ONNX
│                           MiniLM through mempalace's own code path
├── trace/                  The run trace. events · render · stream · sink ·
│                           debug. Imports nothing from agent/, loop/ or
│                           subagents/. ONE event vocabulary, three consumers:
│                           console, always-on JSONL run log, transcript.
│                           stream.py keys position by subgraph NAMESPACE.
│                           render.py escapes every payload. Every kind but NOTICE
│                           describes something the MODEL did; NOTICE is what Rudra
│                           says about ITSELF (a guard halt, a retried call) —
│                           emit through TraceSink.notice, the only path that
│                           redacts. Token streaming (`--stream`) is the one path
│                           into the sink that is not a values chunk:
│                           permissions/approval.py emits deltas as AI_TEXT keyed
│                           by namespace, and `consume` skips an AIMessage's prose
│                           when it ends a noted stream so a message is not
│                           recorded twice. Text, never message ids
├── agent/
│   ├── main_agent.py       RudraAgent — run setup; run() delegates to run_loop,
│   │                       build_backend() → CompositeBackend(default=LocalShellBackend)
│   └── planner_agent.py    THREE staged agents: clarify · architect · breakdown,
│                           one tool set each via _tools_for_stage. consult_planner
│                           + the lifted _stream_planner_turn. Indexes NO skills:
│                           the corpus is written for one agent that clarifies,
│                           designs and builds in one conversation, so its
│                           methodology is merged into the three stage bodies
│                           instead. `_stream_planner_turn` counts only what THIS
│                           turn produced: a stage's consults share one thread
│                           (thread_id=f"{session_id}-{stage}") and stream values,
│                           so `_history_baseline` seeds both the guards' `seen`
│                           and the trace counters once per namespace, anchored on
│                           the last HumanMessage. `create_planner_agent` decides
│                           ONCE per build whether the project holds anything to
│                           read (filesystem/tree.py::is_greenfield) and hands the
│                           same boolean to tools and prompt; a stage on a project
│                           of only NON_CONTENT_NAMES is built with the
│                           FilesystemMiddleware's `.tools` emptied
├── middleware/             2 survivors of D4 (opt-in behind [compat]), plus
│                           always-on guards. Each guard is a patch for a measured
│                           failure mode, documented in its own docstring:
│                           · memory_prompt.py — replaces deepagents'
│                             MEMORY_SYSTEM_PROMPT (which teaches an agent with no
│                             write tools to persist memory via edit_file), via
│                             system_prompt= + replace-by-name, not a monkeypatch;
│                             keeps the trust guidance, drops the write orders
│                           · repeat_guard.py — an agent may not re-run a READ
│                             that already failed twice with identical args and
│                             nothing between; a WRITE whose bytes are already on
│                             disk is refused (keys on the file, not the spelling,
│                             via compat/virtual_paths.py); execute is excluded.
│                             Refusals carry the settled route from tool_route.py
│                             and the spec's grants. Write-belief lives in two
│                             places (instance map = this invocation; RunUsage =
│                             an earlier one). No refusal is a tool failure — a
│                             FIELD (REFUSAL_KEY) set at `_as_message`, read by
│                             `message_is_error` before any text, so the subagent
│                             counter, planner counter and renderer agree; a
│                             refusal neither increments nor CLEARS the counter
│                           · gutter_indent.py — read_file renders a 2-space
│                             gutter, so a model composing old_string from what it
│                             was shown carries it in and edit_file can never
│                             match. Anchors on stripped line content; the
│                             corrected old_string is the file's own bytes. Every
│                             uncertain case declines and passes through. Only ever
│                             turns a certain failure into a success
│                           · machine_paths.py — every backend is virtual_mode,
│                             so a model's /usr/bin/python is
│                             <project>/usr/bin/python and answers "No files
│                             found". The explanation is APPENDED to the empty
│                             result (never prepended — the failure counter keys
│                             on a leading `Error`). Asks `virtual_to_relative` of
│                             the RESOLVED path, fires only where the path lands in
│                             a machine directory. On SUBAGENT and PLANNER stacks,
│                             OUTSIDE the repeat guard
│                           · content_paths.py — a path written INTO a file, which
│                             a real interpreter resolves against the MACHINE root.
│                             Fires on write/edit only, for a file something later
│                             RUNS (.py .js .sh Makefile — never .html/.md/.json),
│                             and only for a literal whose project-relative
│                             spelling is a file on disk. Appends; never rewrites
│                             content. Two scanners: find_project_absolute_literals
│                             reads SOURCE (needs a quoted literal),
│                             find_project_absolute_mentions reads the gate's PROSE
│                             and feeds loop/engine.py::_test_path_note. SUBAGENT
│                             stack only
│                           · fix_write_params.py — fence-stripping and
│                             filename/path → file_path aliasing always on;
│                             sandbox-prefix stripping behind [compat]
│                             sandbox_paths. Four REFUSALS (not repairs): the
│                             directory placeholder, the prose write
│                             (_is_prose_not_content — four conditions over a
│                             closed suffix table), the completion file
│                             (_is_completion_announcement — name is completion
│                             words, at project root, one short sentence), and the
│                             write that would invent a tmp/. Leads with REJECTED:,
│                             counted as a tool failure by status="error". Never
│                             rewrites content; an empty write is not this rule
│                           · planner_write.py — PLANNER stack only. The planner
│                             grants no write tool; this answers an attempt with
│                             the route (file, and that the contents must not be
│                             produced anywhere) instead of langgraph's tool-list
│                             echo. The route names only tools the stage holds,
│                             read off _tools_for_stage; a stage holding neither
│                             declines to name one. execute deliberately not
│                             covered. Constructor takes `stage_tools` not `tools`
│                           · test_extension.py — TESTER stack only. A test the
│                             runner can never collect is not a test: refuses a
│                             tests/ write whose basename reads as a test but whose
│                             suffix is in the closed _UNCOLLECTABLE set and whose
│                             body `ast.parse`s (the load-bearing condition — HTML
│                             is not valid Python). .json/.yaml/.yml are measured
│                             members. Gated on "run_tests" in spec.rudra_tools
│                           · tool_route.py — every SUBAGENT stack. A call to an
│                             unregistered tool in a closed shell/finish set, whose
│                             name is not in the spec's grants, is answered with
│                             the route read off fs_tools | rudra_tools. Counted as
│                             a tool failure
│                           · span_deadline.py — BOTH stacks. The seconds bound,
│                             read in a before_model hook returning {"jump_to":
│                             "end"} so it fires before the NEXT call and never
│                             cancels tools already requested. Announces nothing
│                             (records tripped_at; the span owner says so once the
│                             stream drains). A span that overruns but FINISHES on
│                             its own is not halted. A delegate subgraph inherits
│                             the ContextVar
│                           · planner_greenfield.py — GREENFIELD planner stage
│                             only. That stage holds no file tool;
│                             GreenfieldReadMiddleware answers an unregistered
│                             ls/read_file/glob/grep with the route. Right after
│                             PlannerWriteMiddleware, outside the repeat guard
│                           · execute_guard.py's third note — a FAILED execute
│                             whose command named this project's path with the
│                             file tools' leading `/` is told the relative
│                             spelling (the inverse of machine_paths.py). Annotate,
│                             never repair. The token must be one the SHELL itself
│                             NAMED. The `cd` note stands it down when both apply
├── tools/                  EVERY tool the model can call, and nothing else:
│                           interaction (record_fact · ask_user) · git_tools ·
│                           testing_tools · memory_tools (remember ·
│                           search_memory). `remember` is CONTROL_PLANE, not
│                           MUTATING: writes under .rudra/ only. AskOption reads a
│                           bare string as the label; record_fact defaults `source`
│                           to "inferred" (never "asked"); `why` stays required.
│                           A schema is a prompt — every required field is an
│                           instruction the model must satisfy first try, so ask
│                           only for what it alone knows. The last two are thin
│                           wrappers over git/ and testing/
├── filesystem/             capped project_tree() — VFS deleted (D7).
│                           TREE_MAX_ENTRIES caps the subagents' PROJECT FILES and
│                           the planner's PROJECT STRUCTURE both. is_greenfield
│                           reads a closed root-level NON_CONTENT_NAMES and
│                           re-walks without the depth cap before answering True.
│                           rudra_state.py: the project root's backend, subclassed
│                           so read/ls/glob/grep never see .rudra/ (its config.toml
│                           may hold an api_key). A SUBCLASS because deepagents
│                           reads isinstance(backend.default, LocalShellBackend).
│                           download_files is NOT filtered (MemoryMiddleware loads
│                           .rudra/AGENTS.md). Paths resolve through
│                           virtual_to_relative + realpath, case-folded. Writes are
│                           the floor's `rudra-state` rule; state/paths.py::
│                           is_rudra_state is the one predicate both ask. execute
│                           still reads it
├── state/                  paths.py (D15 layout), session id. archive.py is the
│                           ONE thing here that writes OUTSIDE the project: at run
│                           end it copies usage.json, the ledger, the debug log and
│                           the transcript to
│                           $XDG_STATE_HOME/rudra/runs/<project>/<run>/, because
│                           every instrument lives in a directory the measured runs
│                           are deleted with. Imports nothing from Rudra but
│                           state/paths.py, never raises, records the models a run
│                           used in meta.json (NOT config.toml, which may hold an
│                           api_key)
├── telemetry/              The run, sent somewhere the maintainer can reach it.
│                           langfuse_sink.py builds ONE trace per run
│                           (create_trace_id(seed=session_id) is deterministic).
│                           THREE layers: the CallbackHandler in the RunnableConfig
│                           (model/tool spans), Telemetry.consumer() on
│                           TraceSink.add_recorder (guard halts, refused plans,
│                           NOTICEs), and finish() at close() (verdict + usage).
│                           The security argument is mask=: every input/output/
│                           metadata passes through trace/redact.py before export.
│                           FAILS CLOSED. Off unless keys are configured. SDK retry
│                           chatter is forwarded into the run log, not silenced
├── mcp/                    MCP client + config. `.mcp.json` in Claude Code's
│                           schema, so an existing config pastes in unchanged;
│                           three meta-tools rather than one tool per server
├── skills/                 The vendored superpowers corpus and its delivery:
│                           bundle · registry · manifest · transform · validate ·
│                           cache · sources · notice · rudra_tools. Rendered into a
│                           user-level cache and reached through a CompositeBackend
│                           route. Consumers are the CODER and TESTER only.
│                           transform.py rewrites the rendered copy three ways
│                           (cross-refs → readable paths, Rudra's reference file
│                           added, two frontmatter descriptions replaced —
│                           deepagents copies description verbatim into every
│                           indexing agent's system prompt). Bodies are never
│                           touched
└── compat/                 monkeypatches + version guard, and TWO shared facts
                            about the machine, each with one definition and
                            several consumers. virtual_paths.py says which real
                            file a model-written path names, shared by the gate,
                            the approval preview and the backend. Cross-platform by
                            shape: C:\..., UNC, \x and /x all resolve on every
                            platform. The backend's root strip IS its
                            strip_project_root (resolves before matching, so a NUL
                            path still fails inside validate_path).
                            own_interpreter.py says which Python is RUDRA's own,
                            shared by stacks/detect.py (must never pick it for the
                            user's tests) and permissions/env.py (must keep it out
                            of the agent's shell environment). Inert unless
                            sys.prefix != sys.base_prefix
```

**Permission flow.** `build_gate(cfg, project_path)` returns a `Gate` bundling
the engine, the deny middleware, the `interrupt_on` map, session grants, and the
audit log — constructed together because they must share objects.
`PermissionEngine.decide(tool, args)` is pure and is the only thing that
interprets policy. Precedence, top down: **deny floor** (every mode,
`floor_disable` per rule) → `permissions.deny` → session grants →
`permissions.allow` → mode default. Deny beats allow; reads are never gated;
Rudra's own `add_tasks`/`drop_task`/`ask_user` are control plane and never gated.

**The approval prompt has five answers:** `a` approves one call, `r` rejects,
`A` grants one rule (`suggest_grant` — one path, or one command's first word read
as a shell reads it and matched literally), `!` **auto-accept** stops asking for
the rest of the session, `d` shows the full diff and asks again. `!` sets
`SessionGrants.approve_all`, read at the *session grants* step, so the deny floor
and `permissions.deny` have already returned. `git-dir`, `rudra-state`,
`catastrophic-command` and every user deny rule still refuse after `!`; every call
is still audited.

**An agent's approval map is the gate's map NARROWED to the tools that agent
holds** (`permissions/interrupts.py::narrow_interrupt_on`). deepagents'
`HumanInTheLoopMiddleware` matches on the tool-call name BEFORE the tool node
discovers it is unregistered, so an unnarrowed map prompts for calls the agent
cannot make. One implementation, two call sites:
`subagents/build.py::_interrupt_on_for` and `agent/planner_agent.py`, narrowing
per stage against the tools that stage was built with. Setting `engine.mode =
"auto"` would be wrong: the auto branch denies `execute` unless `shell_in_auto`.

**Session grants outlive the run.** `build_gate` and `create_main_agent` both
take an optional `grants=`, and `_repl_session` builds ONE `SessionGrants` before
its loop (the REPL builds a fresh agent per input). The REPL's task panel reads it
too: `_permission_notice(cfg, grants)` appends `auto-accept on for this session`.

### Control flow (`loop/engine.py`)
1. **Planning runs in three stages before any code:** `clarify` settles the facts, `architect` records layout and boundaries, `breakdown` calls `add_tasks` with units of **work**, not filenames. Each stage is a separate agent with its own tool set (clarify cannot `add_tasks`, breakdown cannot `ask_user` or `record_fact`), so a stage cannot do another stage's job. The fact store is the only channel between them, and no stage has a tool that can mark anything done.

   **No planner stage indexes a skill, and that is load-bearing.** The three
   stages *are* a planning methodology, implemented in Python. The superpowers
   corpus is a methodology written for ONE agent that clarifies, designs and
   implements in a single conversation; handed both, every stage ran the
   corpus's version and emitted "write a design doc" as a task. **A prompt
   cannot outrank a prompt** — the `using-superpowers` bootstrap outranks any
   counter-prose injected into the same prompt. What works is `_tools_for_stage`'s
   argument applied to skills: **absence is the enforcement.** The methodology
   is merged into the stage bodies instead.
2. **The plan is presented and approved:** facts with their source, then the tasks. In `ask` mode the user approves, revises (re-enters `breakdown` with their words **verbatim**, up to `MAX_REVISIONS` = 3), or cancels. `--plan` presents and stops. `--auto` skips the gate. **EOF and Ctrl-C are cancel, never approve.** The gate lives in `RudraAgent`, between `plan()` and `work()`; the loop does not know what a terminal is.
3. `work()` takes the next pending task and calls `run_task`.
4. `run_task`: coder subagent writes → `verify_project` gates → on failure the blocker goes back **verbatim** and it retries, up to `[agent] max_fix_attempts` — **a budget per RUN, not per task lifetime.** The bound is a ceiling captured at entry, `task.attempts + max_fix_attempts`: `task.attempts` persists in the ledger and `_stop` returns a `STOP_RUN` task to `PENDING` with its attempts spent. A ceiling rather than a second counter because the give-back decrements `task.attempts` itself.

   **The budget is flat, and exhausting it is not the same as never converging.**
   C6.5a stops a task whose failure repeats; a task whose every gate shows a NEW
   failure (the coder has no shell and sees one layer per gate) spends the whole
   budget and goes `BLOCKED`, which `--continue` never retries. `max_fix_attempts`
   is the documented lever. `loop/bounds.py::convergence` compares the last gate
   with the one before (got further, or none of the old failures survives) and
   records `converging` / `not converging` / `unreadable` on an exhausted note's
   second line and `task.convergence`; it extends nothing. It reads pytest's
   rewritten `E assert …` lines, which `_exception_lines` does not and must not.

   **A gate that failed ONLY on packages nobody declared gives its attempt back.**
   `loop/dependencies.py::missing_packages` reads pytest's `E` column and answers
   only when EVERY exception is one (`No module named` for a non-local, non-stdlib
   name; a `pip install <x>` hint; an `ImportError` saying something is not
   installed) and never on a truncated tail. Judged on the gate itself, after
   C6.5a. `MAX_DEPENDENCY_GATES` (3 per task per run) bounds one that meets a new
   package every time; recorded in `task.dependency_gates`, `usage.json`
   `run.dependency_gates` and a `dependency-gate` NOTICE.

   **For the test stage, verbatim means the runner's own output, never the
   findings parsed out of it** (`loop/engine.py::_blocker_text`). pytest's frame
   lines are findings and none carries the exception, which pytest prints on an
   `E` line. The findings still drive the no-progress signature
   (`loop/bounds.py`) and the regression keys (`loop/regressions.py`); only what
   the coder reads changed. Other stages still list findings alone.

   **Before every gate run, a Python project's `.venv` is synced**
   (`testing/project_env.py`), and a failed dependency install is appended to the
   next blocker (`_env_sync_note`). The sync never ends a run.

   **Every gate a retry follows is the one it is told about.** An attempt that
   changes no file still runs the gate, and that gate's blocker is what the next
   attempt is sent. A vacuous gate (no blocker, no test judgement) leaves the
   blocker as it was.
5. Success = **`VerifyReport.passed`, or a failing gate whose every located failure was already failing before this task ran** (`loop/regressions.py`). Only `engine.py` writes `DONE`. The second clause exists because the gate is project-wide and the coder is task-scoped: a test written by an earlier task against code a *later* task would build must not deadlock the suite. **It is a time comparison, never a file comparison** — a NEW failure blocks the task whether or not it touched the file. The baseline is per-run and in memory (`LoopContext.failure_baseline`), never persisted: a fresh process (the first task, or `--continue`) has none and the loop behaves as before. **A task that changed no file inherits a red gate it began with, located or not** (`LoopContext.baseline_failed`, in memory): in the empty-diff branch, no file changed by any attempt plus a gate already failing when the task began reads as `INHERITED`.
6. Two identical failure signatures in a row → `BLOCKED` (C6.5a). A gate `escalate` → the whole run stops. **A blocker with no findings is signed by its `detail` and the exception lines in its output** (`loop/bounds.py::_exception_lines`) — absolute paths and memory addresses masked, relative paths kept, sorted. **An escalation stops the run after an attempt that wrote nothing, too** (`run_task`): the empty-diff branch runs its own gate, checked before any verdict is read.
7. The planner is consulted again **only** on a block or an empty ledger — never after an ordinary success, and only the `breakdown` stage is re-entered. Clarify and architect run once.

   **Every re-consult is SHOWN the ledger.** A stage's consults share one thread,
   so the model reads each new message against whatever `read_ledger` last
   returned it; `loop/tools.py::render_ledger` is the one spelling, shared with the
   tool, so what the planner is shown and what it can fetch cannot drift. An agent
   that writes into something is shown it, never merely offered a tool that would
   fetch it.

   **A blocked re-consult asks for a task that fixes the CAUSE, never "a different
   approach"** — a blocked task's files stay on disk and the tests run after every
   later task. `loop/tools.py::BLOCKED_RECOVERY` says so, read by the consult, the
   BLOCKED duplicate refusal and the BLOCKED `drop_task` refusal alike.

   **And what that consult adds is worked NEXT.** `work()` opens
   `ledger.unblocking(task.id)` around that consult alone, so a task added inside
   it is inserted before the first `PENDING` one and carries `unblocks`. List order
   stays the one truth for `next_pending`, `resumable` and `render_ledger`; ids
   stay `len + 1`, so `ledger.json`'s order is no longer id order.
8. Reviewer runs once at the end, advisory, printed, gating nothing. **It is shown the files the run touched, and told which tool suits which file:** `git_diff` for one that existed before the run, `read_file` for one the run created. The claim is pinned against `git_tools.py::_untracked_note`'s own source rather than restated.

**The split that makes this work:** the model decides what work exists; Python
decides when a task is done and when to stop. The ledger tools *cannot express*
`DONE`.

Two edges: an **empty diff is a failed attempt** (with no changed files the gate
reports "0 files parsed" and would pass an untouched task), and `files_touched`
comes from the **filesystem**, not the model — `git status` where there is a
repo, a fingerprinted `project_files` walk where there is not.

**`files_touched` and the gate scope — invariants, each paid for once:**
- `attempt_snapshot` is the single entry point and never returns `None`, so the empty-diff guard runs whether or not the project has a `.git`.
- There is **one** build-output pruning rule, `verify/stubs.py::is_build_output`, which both `project_files` and `git_snapshot` call — **do not add a fourth copy of that literal.** The pin `test_the_three_copies_of_the_pruning_rule_agree` exists because two copies drifted while each carried a docstring asserting they could not.
- `verify/stubs.py` answers two questions separately: `source_files` is what the stub scanner may read, `project_files` is what the project contains (the first's walk without the suffix filter). **Both are needed; do not collapse them again.**
- `files_touched` is **every attempt's files, not the last one's** (`_record_touched` keeps a sorted union). The loop hands that field to the gate as `changed_files`, which scopes the **syntax stage** as well as the stub scan. **The empty-diff guard asks about the attempt; the record and the gate ask about the task. Do not make them one variable again.** A deleted path stays in the gate's scope.
- An invocation that fails mid-stream has run: the error branch records its own diff at once and carries its snapshot (`own` and `before`, differing only after a failure). The record and the `run_errors` sentence read `own`.
- An attempt that wrote nothing takes the writing path when the task's OWN work has met no judging gate in this call — files of its own, nothing here `rejected` it, and the gate is green with no test judgement (`own_work_untested`, read from the single `_verify` above both branches).

### `.rudra/` state directory

Split into durable and volatile subtrees by D15. `src/rudra/state/paths.py` is
the single source of truth — never build a `.rudra/...` path by hand.
`rudra_paths()` is pure; `ensure_layout()` is the only function that creates
anything.

| File | Durable? | Written by | Read by | Notes |
|---|---|---|---|---|
| `config.toml` | durable | `rudra init` | config loader | Layer 3 of five |
| `AGENTS.md` | durable | `_ensure_agents_md`; `record_task_in_memory` + `summarise_architecture` update it | planner via `memory=` | A living document: one Session Log entry per completed task, with `files_touched` from git, and one model call per run folding them into Architecture Notes. Log capped at 20 entries — paid for on every planner call |
| `facts.json` | durable | `record_fact` / `ask_user` (agent) | every agent's prompt | Open key/value: `{key: {value, why, source}}`. Keys enumerated nowhere in code. Replaced `project.json`, which is ignored, not migrated |
| `.gitignore` | durable | `ensure_layout` | git | Scopes **only** `.rudra/` |
| `run/ledger.json` | volatile | `add_tasks`/`drop_task` (agent) + `engine.py` (status) | `run_loop`, `summarise` | Tasks are **work**, not filenames. Written atomically after every status change; never resumed. `halts` records every subagent guard that fired, separate from `note` because `note` is read by a MODEL later and every branch that finishes a task rewrites it. `run_errors` is the same field for the opposite event — an invocation that never STARTED (a build failure, a retry budget that ran out) or that started, ended cleanly, and did not do its job. A failed invocation that made tool calls reads `stopped mid-run after N tool call(s)`; `could not run` for one that made none. `SubagentResult.tools` is None (nothing measured it) vs `{}` (ran, called nothing) |
| `run/checkpoints.db` | volatile | `AsyncSqliteSaver` | nothing | fresh uuid4 thread_id each run — never resumed |
| `run/logs/meta.json` | volatile | `state/archive.py::write_run_meta`, at run START | humans, archive | What ran, on what, in which mode: Rudra's version, python, platform, deepagents' version, permission mode, terminal attached, the four bounds, each role's provider/model/base_url. **Never `api_key` or `api_key_env`.** Written at START so the run that needs explaining has not ended. The archive writes the same dict plus `archived_at` and `files` |
| `run/logs/permissions.jsonl` | volatile | `permissions.AuditLog` | humans; `rudra audit` | One line per gated decision, every mode |
| `run/logs/verify.log` | volatile | `verify_project` | humans | Every stage's full output from the last gate run |
| `run/logs/tests.log` | volatile | `testing/runner.py:155` | humans, the `run_tests` tool | The last test run's full output, which the tool's own result only tails. Copied out by `archive.py` |
| `run/repl_history` | volatile | `cli_repl.build_session` | prompt_toolkit | Per project. A read-only project loses history, never the REPL |
| `run/transcripts/<id>.jsonl` | volatile | `trace/transcript.py`, every run | humans, `rudra log` | One JSON object per `TraceEvent`, appended and flushed per event. Payloads capped at 2000 chars, newest 20 runs kept. Written by default — safe only because redaction happens where the event is built, not in the renderer |
| `run/logs/debug-<id>.jsonl` | volatile | `trace/debug.py`, **every run** | humans, bug reports | The complete record — registered with `TraceSink.add_recorder`, no trace level filters it, payloads uncapped. One file per run, newest 20 kept by `prune_debug_logs`. `[agent] debug_log = false` or `--no-debug` turns it off. An unopenable file disables the log rather than failing the run. Carries two kinds that are NOT `TraceEvent`s: `{"kind": "model_call"}` (role, seconds, both token counts, ok) and `{"kind": "subagent_done"}` (seconds, tool_calls, a `tools` histogram, the halting guard). `_JsonLines.format` promotes `record.event` to the top level |
| `run/logs/usage.json` | volatile | `write_usage_log` | humans | Two blocks: `roles` (per-role tokens, compactions, retries, exhaustions, plus guard counters — `edits_reindented`, `plans_refused`, `content_paths_flagged`, `test_writes_rejected`, `planner_halts`, `planner_writes_refused`, `tool_routes_answered`, `completion_files_refused`, `greenfield_reads_answered`, etc.) and `run` (`wall_seconds`, `counted_seconds`, `suspended_seconds` — the **only true wall clock in the project**). Read the failure rate as `(retries + exhaustions) / calls`. A write failure is swallowed. Written mid-run (at every coder dispatch and once after planning) as well as at both ends, so the file that answers "why is this taking so long" exists for a run still in progress. Overwriting |
| `run/artifacts/` | volatile | deepagents eviction + summarization | the agent, via `/artifacts/` | Kept out of the project by `artifacts_root` |
| `memory/export/` | durable | `rudra memory export` | `rudra memory import` | The portable markdown copy. `exporter.export_palace` is **not** used — it resolves collection and backend from the user's global config |
| `memory/palace/` | volatile | `rudra.memory.store` | `rudra.memory.store` | ChromaDB, project-scoped. Opened only through `MemoryStore`; a failure degrades loudly and never fails a task |

**Every row above is inside the project, which is why `state/archive.py` copies
the instruments OUT**, to `$XDG_STATE_HOME/rudra/runs/<project-slug>/<run-id>/`,
at `RudraAgent.close()` — the last moment every instrument is still on disk.
`[agent] run_archive = false` turns it off. **An instrument whose output is
deleted is not an instrument; it is a print statement.**

**No prompt names a state path at all** — the ledger is reached only through
tools, and `tests/test_rudra_dir_migration.py` asserts that absence.

---

## 4. What deepagents Already Provides (and Rudra ignores)

Verified against **installed 0.7.4**. The 0.4.12 → 0.7.4 delta is recorded in
`TODO-old.md` §F. `create_deep_agent()` accepts:

| Param | What it gives | Rudra uses it? |
|---|---|---|
| `model: str \| BaseChatModel` | `provider:model` resolved via `init_chat_model` | ⚠️ passes a `BaseChatModel` instance from `llm/factory.py` |
| `skills: list[str]` | `SkillsMiddleware` — Anthropic Agent Skills spec | ⚠️ **coder and tester only** (`subagents/build.py::_skills_for`). The planner passes `skills=None` |
| `subagents: list[SubAgent]` | `SubAgentMiddleware` + the `task` tool | ✅ four specs in `subagents/registry.py`. Rudra ships its own `general-purpose` to suppress the ungated one deepagents auto-adds. Passed at `main_agent.py` — the main agent only. The planner passes `subagents=None` and reaches the auto-added one by withholding `task` (`DelegationGuardMiddleware`) |
| `memory: list[str]` | `MemoryMiddleware`, AGENTS.md into system prompt | ⚠️ planner only |
| `permissions: list[FilesystemPermission]` | `allow` / `deny` / `interrupt` path rules | ❌ **cannot be used** — raises on any execute-capable backend (U.7) |
| `interrupt_on: dict` | `HumanInTheLoopMiddleware` — approval gates | ✅ one entry per mutating tool, `when` calling Rudra's `PermissionEngine`. **Narrowed per agent** (`narrow_interrupt_on`) |
| `backend` | `FilesystemBackend` / `LocalShellBackend` / `CompositeBackend` | ✅ `CompositeBackend(default=LocalShellBackend)` |
| (automatic) | `create_summarization_middleware` | ✅ inherited, not designed |
| (automatic) | `TodoListMiddleware`, `PatchToolCallsMiddleware` | ✅ inherited |

**`execute` only works on a backend implementing `SandboxBackendProtocol`.**
`LocalShellBackend` does; plain `FilesystemBackend` does not — the tool is still
registered and returns an error when called. Since Rudra ships
`CompositeBackend(default=LocalShellBackend(...))`, `execute` works and agents can
run tests, linters, builds and git. The composite carries an `/artifacts/` route
with `artifacts_root="/artifacts"`, or deepagents would write
`large_tool_results/` and `conversation_history/` into the user's project.

**`permissions=` is deliberately never passed** — it raises
`NotImplementedError` on any execute-capable backend, and its
`FilesystemOperation` is `('read','write')` only, so it never covered `execute`.
Rudra's own gate does the whole job. See `TODO-old.md` **U.7**/**A1.46**;
`tests/test_deepagents_contract.py::test_permissions_still_rejected_with_execute_backend`
reopens U.7 if upstream lifts the restriction.

**Skills compatibility:** superpowers skills match the format `SkillsMiddleware`
parses, so they drop in as a skills source with no format conversion.

---

## 5. Provider Independence

`src/rudra/llm/` is the model factory; `langchain-openai` ships as a dependency;
`ChatOllama` is constructed in exactly one place.

| Rule | Enforcement |
|---|---|
| No module outside `rudra/llm/` may import a provider package | `tests/test_no_direct_provider_imports.py` parses every module with `ast` |
| Ask for a model by role, never by provider | `build_model(role)` (`llm/factory.py:64`) — no network call; every configuration error raises before inference |
| One adapter covers the OpenAI-compatible world | `provider = "openai_compatible"` with a `base_url` reaches vLLM, OpenRouter, LM Studio, Groq and Together through one code path (`llm/providers.py:87`) |

The `OLLAMA_*` variables survive only as a deprecation shim that warns and maps
to `RUDRA_*`.

---

## 5a. Context Management

Most of what manages context is **inherited from deepagents**; Rudra's work was
making it real (three of four inherited mechanisms were present and inert before).

| Mechanism | Origin | State |
|---|---|---|
| Auto-summarization at 85% of the window | inherited | Real since `context_tokens` is declared per role |
| Tool-result eviction to `/artifacts/` | inherited | Real since a derived threshold is passed |
| `compact_conversation` | inherited | Registered for the coder and tester only. Control plane — outside `CONTROL_PLANE_TOOLS` the gate denies it |
| Artifacts kept out of the user's repo | **designed** | `artifacts_root="/artifacts"` |
| Per-run token accounting | **designed** | `src/rudra/context/usage.py` |
| Recalled memories in every prompt | **designed** | `memory/render.py` + `recall_limit`. One number feeds summarization, eviction and recall; `usage.json` carries `recall_chars` per role |
| The project file listing in coder/tester/planner prompts | **designed** | `filesystem/tree.py` + `wants_tree`, capped at `TREE_MAX_ENTRIES = 150`. The planner's is billed too; `usage.json` carries `tree_chars` per role |
| Per-task memory in `AGENTS.md` | **designed** | `src/rudra/context/agents_md.py` + the two writers in `loop/engine.py`. Session Log capped at 20 |
| Per-role and per-task elapsed time | **designed** | `UsageMiddleware` times every model call; `run_task` times every task. **No cost figure, ever.** These count *running* time (every clock is `monotonic`/`perf_counter`, blind to suspension) |
| The run's true wall clock | **designed** | `RunUsage.started_wall`/`started_mono` + `suspended_seconds`. Over `SUSPENDED_NOTICE_SECONDS` the panel says so and a `suspended` NOTICE is emitted. **If a number will be compared against a human's experience, it needs a wall clock, not a monotonic one** |
| The run trace | **designed** | `src/rudra/trace/`. Before this the subagents printed nothing |

**One number, two consumers.** `[model.<role>] context_tokens` feeds both the
summarization trigger and the eviction threshold.

**The standing gap: nothing measures system prompt growth *as a whole*.** Skills,
facts and the merged planning methodology are a fixed cost paid on every call, and
no mechanism here trims them. Two parts are priced individually (`recall_chars`,
`tree_chars`); the gap is the *total*. Summarization compacts the *conversation*;
the prompt is rebuilt in full each time.

**The compaction count is tool-driven only** — deepagents' automatic
summarization fires without passing through any Rudra middleware, so
`usage.json`'s `compactions` must not be read as "every time context was shed".

**Continuity is the ledger, not the transcript.** `rudra --continue` works the
remaining tasks from `.rudra/run/ledger.json` and never replays a conversation.
The checkpointer stays regardless: interrupt-and-resume approval depends on it.

**`/compact` is not a REPL command** — the REPL builds a fresh agent per input, so
context does not accumulate between turns.

---

## 6. Configuration Design

TOML config, vendored superpowers, MemPalace Python API, permission mode `ask` by
default with an `--auto` escape hatch, minimum local model 32B, `VirtualFileSystem`
deleted. Owner decisions are in `TODO-old.md` §0.

**Layered TOML, later layers override earlier:**
1. Built-in defaults (shipped in package)
2. `~/.config/rudra/config.toml` (user global)
3. `<project>/.rudra/config.toml` (project)
4. Environment variables (`RUDRA_*`)
5. CLI flags

**Every section is live:** `[model.*]`, `[agent]`, `[permissions]`, `[compat]`,
`[tools]`, `[skills]`, `[mcp]`, `[memory]`, `[telemetry]` (Langfuse keys are the
switch). Nothing is reserved — `RESERVED_SECTIONS` is `{}`. MCP *servers* live in
`.mcp.json`; the `[mcp]` section is about which of them Rudra uses. Unknown keys
are fatal and suggest the nearest valid name.

`[tools]` carries `shell`, `shell_in_auto`, `auto_branch` and `test_timeout`.

`rudra init` scaffolds the file; `rudra config list` shows every effective value
and the layer that set it. There is no `rudra config set`.

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
verbose = false                         # prose + untruncated payloads in the trace
stream_tokens = false                   # stream that prose token by token; --stream for one run
max_fix_attempts = 3                    # fix-loop retries per task, per RUN
max_questions = 5                       # clarification budget for the run; 0 never asks
max_invocation_seconds = 1200           # runaway bound in SECONDS
run_archive = true                      # copy the run's evidence out of the project

[tools]
shell = true                            # false removes the execute tool
shell_in_auto = false                   # may --auto run commands?
auto_branch = false                     # branch before a run?
test_timeout = 600                      # seconds before a test run is killed

[permissions]
mode  = "ask"                           # ask | auto | plan

# Rules are "tool" or "tool:pattern", using REAL tool names:
#   read_file  ls  glob  grep  write_file  edit_file  delete  execute  task
# A pattern is matched against THREE spellings of the path -- the model's own,
# the project-relative one, and the resolved host one. For execute it matches
# each shell segment of the command. deny beats allow.
#
# Paths are VIRTUAL. Every backend is virtual_mode=True, so the model's
# "/src/app.py" is <project>/src/app.py and its "C:\x\y.py" is <project>/x/y.py --
# the host's files are never reachable through the file tools, on any OS.
# `compat/virtual_paths.py` is the single function that answers this.
allow = ["execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *", "write_file:.env"]

# Built-in rules denied in EVERY mode, including --auto. Name one to switch it
# off; the run says so, and calls it would have blocked are still audited.
#   git-dir · rudra-state · catastrophic-command
# ("outside-root" is rejected here: the backend confines writes, not this rule.
#  It fires only on a REAL escape -- ../.. traversal, or a symlink inside the
#  project pointing out -- never on a virtual absolute path.)
floor_disable = []

# Reserved, not yet accepted:
#   [skills]    [memory]
```

**MCP servers in a separate `.mcp.json`, Claude-Code-compatible schema:**
```json
{ "mcpServers": { "mempalace": { "command": "mempalace", "args": ["mcp"] } } }
```
Loaded via `langchain-mcp-adapters` → tools handed to `create_deep_agent(tools=[...])`.

**API keys may be written in TOML, and are never displayed.** `ModelConfig`
carries a literal `api_key`, checked by `_resolve_api_key` before `api_key_env`
(`llm/factory.py:50`). A key may be **stored** and is never **shown**: `api_key`
is declared `field(repr=False)`, so it cannot reach a repr, log line or traceback;
`cli.py::_safe_value` masks it in `config list`.

**A user's project is configured by `.rudra/config.toml`, not by a `.env`.**
`rudra init` writes that file and no `.env`; it carries everything a `.env` would.
`.env` remains a supported layer-4 input, but nothing asks a user to create one.
`~/.config/rudra/config.toml` is a home-directory file in nobody's repo — a key
there is fine. `<project>/.rudra/config.toml` is the file `README.md` tells users
to commit, so `committed_api_key_notice` warns once per run when the key came from
that layer — a warning, not a refusal. See §9 for why this distinction matters.

---

## 7. Hard-Won Knowledge (don't relearn)

- `install_path_normalizer` (`compat/deepagents_path.py`) monkeypatches `validate_path` in **three** modules — `deepagents.backends.utils`, `deepagents.middleware.filesystem`, and `deepagents.middleware._fs_interrupt` — because each does `from ... import validate_path` and resolves the name in its own globals. `_fs_interrupt` is reached only when `permissions=` is passed, which Rudra never does — it is patched so U.7 does not reopen onto a half-applied file. `pyproject.toml:30` pins `deepagents==0.7.4` exactly for this.
- `OverwriteFilesystemBackend` **no longer exists** — `tests/test_agent_wiring.py::test_main_agent_constructs_filesystem_backend_with_virtual_mode` (AST check) fails if `main_agent.py` ever constructs one again. It was deleted with the 0.7.4 upgrade, whose `write()` overwrites by default; its markdown-fence stripping lives on in `middleware/fix_write_params.py`.
- The 6 middlewares are patches for qwen3:14b failure modes documented in their own docstrings. Minimum target model is now **32B** → 4 of 6 were deleted; the 2 survivors are opt-in behind `[compat]`, default off. Disposition table: `TODO-old.md` §0.1. `FixWriteParamsMiddleware` is split: fence-stripping and `filename`/`path` → `file_path` aliasing always on, sandbox-prefix stripping behind `[compat] sandbox_paths`. Its four refusals are always on and are not repairs — this middleware refuses a call or fixes its parameters, and never rewrites a body into something plausible.
- **Configuration precedence lives in exactly one function**, `config/loader.py::deep_merge`, and every value records the layer that set it. Do not reintroduce per-field resolution.
- **Role inheritance runs after the cross-layer merge**, not inside a layer.
- Retired planning material lives in `docs/archive/`, which is **gitignored**.

---

## 8. Commands

`Documentation/07-development.md` owns the development workflow; when it and this
section disagree about the workflow, that file wins. This is the agent-facing
cheat sheet for *driving Rudra*.

**Contributions: pull requests are not accepted** (`CONTRIBUTING.md`). Bug reports
and feature requests are welcome as issues; security problems go privately via
`SECURITY.md`. Do not propose a PR-based workflow, and do not add CI to gate one.

```bash
.venv/bin/ruff check src/ tests/     # must print "All checks passed!" — absolute gate
.venv/bin/ruff format --check src/ tests/
uv run pytest -q                     # must never go down; see Documentation/07-development.md
git config core.hooksPath .githooks  # once per clone: run all three gates on push
.venv/bin/rudra --version            # Rudra v0.2.0

.venv/bin/rudra init                 # scaffold .rudra/config.toml + the D15 layout
.venv/bin/rudra config list          # every effective value + which layer set it
.venv/bin/rudra doctor --offline     # diagnose config, layout, deps; --offline skips network
.venv/bin/rudra models test          # verify each role is reachable and can call tools
.venv/bin/rudra memory list          # what this project has learned, and who recorded it
.venv/bin/rudra memory search "..."  # by meaning; `forget` prunes, `export` keeps
.venv/bin/rudra verify               # deterministic gate over the changed files
.venv/bin/rudra verify --all --json  # whole project, machine-readable. Exit 0/1/2
.venv/bin/rudra "build a flask app"  # single-shot; prompts before each write and command
.venv/bin/rudra --auto "..."         # unattended: files yes, commands no
.venv/bin/rudra --verbose "..."      # ...and the model's prose, untruncated
.venv/bin/rudra --no-debug "..."     # skip .rudra/run/logs/debug-<id>.jsonl (on by default)
.venv/bin/rudra log --last           # replay a past run from its transcript
.venv/bin/rudra --auto --allow-shell "..."   # ...and commands too, opted in explicitly
.venv/bin/rudra --plan "..."         # show the plan and stop — writes nothing
.venv/bin/rudra                      # REPL
```

**Ctrl-C stops at a task boundary.** SIGINT cancels the run's asyncio task;
`work()` catches `CancelledError` between tasks, returns the in-flight task to
`PENDING`, saves the ledger, and skips the reviewer and the AGENTS.md summary
(both model calls). Single-shot exits **130**. `PENDING` and not `IN_PROGRESS` is
the whole point: `Ledger.resumable()` excludes `IN_PROGRESS`, so the cancel path
and the resume path are the same path.

**`mode = "ask"` needs a TTY.** Piped or redirected stdin exits 2 before any model
call. Use `--auto` for unattended runs.

**Unattended shell is opt-in.** `--auto` alone runs the filesystem tools only,
which the backend genuinely confines to the project via `virtual_mode=True`.
`execute` is denied under `--auto` unless the user says so with `--allow-shell` or
`[tools] shell_in_auto = true`. The reason is measured: the floor's path rules
cover `write_file`/`edit_file`/`delete`, but a shell command can write anywhere
the user can (the Step 7 acceptance run watched the model, denied twice on
`write_file`, run `echo "hello" > /abs/path` and succeed).

**`--auto` alone runs no tests.** `run_tests` is gated as `execute`, so an
unattended run cannot verify its own output unless shell is opted in. A
loop-engineering run wants `--auto --allow-shell`.

**`rudra verify` follows the same rule.** Its lint, typecheck and test stages are
commands, so under `--auto` without `--allow-shell`, and under `mode = "ask"` with
no terminal, they report `denied`, the verdict escalates, and the command exits 2.
Only Python's `ast.parse` syntax check and the stub scan always run. An explicit
`allow = ["execute:pytest*"]` counts as opting in — **but only for a command an
agent TYPES as `pytest …`**: a permissive rule is matched as written, and every
command a Python project's gate runs is an absolute path, so a rule for one names
it as `permissions.jsonl` records it. `Documentation/06-troubleshooting.md` says
so, and `tests/test_docs_permission_examples.py` resolves real commands against
its rules.

**One write outside the project is refused whatever the mode: `pip install` into
an interpreter that is not a virtualenv.** Every command's environment carries
`PIP_REQUIRE_VIRTUALENV=1`, so pip exits 3, and for a Python project Rudra builds
and fills the project's own `.venv` itself. It is pip's switch, so
`pip install --isolated`, `setup.py install`, conda and non-pip installers are not
covered.

---

## 8a. Debuggability Is a Product Requirement

**The support story is the whole story.** Rudra ships as open source with no CI,
no telemetry, and no accepted pull requests — so when a user says *"it is slow"*
or *"it wrote the same file four times"*, the **entire** diagnostic channel is:
*send me `.rudra/run/logs/`*, read on a different machine, against a project the
maintainer does not have, weeks later, with no ability to re-run anything.
`state/archive.py` copies that folder out so it outlives the project.

**The rule, a design constraint on new code:**

> **If a number would answer a user's complaint, the run must WRITE that number.
> A number that exists only in memory, only in a console panel, or only at run
> end, does not exist.**

### The four failure shapes

1. **The number is computed and then dropped.** A guard maintains a count for its
   bound and discards it at every `return`; middleware measures a duration and
   folds it into a total. Known at the exact moment it mattered, never reached disk.
2. **The number is written only at the end.** A run taking too long has not ended,
   so a file written from `close()` does not exist yet. **Any record whose subject
   is a run in progress must be written while the run is in progress.**
3. **The number must be derived rather than read.** A log the maintainer has to
   write a parser against is a log that does not get read.
4. **The record is one field and the field gets overwritten.** `halts` and
   `run_errors` are separate fields from `note` for this reason. **Diagnostic
   state is append-only or it is not state.**

### What this means when you write new code

- **A guard that stops something must log what the something spent.** "It halted"
  is a symptom; "it halted after 55 tool calls, 41 of them `glob`" is the
  diagnosis — which is why `log_invocation` carries a `tools` histogram.
- **A failure costs time too.** `_record_failure` logs `ok: false` with the
  exception type, because a provider 500 costs the full wait and buys nothing.
- **Route it through the `rudra.*` logger tree.** `trace/debug.py` captures that
  tree into `debug-<id>.jsonl` and `_JsonLines.format` promotes
  `extra={"event": {...}}` to the top level — so a new diagnostic record needs no
  new sink, no new file and no new config key. Give it a `kind` and name that
  string as a module constant.
- **A record is not a `TraceEvent` unless it describes something that was DONE.**
  `TraceKind` is the vocabulary for actions; `model_call` and `subagent_done` are
  measurements and sit beside it. Do not widen `TraceKind` to hold timings.
- **Bookkeeping may never end a run.** Every writer here swallows its own failure.
- **Never put a secret in a diagnostic.** Redaction happens where the event is
  built, not where it is rendered; `archive.py` records the models a run used and
  never `config.toml`. A new record is going to be pasted into a public GitHub
  issue.

### The reader's checklist, for the folder a user sends you

`.rudra/run/logs/` — or `$XDG_STATE_HOME/rudra/runs/<project>/<run>/` when the
project is gone:

| Question | File | How |
|---|---|---|
| How long, really? | `usage.json` | `run.wall_seconds`, and `suspended_seconds` before you believe any other clock |
| Which role burned it? | `usage.json` | `roles.<role>.seconds / calls`. If ~100% of `counted_seconds`, the run is model-latency bound and the only lever is **fewer calls** |
| Is the provider healthy? | `usage.json` | `(retries + exhaustions) / calls`, both, never `retries` alone. The AGENTS.md summariser is outside `usage.json` — its retries are `notice` lines named `retry`/`retry-exhausted` with `role: "summariser"` |
| Which call was slow? | `debug-<id>.jsonl` | `"kind": "model_call"`, including the summariser's as role `summariser` |
| Which invocation was the runaway? | `debug-<id>.jsonl` | `"kind": "subagent_done"` — sort by `seconds`, then read `tools` |
| What was it thinking? | `debug-<id>.jsonl` | `"kind": "tool_call"` / `"ai_text"`, uncapped. With `--stream`, `ai_text` is one record per LINE — join consecutive records of one role+namespace |
| What did Rudra stop, and why? | `debug-<id>.jsonl` | `"kind": "notice"` — `guard`, `retry`, `suspended` |
| Which task, how many tries, how long? | `ledger.json` | `seconds`, `attempts`, `halts`, `run_errors`, `files_touched`, `convergence`, `dependency_gates`, `unblocks` |
| Did the gate pass, and on whose interpreter? | `verify.log` | the `command:` line of each stage. A Python venv's test command reads `<venv>/bin/python -m pytest` |
| Was anything denied? | `permissions.jsonl` | one line per gated decision. A denied `arg` is the exact string an `allow` rule is matched against — for a Python project an absolute path |
| Which Rudra, Python, mode? | `meta.json` | written at run start |
| What did the provider actually say? | `debug-<id>.jsonl` | `"kind": "model_call"` with `"ok": false` → `error_detail` and `traceback`, redacted |
| Did the run die during SETUP? | `debug-<id>.jsonl` | the log is opened before the skills cache, backend, gate, `.mcp.json`, sqlite and checkpointer. A debug log holding only a traceback died in one of those |
| Is Langfuse configured? | `debug-<id>.jsonl` | `"logger": "rudra.telemetry*"`; `Langfuse tracing → <url>` on the console says it started |
| Guard halts, repeats, nudges, path confusions, refused writes/plans/routes, pip refusals, dependency refunds, `.venv` cost | `usage.json` · `debug-<id>.jsonl` · `ledger.json` | each guard writes a per-role counter in `usage.json` and a matching `"kind": "notice"` line naming the path/tool/command. **A false positive on any refusal counter is a REFUSED REAL WRITE — check the first path by eye** |

**`files_touched: []` on a `DONE` task is the single highest-signal line in that
folder.** It means a coder invocation was dispatched, paid for, and wrote nothing.
Read the plan next, not the loop. A `DONE` task with `[]` may also be one that
passed over a gate already red when it began — its note says so and quotes the
failure; read the task that caused it.

**The console's own counterpart is the headline's `unfinished`.** A run that
stopped names the task it stopped on, `· 1 unfinished`, apart from the tasks after
it, which are `never attempted`; that task's line carries the `run_errors` sentence
or the halt that ended the run.

---

## 9. Repo Hygiene Facts

- `.env` and `.rudra/` are gitignored.
- **Two different folders, and confusing them wastes a session. Read this before testing Rudra on anything.**

  | | **This repo** | **A user's project** (anywhere else) |
  |---|---|---|
  | What it is | Rudra's own source | The folder a user builds *their* app in |
  | Configured by | `.env` at the repo root | `rudra init` → `.rudra/config.toml` |
  | Holds creds? | **Yes** — the dev backend's keys | No. `config.toml` does this job |
  | Do we touch it? | **No.** Leave `.env` exactly as it is | This is what you test against |

  `.env` is Rudra's **development** config, read by `config/loader.py:459` as
  layer 4. It is **not** how a user configures a project. **Never copy this repo's
  `.env` into a test project** — configure the test project the way a user would
  (model settings in its own `.rudra/config.toml`, `api_key_env` naming a variable
  you export). A `.env` in a test project is not a realistic setup, and reasoning
  from its presence produces false findings.

- `LICENSE` is present and the suite is green (`Documentation/07-development.md`
  owns the count). **There is no CI, and none is planned** — `CONTRIBUTING.md`
  accepts no pull requests, so a PR-triggered gate would guard nothing.
  `.githooks/pre-push` is the only gate; `--no-verify` or a clone that never set
  `core.hooksPath` is checked by nothing. `CONTRIBUTING.md` and `SECURITY.md` are
  present.
- **A local `.venv` drifts from `uv.lock` and will lie to you.** Run `uv sync`
  before believing a local failure, and prefer `uv run` — which reconciles first —
  when the answer matters.
