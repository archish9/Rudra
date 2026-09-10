# CLAUDE.md — Rudra

Context loaded into every fresh Claude Code session. Read `TODO.md` next — it
is the live ledger of what is open. **Three are open as of 2026-09-09:
OPEN-103, OPEN-104 and OPEN-105**, all filed by the live run `f845b496a2aa`
that day. OPEN-99 … OPEN-102 and the whole 2026-09-04 board — OPEN-93 …
OPEN-98 — closed the same day; each has a self-contained document under
`docs/superpowers/plans/`, carrying a closing section saying what shipped and
where it deviated from the plan. Trust `grep -n PENDING TODO.md` over any
heading, including this sentence.

**The board was empty for about an hour**, which is how long it took to run a
model against the code. That is the pattern, four rounds running, and it is
the most useful thing this file can tell a fresh session: **offline closure
empties the board and tells you nothing about the model.**

**The verification run the 2026-09-04 board called for happened
(`5775ba1f9855`, 2026-09-06), and its tables are now on the 2026-09-06 board
in `TODO-closed.md`.** Read them before believing any of those six is
verified: the run exercised **two** of nine fixes (OPEN-96 and OPEN-98's
record), and the other seven produced zeros **because no occasion arose** —
the model behaved. A run where nothing goes wrong cannot verify a guard, which
is `TODO.md` watch item 2 in its most expensive form.

**Then a second live run happened, `d8f742805b9b` on 2026-09-09, and it is
the more important one.** Request: *"write a single HTML file for iphone 15"*.
Result: **zero files, zero tasks, 1092 s, ended by the user pressing Ctrl-C.**
It found three defects — OPEN-100, OPEN-101, OPEN-102 — **and none of them was
in the nine fixes it was meant to exercise.** The planner spent 42% of the
run's model time generating a document for a `write_file` tool it correctly
does not have, inside a stage with no call cap and no time cap at all — and
raised an approval panel for that same absent tool, which the user answered
with `!`, granting `approve_all` for the session.

**Read that as the standing answer to "is offline verification enough".** Both
live runs found defects nobody had predicted, in places no closed item
covered, and the seven fixes still waiting on a model are still waiting.

**Then a third run, `f845b496a2aa`, the same day, with a prompt built to make
the model fail — and it found three more.** 11 tasks planned, **0 done**, 1
blocked, 2737.3 s. The coder called a tool named `bash` fifteen times, was
answered each time with langgraph's bare tool list, and **3 of its 4
invocations died on the consecutive-failure guard** (OPEN-103 — OPEN-100's
finding one agent down, and the coder correctly has no `execute`). It wrote a
`COMPLETION` file into the project to announce it had finished, which is
OPEN-42 live again four runs later (OPEN-104). And the run18 checklist reported
`NO ARCHIVE` for a run that was archived perfectly (OPEN-105). **Two of
OPEN-100's bounds fired live for the first time**, which is the good news, and
OPEN-92 is *still* unverified — one `edit_file` call in the whole run, because
the coder kept dying first.

**Still outstanding, and it is the highest-value thing available:** seven of
the nine 2026-09-03/04 fixes have never been seen acting on a model. OPEN-92
is the sharpest — run `5775ba1f9855` made **zero `edit_file` calls**, so the
gutter repair had nothing to repair, and OPEN-99 was the reason (no
collectable test → no failing gate → no fix loop). **That blocker is gone**;
what the next run still needs is a prompt forcing a multi-file plan and a
failing first attempt. Nine follow-ups the closed items named and did not take
are recorded in `TODO.md` — OPEN-97's Options B and D, OPEN-98's Option B,
OPEN-100's Options A and B, OPEN-101's three, and OPEN-68's missing
`verify.log` copy, with OPEN-99's Option D as a tenth — as candidates for
filing, not as open work. *Corrected 2026-09-09: this said six, and `TODO.md`'s
own heading said eight over a table of nine.*

Corrected 2026-09-09, four times in one day: this paragraph said *"One is open
as of 2026-09-06: OPEN-99"*, then *"Nothing is open"*, then *"One is open:
OPEN-102"*, and now *"Nothing is open"* again. The live-run verification it
inherited from the 2026-09-06 correction, which inherited it from the
2026-09-04 one, **is still outstanding after every one of them** — and the
board being empty again is precisely when that is easiest to forget. For four
rounds every item was closable offline, and the first two runs that actually
exercised a model produced four new items between them. **Offline closure is
not evidence about a model; it is evidence about the code.**

**One more caution, and OPEN-100 is where it was learned:** a plan document's
recommended *number* is not evidence either. OPEN-100's own plan said a
40-call cap would halt the runaway stage "at roughly call 20-25"; measured
against the archive, a healthy planner stage runs to 9-22 calls and the
runaway one reached 20, so neither shipped bound would have fired before the
user did. **Check a plan's arithmetic against the archived run before
implementing it** — these documents are written carefully and are still
written from memory of the log, not from a re-read of it.

**One more round happened on 2026-09-10, and it is the fifth for five.**
OPEN-106 … OPEN-110 were filed and closed the same day, from an *audit* rather
than a run: *can the folder an end-user sends explain their run?* No, five
ways — `logs/` carried no version/platform/mode record, the archive carried
neither `verify.log` nor `permissions.jsonl`, a setup-phase crash left no
debug log at all, a failed model call recorded `type(exc).__name__` and
nothing else, and a run could only ever be *mailed* rather than *seen*.
`[telemetry]` is a live config section now, `langfuse` is a dependency, and
`src/rudra/telemetry/` sends one trace per run when a user configures keys.
**Every one of those five was closable offline, which is what the paragraph
above is about** — they say nothing about how a model behaves.

**Three ledgers, and they are not interchangeable.**

| File | Owns |
|---|---|
| `TODO.md` | **What is open now, and nothing else.** Read it first. **OPEN-103 … OPEN-105 open as of 2026-09-09** — it opens with the census, then what closing OPEN-99 did and did not buy, then the nine unfiled follow-ups the closed items named, then the 2026-09-09c board carrying run `f845b496a2aa` and its exercised / not-exercised tables, then the 2026-09-09b board with every row struck through. Then four things being watched that are deliberately **not** items, the fourth being the live-run verification still outstanding across OPEN-90/91/92. Below that is the operational half a fresh session needs whatever it works on: the test baseline, how to configure a throwaway project, what the provider is doing, where a run's evidence lands, and four lessons that keep being re-learned. Trimmed on 2026-09-09 when the 2026-09-06 board moved out, and again as OPEN-100 … OPEN-102 closed; the run records and ordering arguments went with them |
| `TODO-closed.md` | Every `DONE` and `WONTFIX` item — **now all 102 of them**, plus the 75 `CR-*` code-review findings of 2026-08-21, each with its reproduction. Split out 2026-08-25 and emptied out of `TODO.md` in rounds since. Four blocks have their own header: the **2026-09-01** one carries all eleven run records and both ordering boards; the **2026-09-03** one carries OPEN-72 … OPEN-83 (two rounds of 2026-09-02) and OPEN-87 … OPEN-92; **the 2026-09-03 board itself**, moved here 2026-09-04, which carries run `fc543fb2b82f`'s final numbers and the correctness-before-cost ordering argument; and **the 2026-09-06 board**, moved here 2026-09-09, which carries run `5775ba1f9855`'s numbers and its **exercised / not-exercised tables** — the record of what a verification run can and cannot prove. **OPEN-100, OPEN-101 and OPEN-102 sit at the end**, each moved there the day it was filed; OPEN-100's closing carries the per-stage call and seconds table measured across every archived run — the evidence that its own plan's recommended number was wrong. Its index table at the top is the map; read that before scrolling |
| `TODO-old.md` | The Steps 0–16 build ledger: **§0 (locked decisions D1–D19), §0.1 (middleware disposition), §E (execution order), §F (deepagents 0.7.4 findings)** and every `A*`/`C*`/`S*`/`U*` item this file cites |

When a reference below says §0, §F, U.7 or A1.46, it means `TODO-old.md`.
When it names an `OPEN-N`, it means `TODO-closed.md` — every `OPEN-N` cited
in *this* file is closed, so a citation here is a pointer to a record rather
than to work outstanding. **`TODO.md` is where the open ones are**, and as of
2026-09-09 those are OPEN-103, OPEN-104 and OPEN-105 — cited in this file only
in the run paragraph above; every other `OPEN-N` here points at a record. When
the next item lands, its own document's closing section says which paragraphs
here it corrects.

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

1. **Read `TODO.md` at the start of every session** — it holds only what is open, and opens with the order to work it in. As of 2026-09-09 OPEN-103, OPEN-104 and OPEN-105 are open, on the 2026-09-09c board; OPEN-103 is the run-killer and everything else in that run is downstream of it. What the file opens with is the census, then what closing OPEN-99 did and did not buy, then the nine unfiled follow-ups, then the two 2026-09-09 boards. Run `5775ba1f9855`'s results — which two fixes it exercised and which seven it left silent — moved to `TODO-closed.md` with the 2026-09-06 board. When there is more than one item, **the board's order can be a dependency rather than a preference** — on the 2026-09-03 board OPEN-90's fix would have destroyed OPEN-92's live reproduction, which is why OPEN-92 went first. For a closed item's reproduction read `TODO-closed.md`; for historical decisions and step order read `TODO-old.md` (§0 = locked decisions, §E = execution order, §F = deepagents 0.7.4 findings). See the table at the top of this file about which ledger owns what.
2. **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with file:line evidence first. Then fix it. Then mark `DONE`. This ordering is non-negotiable — the owner asked for it explicitly.
3. **Evidence-based only.** Every claim about the codebase must cite `file.py:line`. No assumptions, no guessing. If you cannot verify, say so.
4. **Verify before claiming done.** Run the command, show the output. `ruff check`, `pytest`, actual CLI invocation.
5. **Planning sessions produce self-contained implementation plans** that a fresh session can execute without this conversation's context. Write them under `docs/superpowers/plans/`, which is where the existing ones live — `plans/` was named here for a directory that has never existed (CR-DOC6).
6. When done with a work item, update `TODO.md` in the same commit as the code.
7. **Every diagnostic number a run produces must be written to
   `.rudra/run/logs/`.** Rudra's only support channel is a user emailing that
   folder, so a number held in memory, printed to a console panel, or written
   only at run end cannot be used to answer anybody. Read **§8a** before adding
   code that measures, guards, retries or halts anything.

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
│                           Blocking except lint — and except typecheck when
│                           it ran RUDRA's bundled mypy, whose verdict is a
│                           function of Rudra's own site-packages and so
│                           differs machine to machine (OPEN-38). A project
│                           that installs mypy into its own .venv gets the
│                           blocking verdict back. The resolution declares
│                           this, not the stage: only resolve_typecheck_command
│                           knows which mypy it picked. Six outcomes, not two.
│                           VerifyReport.escalate splits fix-loop input
│                           (iterate) from user action (stop). Calls
│                           run_gated and run_tests; starts no subprocess
├── subagents/              The four subagents (Step 9b): coder · tester ·
│                           reviewer · general-purpose. spec/registry/build/
│                           runner. `run_subagent` gives an invocation ONE
│                           extra turn when its final message announced a
│                           next action rather than a result (OPEN-98):
│                           langgraph ends the loop on an AIMessage with no
│                           tool calls, `_FINISH_RULES` teaches exactly
│                           that, and the signal carries no INTENT — "I am
│                           done" and "I have noticed a mistake and am about
│                           to fix it" are the same message. Run
│                           2cde3406f7d6's tester said *"Wait, I made an
│                           error. The file path should be relative to the
│                           project root, not absolute. Let me check the
│                           project structure"* — OPEN-93, diagnosed by the
│                           agent that made it, 1.3 s after committing it —
│                           and the turn ended. No prompt is added and none
│                           could be: the model was not disobeying.
│                           `announces_continuation` judges the FINAL
│                           SENTENCE only, requires it short enough to be a
│                           hand-off, and matches first-person future-tense
│                           openers after stripping discourse lead-ins;
│                           "let me know" is a sign-off, and "actually," is
│                           deliberately not an opener. The nudge is ONE,
│                           from one call site, AFTER the guards — a halted
│                           invocation is never handed another turn — and it
│                           shares the invocation's budget, so `seen`,
│                           `total_calls` and the time limit all carry
│                           across. `subagent_done` gains `nudged` and
│                           `nudge_outcome`, and the second is the number
│                           that judges the heuristic. build.py is the ONLY assembly path, so
│                           every subagent carries the gate — deepagents
│                           inherits interrupt_on but NOT middleware.
│                           The reviewer cannot write because the tools are
│                           never registered, not because a prompt says so.
│                           `_prompt_for` is where a spec meets the project:
│                           facts, then the `## PROJECT FILES` listing
│                           (`wants_tree`, coder and tester only), then
│                           recalled memories, then the MCP catalog. The
│                           listing is rebuilt on EVERY invocation and never
│                           cached — these agents write the files they are
│                           being shown, so a carried-over listing is a
│                           correctness bug, not a saved call (OPEN-39)
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
│                           regressions · decomposition · tools · engine.
│                           ledger.py, bounds.py, regressions.py and
│                           decomposition.py import nothing from
│                           Rudra. regressions.py answers the loop's hardest
│                           question — which failures a task answers for
│                           (OPEN-23) — and is worth reading without a graph. No agent-facing tool can write DONE — only
│                           engine.py, and only on VerifyReport.passed or a
│                           failure that predates the task (regressions.py).
│                           decomposition.py is the SHAPE of a task list
│                           (OPEN-90): run fc543fb2b82f planned one HTML
│                           page as eight tasks, seven of them naming a
│                           region of the same file, and the six coders
│                           dispatched after the first wrote the whole
│                           file touched nothing — 4,599.8 s, 63% of the
│                           run. `_BREAKDOWN_BODY` had already forbidden
│                           two of those eight in prose and lost, which is
│                           OPEN-17's rule again, so the rule is Python:
│                           `add_tasks` refuses a list of 3+ non-test
│                           tasks when a FACT VALUE says the deliverable
│                           is one file. It reads values, never key names
│                           — the store enumerates its keys nowhere in
│                           code — and vetoes on the two shapes that read
│                           the same by accident: "one file **per**
│                           module", and "a single **test** file". Facts
│                           naming two distinct non-test filenames veto
│                           outright, because that project is not one
│                           file whatever a phrase elsewhere says.
│                           **All-or-nothing where the duplicate check is
│                           per-task** — the defect is the list's shape,
│                           so there is no good half to keep — and
│                           refusable exactly ONCE per run
│                           (`Ledger.decomposition_refused`, deliberately
│                           not persisted), because a guard that refuses
│                           for ever turns a bad plan into no plan.
│                           Beside it, `forbidden_shape_refusal` is
│                           per-task and kills the two shapes that prompt
│                           quotes as BAD: a task that only checks what
│                           the gate already checks, and "create the
│                           project structure". Narrow on purpose —
│                           "validate user input in the signup form"
│                           writes a file and survives, because a false
│                           positive here is work that never happens
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
│                           printed raw through Rich loses anything in brackets.
│                           Every kind but NOTICE describes something the MODEL
│                           did; NOTICE is what Rudra says about ITSELF — a
│                           subagent guard halt (OPEN-44, name="guard") and a
│                           retried model call (OPEN-45, name="retry"). Emit
│                           one through TraceSink.notice, never by hand: it is
│                           the only path that redacts. It renders at VERBOSE and
│                           reaches the debug log at every level, so anything
│                           the user must ACT on is printed by the code that
│                           decided to act, the way loop/engine.py prints a halt
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
│                           plus memory_prompt.py (OPEN-70): Rudra's
│                           replacement for deepagents' MEMORY_SYSTEM_PROMPT,
│                           which spends ~1,280 tokens per planner call
│                           teaching an agent with NO write tools to persist
│                           memory by calling `edit_file`. Reached through
│                           `system_prompt=` plus replace-by-name — the same
│                           public seam FilesystemMiddleware uses (A1.47),
│                           NOT a monkeypatch. The trust guidance is kept
│                           (AGENTS.md is partly model-written); the write
│                           orders go. OPEN-66 tried to fix this with a line
│                           in AGENTS.md's body and lost, because upstream's
│                           own prompt marks that body untrusted data,
│                           plus repeat_guard.py (OPEN-10, always on): an
│                           agent may not re-run a READ that already failed
│                           twice with identical args and nothing in between.
│                           execute is excluded — a command can legitimately
│                           succeed on retry, and guarding it would turn a
│                           token cost into a correctness bug. THREE rules
│                           since OPEN-60: a WRITE whose bytes are already
│                           on disk is refused too, and that one is about
│                           the POST-CONDITION rather than the answer —
│                           the bytes asked for are the bytes present.
│                           It keys on the file, not the spelling
│                           (compat/virtual_paths.py, OPEN-52), so it
│                           agrees with the gate, the approval preview and
│                           the backend about which file a call touches.
│                           Its write-belief lives in TWO places and they
│                           are different claims (OPEN-62): the instance
│                           map is what THIS invocation did and watched, so
│                           it refuses on its own authority and any
│                           file-changing call drops it; RunUsage's is what
│                           an EARLIER invocation did, across a boundary
│                           this middleware sees nothing of, so it is never
│                           invalidated and refuses nothing until the file
│                           on disk is read and holds exactly those bytes.
│                           **No refusal is a tool failure, and since
│                           OPEN-94 that is a FIELD rather than a spelling
│                           convention.** Two of the three led with
│                           "Already read:"/"Already written:" to dodge
│                           `trace/stream.py`'s first-line check; the third
│                           leads with "Error:" because that is what the
│                           MODEL must read, and `subagents/runner.py`
│                           counted it — run 2cde3406f7d6's first coder
│                           died on 3 consecutive failures at 11.67 s
│                           having written nothing, two of them this
│                           middleware's own text. `_as_message` is the one
│                           seam all three are built at and it sets
│                           `REFUSAL_KEY`; `message_is_error` answers from
│                           that before any text, so the subagent counter,
│                           the planner counter and the renderer agree for
│                           free. At both counters a refusal is **no
│                           event** — it neither increments nor CLEARS,
│                           because clearing would let a free
│                           short-circuit separate two real failures that
│                           should have met. OPEN-94 moved only the
│                           classification and left all three texts
│                           byte-identical, so that **OPEN-95 could own
│                           the failure one — and it rewrote everything
│                           after the first word.** That refusal names the
│                           RESOLVED target now, the same one the notice
│                           beside it prints, and says nothing about the
│                           arguments: the signature keys on the file, not
│                           the spelling (OPEN-52), so "these exact
│                           arguments" was a claim the model could see was
│                           untrue — run 2cde3406f7d6's coder was told it
│                           had already called `ls 'src'` twice when it
│                           had typed `/src` and the full host path, and
│                           re-sent the call verbatim. `_spelling_note`
│                           supplies what was missing, the EXPLANATION of
│                           the identity, and declines in the three cases
│                           where the claim would be false: no project
│                           path, a bare `grep` pattern, an unplaceable
│                           route. The leading `Error:` stays, and is free
│                           to, precisely because OPEN-94 took the
│                           classification off it,
│                           plus gutter_indent.py (OPEN-92, always on):
│                           `read_file` renders `f"{marker:>{w}}  {line}"`
│                           (backends/utils.py:243), so a model composing
│                           `old_string` from what it was SHOWN carries the
│                           two-space gutter in as indentation — every line
│                           +2 against the file — and `edit_file`'s exact
│                           `content.count` (utils.py:523) can never match.
│                           Measured: 7 of run fc543fb2b82f's 7 coder edits,
│                           0 bytes changed, 2 invocations killed by the
│                           repeat guard. **It fails exactly when an agent
│                           edits what it did not just write** — every
│                           fix-loop retry, every task after the first,
│                           every `--continue`; the tester's edits in that
│                           same run SUCCEEDED because it was editing its
│                           own `write_file` content. The repair does NOT
│                           dedent the model's string — measured, that fixes
│                           0 of the 7, because the file's block is indented
│                           too. It anchors on stripped line content, and
│                           the corrected `old_string` is the FILE's own
│                           bytes, which match by construction. Every
│                           uncertain case declines and passes the call
│                           through untouched: already-matching, one line,
│                           no unique window, a non-uniform delta. It only
│                           ever turns a certain failure into a success,
│                           never a failure into a different one. When it
│                           declines but the text IS in the file more than
│                           once it answers instead of running the tool,
│                           because upstream's message is the model's own
│                           argument echoed back and a model given that
│                           re-sends it verbatim until the guard kills it,
│                           plus machine_paths.py (OPEN-91, always on):
│                           every backend is `virtual_mode=True`, so a
│                           model's `/usr/bin/python*` is
│                           `<project>/usr/bin/python*` and can only ever
│                           answer `No files found` — a SUCCESSFUL result
│                           that reads as *look somewhere else*. Run
│                           fc543fb2b82f's t7 did, 36 times over 2,704 s,
│                           hunting an interpreter for a shell it does not
│                           have. Two prompts already said so and both lost
│                           (OPEN-17's rule, third time), so the fix is what
│                           the TOOL says: the explanation is APPENDED to
│                           the empty result — never prepended, because
│                           `runner.py`'s failure counter and
│                           `repeat_guard._is_error` both key on a leading
│                           `Error`. It fires only where the tool found
│                           NOTHING, so a project that really holds
│                           `usr/bin/` is answered with its own files, and
│                           it names the tool the agent actually has: no
│                           shell means "the gate runs the tests when you
│                           stop", `execute` means "that is the tool that
│                           sees the machine". Registered on the SUBAGENT
│                           and PLANNER stacks both, OUTSIDE the repeat
│                           guard — 19 of those 36 globs were answered by
│                           the guard's dedupe, so inside it the hint would
│                           have missed exactly the calls the model was
│                           most stuck on. **Since OPEN-96 the question is
│                           asked of the RESOLVED path, never of the raw
│                           spelling.** `machine_root` read the first
│                           segment of what the model typed, so it fired on
│                           `{project_path}/src` — the third spelling
│                           `_PATH_RULES` teaches as CORRECT — and told the
│                           model its own project was outside the project:
│                           every macOS project is under `/Users`, every
│                           Linux one under `/home`, so the false positive
│                           was the default case. 3 of run 2cde3406f7d6's 4
│                           firings were false. It now asks
│                           `virtual_to_relative` — CR-B4's single authority
│                           on which real file a path names, and already
│                           imported two functions away — and fires only
│                           when the place the path LANDS is a machine
│                           directory. Two escapes, each for a reason the
│                           resolved reading cannot cover: a real `C:\`
│                           anchor is always the machine whatever follows
│                           it, because the backend strips it and `Windows`
│                           is in no MACHINE_DIRS; and with no project root
│                           there is nowhere to land, so the old spelling
│                           reading stands. UNC is deliberately NOT an
│                           escape — `PureWindowsPath("//x").drive` is
│                           truthy, so a doubled-slash typo and
│                           `\\server\share\x` are one shape and the anchor
│                           cannot separate them; the resolved path can.
│                           `looks_windows_absolute` is untouched and
│                           pinned, because `virtual_to_relative` and the
│                           backend's routing both read it,
│                           plus content_paths.py (OPEN-93, always on): the
│                           THIRD consumer of a path, and the one no prompt
│                           documented. `_PATH_RULES` teaches that `/src/x`
│                           is this project's `src/x` and calls it correct —
│                           and it is, as a TOOL ARGUMENT. `_COMMAND_RULES`
│                           says `execute` disagrees. Neither says anything
│                           about a path written INTO a file, which a real
│                           interpreter resolves later against the MACHINE's
│                           root. Run `2cde3406f7d6`'s tester wrote
│                           `HTML_PATH = "/src/iphone15.html"` into a test
│                           file 170 s AFTER `cat /src/iphone15.html` had
│                           told it that spelling names nothing — 8 of 8
│                           tests failed forever against 480 lines of correct
│                           HTML, and the run finished 0 of 2 tasks in
│                           2,132 s. It fires on `write_file`/`edit_file`
│                           only, only for a file something later RUNS
│                           (`.py .js .sh Makefile …` — never `.html`,
│                           `.md` or `.json`, where a leading `/` is a
│                           document root and legitimate), and only for a
│                           literal whose first segment is a real top-level
│                           entry READ FROM DISK — so `/api/v1/users` and
│                           `/usr/bin/env` are left alone. It APPENDS an
│                           explanation to the successful result and
│                           **never rewrites `content`**, which
│                           `fix_write_params.py` excludes from path repair
│                           for the same reason. Two scanners, deliberately
│                           not one: `find_project_absolute_literals` reads
│                           SOURCE and needs a quoted literal, because
│                           unquoted `/a/b` is division;
│                           `find_project_absolute_mentions` reads the
│                           gate's PROSE, because pytest quotes the path on
│                           the FileNotFoundError line and not on the
│                           AssertionError line. The second feeds
│                           `loop/engine.py::_test_path_note`, which is what
│                           makes the fix loop CONVERGE: the blocker now says
│                           the test's path is wrong when the file really is
│                           there under the relative spelling, so a coder
│                           stops rewriting HTML that was never broken.
│                           SUBAGENT stack only — the planner holds neither
│                           write tool, and an entry for a tool an agent does
│                           not have is OPEN-15.
│                           `fix_write_params.py` now REFUSES two calls
│                           beside the repairs, both on the SHAPE of the
│                           content and never on the path. The second is
│                           OPEN-97: the coder wrote its closing summary
│                           INTO the deliverable — 329 bytes of English
│                           over 24,800 bytes of finished HTML — and
│                           `write_file` answered `Updated file`. Seven
│                           mechanisms let it through, every one behaving
│                           as specified: `_is_directory_placeholder`
│                           declines any path with a suffix, the repeat
│                           guard's write rule refuses only bytes ALREADY
│                           on disk, `_FINISH_RULES` forbids writing a file
│                           to announce completion and this was an UPDATE
│                           to a file the model owned, the gate does not
│                           parse `.html`, the empty-diff guard asks that
│                           something was written and never what, and the
│                           user had auto-accept on (OPEN-30), so no human
│                           saw a preview. `_is_prose_not_content` is held
│                           to `_is_directory_placeholder`'s bar — *it must
│                           not be able to fire on something a person would
│                           write by hand* — with FOUR conditions over a
│                           CLOSED suffix table that declines what it does
│                           not know: short, at most three lines, carrying
│                           NONE of its own type's syntax, and prose-shaped.
│                           The third is load-bearing — there is no valid
│                           HTML with no `<` in it, at any size — and a
│                           template marker (`{{`, `{%`, `<%`) declines,
│                           because a Jinja partial legitimately carries no
│                           markup. `.md`, `.txt` and `.rst` are absent
│                           from the table and must stay absent. **REFUSED,
│                           not annotated**, which inverts OPEN-93's
│                           preference one middleware over and for the
│                           stated reason: a note arrives after the bytes
│                           are on disk, and the bytes are the damage — a
│                           false refusal costs one round trip, a false
│                           accept costs the file. Leads with `REJECTED:`
│                           and never `Error:`, matching its sibling, so
│                           `runner.py`'s counter cannot halt an invocation
│                           on it (OPEN-94). An empty write is deliberately
│                           NOT this rule: truncation to zero is a
│                           different intention,
│                           plus planner_write.py (OPEN-100, the PLANNER's
│                           stack only): `PLANNER_FS_TOOLS` grants no write
│                           tool and must not — the planner plans, the coder
│                           writes (D9, S9c.1) — but until this the ANSWER to
│                           an attempt was langgraph's own tool-list echo,
│                           `write_file is not a valid tool, try one of
│                           [...]`. Run `d8f742805b9b`'s planner read it,
│                           said so — *"I notice there's no write_file tool
│                           available in this environment"* — and generated
│                           the document AGAIN: 140.6 s / 4932 tokens, echo,
│                           then 151.9 s / 5540 tokens, three such calls for
│                           430.9 s, 42% of the run's model time, and zero
│                           files. **OPEN-95's argument one agent up:** a
│                           refusal carrying no correction is one the model
│                           answers by retrying. It names the file, the
│                           route, and — the part that saves the SECONDS
│                           rather than the call — that the contents must
│                           not be produced anywhere, because nothing on
│                           this stack can reach disk. **The route names
│                           only tools the stage actually holds**, read off
│                           the tools it was built with rather than
│                           restated: `_tools_for_stage` gives breakdown no
│                           `record_fact`, so a fixed sentence naming one
│                           would advertise an absent tool — OPEN-15, and
│                           the defect OPEN-101 was filed on one surface
│                           over and closed the same day. A stage holding neither declines to name
│                           a tool rather than naming a wrong one.
│                           `execute` is deliberately NOT covered: the
│                           planner lacks it too, but "the coder writes the
│                           file" is the wrong correction for a command, and
│                           a wrong route is worse than the echo. It fires
│                           at all only because langgraph defers tool-name
│                           validation "to allow interceptors to
│                           short-circuit requests for unregistered tools"
│                           (`tool_node.py:1031`) — upstream behaviour
│                           pinned in `tests/test_deepagents_contract.py`,
│                           because if it changes this stops firing
│                           silently. Its constructor takes `stage_tools`
│                           and NOT `tools`: `AgentMiddleware.tools` is
│                           upstream's list of tools a middleware
│                           CONTRIBUTES (`factory.py:1005`), so the obvious
│                           name hands ToolNode a set of strings and every
│                           planner stage fails to build,
│                           plus test_extension.py (OPEN-99, the TESTER's
│                           stack only): a test the runner can never collect
│                           is not a test. Run 5775ba1f9855's tester wrote
│                           7,619 bytes of Python to
│                           `tests/test_iphone15_responsive.html`, having
│                           derived the name from the file under test and
│                           carried the extension across — then SAID, in its
│                           own output, *"the pytest collection doesn't pick
│                           up .html test files (it only collects .py
│                           files)"*, and shipped it. `stacks/detect.py:120`
│                           found no `.py` file, so the project had no stack,
│                           so `verify/pipeline.py` reported "this project
│                           declares no test command", so four of five stages
│                           were `not_applicable` and the verdict was
│                           `passed`: the run finished DONE in one attempt
│                           with ZERO runnable tests. **Seven components,
│                           none of them wrong** — OPEN-93's shape one layer
│                           out. Rudra held two notions of a test that never
│                           meet: the tester's, whatever it writes into
│                           `tests/`, constrained by prose that assumes a
│                           neighbouring test exists to imitate — false in
│                           exactly the greenfield case this fires in; and
│                           the gate's, which needs a `.py` file to EXIST
│                           before claiming a Python stack. THREE conditions,
│                           all required, and `ast.parse` succeeding is the
│                           load-bearing one: it is the same question the
│                           syntax stage asks, and an HTML document is not
│                           valid Python, so a real `tests/fixture.html`
│                           cannot reach the refusal. `_UNCOLLECTABLE` is
│                           CLOSED and **`.json`, `.yaml` and `.yml` are
│                           MEASURED members of the must-not set** —
│                           `ast.parse('{"a": 1}')` is a dict literal and
│                           `ast.parse('name: rudra')` an AnnAssign, so
│                           including them would refuse an ordinary
│                           `tests/data.json`; two tests pin their absence.
│                           REFUSED rather than annotated, for OPEN-97's
│                           stated reason, and leading with `REJECTED:`
│                           rather than `Error:`, for OPEN-94's. Registered
│                           on the TESTER only, gated on `"run_tests" in
│                           spec.rudra_tools` the way MachinePathMiddleware
│                           reads `fs_tools` — the coder writes `.html`
│                           deliverables on purpose. `has_test_name` is THE
│                           basename reading, shared with
│                           `verify/pipeline.py`, and is deliberately NOT
│                           `stacks/detect.py::_is_test_filename`: that one
│                           is gated on source suffixes and answers False
│                           for every suffix this catches, while accepting
│                           `api.test.ts`, which pytest collects under no
│                           name. A test pins the divergence
├── tools/                  EVERY tool the model can call, and nothing else:
│                           interaction (record_fact · ask_user) · git_tools ·
│                           testing_tools · memory_tools (remember ·
│                           search_memory). `remember` is CONTROL_PLANE, not
│                           MUTATING: it writes under .rudra/ only, and
│                           outside that set the gate would deny it on every
│                           call (A1.75).
│                           **Two schemas were costing a round trip each until
│                           OPEN-102**, and the shape is `fix_write_params.py`'s
│                           one package over: a message carrying COMPLETE
│                           information, rejected on how it was spelled.
│                           `AskOption` now reads a bare string as the label — it
│                           always could have, `description` defaults to `""` — so
│                           run d8f742805b9b's `options: ['Product landing page',
│                           …]` no longer earns 24 lines of pydantic error, one per
│                           option, into every later call of that stage. Exactly ONE
│                           shape is widened: a dict with no `label` and a non-string
│                           scalar still raise, and three tests pin that. `record_fact`
│                           defaults `source` to `"inferred"`, the label that claims
│                           least — never `"asked"`, which A1.72 forbids an unattended
│                           run and A1.73 makes the one thing a later stage trusts as
│                           settled. **`why` stays required and must**: a fact without
│                           a reason is what that field exists to prevent. The store's
│                           own validation is untouched — shape coercion, never value
│                           coercion (facts/store.py:88). The rule it leaves behind is
│                           **a schema is a prompt**: every required field is an
│                           instruction the model must satisfy first try, so ask it
│                           only for what it alone knows.
│                           The last two are thin wrappers over git/ and
│                           testing/, whose APIs the orchestrator calls directly
├── filesystem/             capped project_tree() — VFS deleted in Step 2 (D7)
├── state/                  paths.py (D15 layout), session id (unused).
│                           ProjectConfigManager died with C6.8a.
│                           archive.py is the ONE thing here that writes
│                           OUTSIDE the project (OPEN-68): at run end it
│                           copies usage.json, the ledger, the debug log
│                           and the transcript to
│                           $XDG_STATE_HOME/rudra/runs/<project>/<run>/,
│                           because every instrument this project has is
│                           written inside a directory the measured runs
│                           are then deleted with. It imports nothing from
│                           Rudra but state/paths.py, never raises, and
│                           records the models a run used in meta.json --
│                           NOT config.toml, which may hold an api_key
├── telemetry/              The run, sent somewhere the maintainer can reach
│                           it (OPEN-110). `.rudra/run/logs/` is the whole
│                           support channel and it is a folder the user has
│                           to find, read for secrets and attach; this is
│                           the other half. `langfuse_sink.py` builds ONE
│                           trace per run — `create_trace_id(seed=session_id)`
│                           is deterministic, so the Langfuse URL, the debug
│                           log's filename and `meta.json`'s `run_id` name
│                           the same run. THREE layers, because LangChain
│                           sees only what LangChain does: the
│                           `CallbackHandler` in the RunnableConfig at
│                           `subagents/runner.py` and
│                           `planner_agent.py::_stream_planner_turn` (model
│                           and tool spans, propagated into subgraphs);
│                           `Telemetry.consumer()` registered with
│                           `TraceSink.add_recorder` beside `debug_consumer`
│                           (guard halts, refused plans, every NOTICE — no
│                           LangChain callback fires for any of them); and
│                           `finish()` at `close()` (the ledger's verdict and
│                           usage.json's numbers). `loop/engine.py`'s
│                           AGENTS.md summariser is the fourth site and gets
│                           `config=` explicitly, because "everything except
│                           one call" is what makes a trace untrustworthy.
│                           **The security argument is `mask=`**: the
│                           handler builds LangfuseSpan/LangfuseGeneration
│                           objects and every `input`, `output` and
│                           `metadata` on one passes through the client's
│                           mask before export, so routing it through
│                           `trace/redact.py` covers payloads Rudra never
│                           touches. It FAILS CLOSED — the one swallow in
│                           the project that does — because every other one
│                           protects a run from its bookkeeping and this
│                           protects a user's secrets from a cloud host.
│                           Off unless keys are configured: no keys, no
│                           client, no handler, no call, so §1 goal 7 holds
│                           for every install that did not ask. The SDK's
│                           own retry chatter is FORWARDED into the run log
│                           rather than silenced — `langfuse` AND
│                           `opentelemetry.exporter.otlp.proto.http.trace_exporter`,
│                           the second being the louder one, and named
│                           exactly rather than by its parent tree because
│                           chromadb has an OpenTelemetry of its own
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
                            internals, and TWO shared facts about the
                            machine, each with one definition and several
                            consumers. virtual_paths.py says which real
                            file a model-written path names, shared by the
                            gate, the approval preview and the backend so
                            they cannot disagree (CR-B4). Cross-platform by
                            shape, not by host OS: `C:\...`, UNC, `\x`
                            and `/x` all resolve on every platform.
                            own_interpreter.py says which Python is RUDRA's
                            own (OPEN-80), shared by stacks/detect.py —
                            which must never pick it for the user's tests
                            (D18) — and permissions/env.py, which must keep
                            it out of the environment an agent's shell runs
                            in, or `pip install` installs into Rudra. Inert
                            unless `sys.prefix != sys.base_prefix`: a
                            distribution-packaged Rudra's "own bin" is
                            /usr/bin, and stripping that breaks the machine
                            rather than protecting it
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

**An agent's approval map is the gate's map NARROWED to the tools that agent
holds (OPEN-101, `permissions/interrupts.py::narrow_interrupt_on`).**
`build_interrupt_on` emits one entry per `MUTATING_TOOLS` name and knows
nothing about any agent, which is correct — it describes what the GATE
covers. But deepagents' `HumanInTheLoopMiddleware` matches on the tool-call
**name** in the assistant message, and that happens BEFORE the tool node
discovers the name is unregistered — so an agent raises a full panel for a
call it cannot make, takes the answer, and writes the grant to the audit log.
Run `d8f742805b9b`'s planner did exactly that for `write_file`, and the `!`
the user answered with then authorised a `task` call ten minutes later. **One
implementation, two call sites** — `subagents/build.py::_interrupt_on_for`
(which owns OPEN-15's reasoning and the question only a spec can answer) and
`agent/planner_agent.py`, narrowing per stage against the tools that stage was
built with. A third `create_deep_agent` call site fails
`tests/test_planner_interrupts.py` the day it appears, which is the pin: the
fix existed for a year on one stack and nothing made the other use it.

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

   **Every re-consult is SHOWN the ledger, and that is not politeness
   (OPEN-65).** A stage's consults all share one thread
   (`thread_id=f"{session_id}-{stage}"`), so the model reads each new
   message against whatever `read_ledger` last returned it — and
   `ledger_empty` asserts *"Every task is finished"*, which contradicts
   any snapshot taken before the last task was worked. Told that and
   given nothing to reconcile with, run11's planner re-declared a DONE
   task, reworded; the run spent 133.1s writing nothing. Measured over
   six runs, `ledger_empty` fired six times and reached for `read_ledger`
   **zero** times. `loop/tools.py::render_ledger` is the one spelling,
   shared with the tool, so what the planner is shown and what it can
   fetch cannot drift. This is OPEN-39's rule in the other half of the
   system: **an agent that writes into something is shown it, never
   merely offered a tool that would fetch it.**
8. Reviewer runs once at the end, advisory, printed, gating nothing.

**The split that makes this work (S9c.1):** the model decides what work exists;
Python decides when a task is done and when to stop. `C6.1` wanted an agent that
owns the todo list, D9 forbids an LLM deciding termination — both hold because
the ledger tools *cannot express* `DONE`, not because a prompt asks nicely.

Two edges worth knowing before touching it: an **empty diff is a failed attempt**
(with no changed files the gate reports "0 files parsed" and would pass an
untouched task), and `files_touched` comes from the **filesystem**, not from the
model — `git status` where there is a repo, a fingerprinted `project_files` walk
where there is not. Corrected 2026-08-24 (OPEN-12/OPEN-13): this said "from
**git**", full stop, and the code agreed — `git_snapshot` returned `None`
outside a repo and the empty-diff guard was written `before is not None and
not files_touched`, so in a project with no `.git` the guard above never ran
and two tasks were marked `DONE` having created no file. `attempt_snapshot`
is now the single entry point and never returns `None`.

Corrected again 2026-08-31 (OPEN-63): the walk was `source_files`, which
keeps nine source suffixes, while `git_snapshot` prunes build output and
nothing else — so **whether a file the coder wrote was recorded at all
depended on whether the project had a `.git`**, and no project Rudra has been
run against has ever had one. run12's t1 wrote a `requirements.txt` its
`files_touched` does not name, and t2 — whose brief *was* that file — then
reported "the coder wrote nothing". `verify/stubs.py` now answers the two
questions separately: `source_files` is what the stub scanner may read,
`project_files` is what the project contains, and the second is the first's
walk without the suffix filter, so the gate and the ledger keep one opinion
about build output. **Both are needed; do not collapse them again.**

Corrected a third time 2026-08-31 (OPEN-64), the same defect with the two
paths swapped. The pruning rule had **three** copies and one was different:
`filesystem/tree.py` and `verify/stubs.py` applied A1.29's root-anchored
split, while the loop's own `_is_build_output` matched `out`, `build`,
`dist`, `target` and `coverage` at **any depth**. So in a project that HAS
a `.git`, `src/out/handler.py` — the file A1.29's own comment names as its
casualty — never reached `files_touched`, was never stub-scanned, and as an
attempt's only write read as "the coder wrote nothing", spending an attempt
toward `BLOCKED`. **OPEN-63 lost a record; this lost the task.** The two are
exactly inverted, which is why neither was caught: OPEN-63 bit only projects
with **no** git, so every test run hit it and no real user would; OPEN-64
bites only projects **with** git, so no test run could reach it and every
real user is in it.

There is now **one** implementation, `verify/stubs.py::is_build_output`,
which both `project_files` and `git_snapshot` call — not one definition read
from two places. **Do not add a fourth copy of that literal.** Both copies
that drifted carried a docstring asserting they could not: `stubs.py` named
a test in `tests/test_verify_stubs.py` that did not exist, and
`_is_build_output` claimed "one definition, shared with the gate". The pin
exists now (`test_the_three_copies_of_the_pruning_rule_agree`), and that is
the rule to carry: **a comment asserting two things are the same needs a
test, or it becomes the reason nobody checks.**

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
| `run/ledger.json` | volatile | `add_tasks`/`drop_task` (agent) + `engine.py` (status) | `run_loop`, `summarise` | Tasks are **work**, not filenames. Written atomically after every status change; never resumed. A task's `halts` records every subagent guard that fired on it, and is separate from `note` because `note` is read by a MODEL later — `consult_planner` interpolates it and `record_block_memory` files it in the palace (CR-C4) — and because every branch that finishes a task rewrites `note`, which is how six guard halts vanished from run7 (OPEN-44). **`run_errors` is the same field for the opposite event** (OPEN-46, 2026-09-01): a halt is an invocation Rudra STOPPED, and this is one that never STARTED — a build failure, or a retry budget that ran out. It exists because that landed in `note` alone and so vanished exactly when the task later succeeded, which is OPEN-44's failure one field over. Both dispatch sites write it, and the TESTER's was the worse silence: its result reached only `_record_halt`, which returns early unless a guard fired, so a tester that never ran was recorded in **no field at all**. **A THIRD member since OPEN-98**: an invocation that started, ended cleanly, and did not do its job — run 2cde3406f7d6's tester spent 180.4 s and 22 tool calls and called `run_tests` zero times, and `ok: true` with no halt and no error meant it landed in no field either. Its sentence is its own, because `_record_run_error`'s asserts the invocation never ran; and it states what was OBSERVED (`ended without calling run_tests`) rather than what follows from it — that tester DID shell out to `python3 -m pytest --co`. `SubagentResult.tools` is what answers it, and **None is not `{}`**: None means nothing measured it, `{}` means it ran and called nothing |
| `run/checkpoints.db` | volatile | `AsyncSqliteSaver` | nothing | **fresh uuid4 thread_id each run — never resumed** (A1.2) |
| `run/logs/meta.json` | volatile | `state/archive.py::write_run_meta`, at run START | humans, `state/archive.py` | **What ran, on what, in which mode (OPEN-106).** Rudra's version, python, platform, deepagents' version, the permission mode, whether a terminal was attached, the four bounds, and each role's provider/model/base_url. **Never `api_key` or `api_key_env`** — `model_facts`'s omission, and stricter here because this file is inside the project. Written at the START for §8a failure shape 2: the run that needs explaining has not ended. The archive writes the same dict plus `archived_at` and `files`, from `run_meta`, so the copy in the project and the copy that outlives it cannot disagree |
| `run/logs/permissions.jsonl` | volatile | `permissions.AuditLog` | humans; later `rudra audit` | One line per gated decision, every mode (Step 7) |
| `run/logs/verify.log` | volatile | `verify_project` | humans | Every stage's full output from the last gate run (Step 9a) |
| `run/repl_history` | volatile | `cli_repl.build_session` | prompt_toolkit | Per project, because a Rust project's prompts are not a Python project's. A read-only project loses history, never the REPL (Step 15b) |
| `run/transcripts/<id>.jsonl` | volatile | `trace/transcript.py`, every run | humans, `rudra log` | One JSON object per `TraceEvent`, appended and flushed per event so a killed run still leaves a readable record. Payloads capped at 2000 chars, newest 20 runs kept. **Written by default**, which is safe only because redaction happens where the event is built (A1.95) — moving redaction into the renderer would silently refill this file with credentials (Step 15c, C9.5) |
| `run/logs/debug-<id>.jsonl` | volatile | `trace/debug.py`, **every run** | humans, bug reports | One JSON object per line: every `TraceEvent` plus every `rudra.*` log record and traceback. **The complete record** — registered with `TraceSink.add_recorder`, so unlike the transcript no trace level filters it and payloads are uncapped. One file per run, newest 20 kept by `prune_debug_logs`, which globs `debug-*` so it cannot eat `permissions.jsonl`. `[agent] debug_log = false` or `--no-debug` turns it off. Corrected 2026-08-24 (OPEN-7): was one appending `debug.jsonl` written only under `--debug`, and registered as an ordinary consumer, so `--no-verbose` cut it down to errors. An unopenable file disables the log rather than failing the run (Step 15a, C9.7). **Since OPEN-87/OPEN-89 (2026-09-03) it carries two kinds that are NOT `TraceEvent`s**, and the distinction is the point: a trace event says what the model or Rudra *did*, and these say what the doing COST. `{"kind": "model_call"}` — one per model call, with `role`, `seconds`, both token counts and `ok` (`context/middleware.py::log_model_call`). `{"kind": "subagent_done"}` — one per subagent invocation, with `seconds`, `tool_calls`, a `tools` histogram of the six busiest, and the guard that halted it (`subagents/runner.py::log_invocation`). They ride the `rudra.*` logger tree this file already captures, so neither needed a new sink; `_JsonLines.format` promotes `record.event` to the top level, which is what makes them greppable by `kind` rather than parseable out of a message string |
| `run/logs/usage.json` | volatile | `write_usage_log` | humans | **Two blocks since OPEN-53: `roles` and `run`.** `roles` is per-role tokens, compactions, **retries** and **exhaustions**, plus **`edits_reindented`** since OPEN-92 (a repaired `edit_file` is indistinguishable from one that was right the first time in tokens, seconds and tool results, so without this counter "did that fix pay for itself" has no answer but a hand-written parser) and **`plans_refused`** since OPEN-90 and **`content_paths_flagged`** since OPEN-93 and **`test_writes_rejected`** since OPEN-99 and **`planner_halts`** and **`planner_writes_refused`** since OPEN-100, for the same reason one field over: what those guards prevent is work that never happens — a coder invocation not dispatched, a test that would have failed forever, a planner stage that would have run until a human killed it — and what never happens leaves no mark on `calls`, `seconds` or tokens — `retries` since OPEN-45, because `ModelRetryMiddleware` sits outside `UsageMiddleware` so `calls` silently absorbs every re-issue; `exhaustions` since OPEN-46 reopened (2026-09-01), because `retries` counts only the attempts that were RE-ISSUED and the one that runs out of budget was counted nowhere at all — so every failure rate this ledger has ever computed, `p = retries / calls`, was a **lower bound with nothing saying by how much**. run14 lost task t8 to an exhaustion and its debug log held zero record of it. Read the rate as `(retries + exhaustions) / calls`. `run` is `wall_seconds`, `counted_seconds` and `suspended_seconds`, and is the **only true wall clock in the project** (see §5a). Corrected 2026-08-30 (OPEN-53): the roles used to be the top level, and they were nested rather than given a `run` sibling because anything iterating the file — this repo's own ledger scripts included — would have counted a sibling as a fifth role. A write failure is swallowed — a finished run must not be reported failed over bookkeeping (C7.5). **Written mid-run since OPEN-88 (2026-09-03)**: at every coder dispatch (`loop/engine.py::flush_usage`, beside the ledger save) and once after planning, as well as at the two ends it already had. It used to be written at run end and nowhere else — both `work()`'s last statement and `close()` — so the file that answers *"why is this taking so long"* existed for every run **except the ones anybody complains about**, a run being complained about being by definition one that has not finished. Run `fc543fb2b82f` was two hours into a one-page task with no `usage.json` in its logs directory at all. Overwriting, so flushing often leaves one document rather than a pile |
| `run/artifacts/` | volatile | deepagents eviction + summarization | the agent, via the `/artifacts/` route | Kept out of the project by `artifacts_root` (A1.45) |
| `memory/export/` | durable | `rudra memory export` | `rudra memory import` | The palace is binary and churns, so the markdown export is the portable copy. `exporter.export_palace` is **not** used — it resolves collection and backend from the user's global config (**A1.87**) |
| `memory/palace/` | volatile | `rudra.memory.store` | `rudra.memory.store` | ChromaDB, project-scoped per D14/S14.5. Opened only through `MemoryStore`; a failure degrades loudly and never fails a task (C8.6) |

**Every row above is inside the project, and that is exactly what OPEN-68
was filed on.** `usage.json` and `debug-<id>.jsonl` are the only instruments
this ledger has, and on 2026-09-01 the whole pool six items had been sized
against — run6 … run14 — was found deleted, because the measured runs live
in throwaway sibling directories and `.rudra/` goes with them. Promoting
those files from `run/` to a durable subtree would have changed nothing: the
*project* was deleted, not its volatile subtree. So `state/archive.py` copies
them **out**, to `$XDG_STATE_HOME/rudra/runs/<project-slug>/<run-id>/`, at
`RudraAgent.close()` — the last moment every instrument is still on disk.
`[agent] run_archive = false` turns it off. Automatic and not a command,
because the pool was lost exactly the way a manual step gets skipped, and
`docs/superpowers/plans/2026-08-31-open46-retry-rate.py` reads the archive
root from `archive_home()` rather than spelling it again. **An instrument
whose output is deleted is not an instrument; it is a print statement.**

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
| `subagents: list[SubAgent]` | `SubAgentMiddleware` + the `task` tool | ✅ Step 9b: four specs in `subagents/registry.py`. **Rudra ships its own `general-purpose` to suppress the ungated one deepagents auto-adds** (`graph.py:751`). Passed at `agent/main_agent.py:687` — **the main agent only**. Corrected 2026-08-21 (CR-DOC5) — this read "nothing passes `subagents=` yet", which stopped being true when 9c shipped. Qualified 2026-08-27 (OPEN-37): the planner passes `subagents=None`, so it gets deepagents' ungated auto-added one holding the *planner's* tools, ledger writers included. It is reached by withholding `task` (`DelegationGuardMiddleware` in `build_planner_middleware`), not by passing a spec — suppressing the auto-add would leave `task` reachable |
| `memory: list[str]` | `MemoryMiddleware`, AGENTS.md into system prompt | ⚠️ planner only |
| `permissions: list[FilesystemPermission]` | `allow` / `deny` / `interrupt` path rules | ❌ **cannot be used** — raises on any execute-capable backend (U.7) |
| `interrupt_on: dict` | `HumanInTheLoopMiddleware` — approval gates | ✅ Step 7: one entry per mutating tool, `when` calling Rudra's `PermissionEngine`. **Narrowed per agent before it is passed** (`narrow_interrupt_on`, OPEN-15/OPEN-101): upstream matches the tool-call name before the tool node rejects it, so an unnarrowed map prompts for calls the agent cannot make |
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
| The project file listing in the coder's and tester's prompts | **designed** | `filesystem/tree.py` + `wants_tree`, capped at `TREE_MAX_ENTRIES = 150` in `subagents/build.py`. It BUYS model calls WITH prompt tokens — 65% of run6's tool calls were agents re-deriving a layout nobody had told them — so `usage.json` carries `tree_chars` per role and the trade is settled by that number, not by argument (OPEN-39) |
| Per-task memory in `AGENTS.md` | **designed** | `src/rudra/context/agents_md.py` + the two writers in `loop/engine.py`. Session Log capped at 20 entries (C7.3) |
| Per-role and per-task elapsed time | **designed** | `UsageMiddleware` times every model call; `run_task` times every task (Step 15a, C9.6). **No cost figure, ever — S15.2.** Latency is the one number a local backend always has: token counts are frequently `not reported`, and a failed call still costs the wait. Corrected 2026-08-30 (OPEN-53): this row said **wall clock**, and it is not one. Every clock in Rudra is `monotonic` or `perf_counter` — on macOS both are `mach_absolute_time()`, which does not tick while the process is suspended — so these count *running* time. run10 reported 1243s of it against 4888s of clock because the machine slept three times, and three sessions read the difference as a missing instrument |
| The run's true wall clock | **designed** | `RunUsage.started_wall`/`started_mono` + `suspended_seconds` (OPEN-53). The pair exists because every other number here is blind to suspension in the same direction, so they agree with each other and disagree with the user's stopwatch. Over `SUSPENDED_NOTICE_SECONDS` the panel says so and `loop/engine.py::notice_if_suspended` emits a `NOTICE` named `suspended`. **Write new timing code the same way the portability rule works (§1.8): if a number will be compared against a human's experience, it needs a wall clock, not a monotonic one** |
| The run trace | **designed** | `src/rudra/trace/`. Before Step 15a the subagents printed nothing at all — `runner.py` consumed every chunk to drive its guards and rendered none — so a run went quiet exactly while the coder worked. `--verbose` reached nothing (A1.90) |

**One number, two consumers.** `[model.<role>] context_tokens` feeds both
the summarization trigger and the eviction threshold. That is deliberate:
two independently configured numbers would drift, and the pair only makes
sense read together.

**The standing gap, stated rather than implied: nothing measures system
prompt growth *as a whole*.** Skills, facts and the merged planning
methodology are a fixed cost paid on every call, and no mechanism here trims
them. Two of its parts are now priced individually — `recall_chars` since
Step 14b and `tree_chars` since OPEN-39 — and both exist for the same
reason: a block added to the fixed prompt must arrive with the number that
says whether it paid for itself. The gap is the *total*, not the parts.
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
`[compat]`, `[tools]`, `[skills]`, `[mcp]`, `[memory]`, `[telemetry]`
(OPEN-110 — Langfuse keys; the keys are the switch, `enabled` defaults true
and does nothing without them). Nothing is reserved
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
max_invocation_seconds = 1200           # runaway bound in SECONDS, beside the call one (OPEN-91)
run_archive = true                      # copy the run's evidence out of the project (OPEN-68)

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
- `OverwriteFilesystemBackend` **no longer exists** — nothing imports or constructs it, and `tests/test_agent_wiring.py::test_main_agent_constructs_filesystem_backend_with_virtual_mode` fails if `main_agent.py` ever does again (it checks the AST, so a prose mention in a comment is fine — A4.9). It was deleted with the 0.7.4 upgrade, whose `write()` overwrites by default (`backends/filesystem.py:489`); 0.4.12's refusal to overwrite was what sent small local models into a `write → error → edit no-op → write` loop. Its markdown-fence stripping lives on in `middleware/fix_write_params.py`. Corrected 2026-08-21 (CR-F4) — this bullet described the deleted module as live. Corrected again 2026-09-02 (OPEN-84) — it proved the deletion by enumerating `ls src/rudra/compat/`, and that listing was two files stale: `virtual_paths.py` landed 2026-08-21 (CR-B4) and `own_interpreter.py` 2026-09-02 (OPEN-80), both of which §3's tree already describes at length. An enumeration used as proof has to be complete, and nothing tied this one to the directory — so §3's tree owns the listing, and the proof is now a test that runs rather than a shell command no reader re-runs.
- The 6 middlewares are all patches for qwen3:14b failure modes documented in their own docstrings. Minimum target model is now **32B** → 4 of the 6 were deleted in Step 2, and the 2 survivors are **opt-in behind `[compat]`, default off, as of Step 6** (C1.8). Disposition table: `TODO-old.md` §0.1. `FixWriteParamsMiddleware` is split rather than gated wholesale: fence-stripping and `filename`/`path` → `file_path` aliasing are **always on** (required since U.3 — 0.7.4's `write()` no longer strips), while sandbox-prefix stripping sits behind `[compat] sandbox_paths` because `/src/` and `/tmp/` are sandbox prefixes *and* ordinary absolute directories. Its two **refusals** — the directory placeholder (OPEN-22) and the prose write (OPEN-97) — are always on and are not repairs: this middleware refuses a call or fixes its parameters, and never rewrites a body into something plausible.
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

---

## 8a. Debuggability Is a Product Requirement, Not a Convenience

**The support story is the whole story.** Rudra ships as an open-source
project on GitHub. `CONTRIBUTING.md` accepts no pull requests, there is no CI,
and there is no telemetry — so when a user says *"it is slow"* or *"it wrote
the same file four times"*, the **entire** diagnostic channel is: *send me
`.rudra/run/logs/`*, and the maintainer reads it on a different machine,
against a project they do not have, weeks later, with no ability to re-run
anything. `state/archive.py` exists (OPEN-68) precisely so that folder outlives
the project it describes.

**So the rule, and it is a design constraint on new code, not a nicety:**

> **If a number would answer a user's complaint, the run must WRITE that
> number. A number that exists only in memory, or only in a console panel, or
> only at run end, does not exist.**

This is `§5a`'s "an instrument whose output is deleted is not an instrument"
one step earlier: an instrument whose output is never **written** is not one
either.

### The four failure shapes, each of which has already cost a session

1. **The number is computed and then dropped.** `run_subagent` maintained
   `total_calls` for `MAX_TOTAL_CALLS` and discarded it at every `return`
   (OPEN-89); `UsageMiddleware` measured every model call's duration and
   folded it into a per-role total (OPEN-87). Both numbers were *known* at the
   exact moment they mattered and neither reached disk.
2. **The number is written only at the end.** `usage.json` was written from
   `close()` and from `work()`'s last statement (OPEN-88). A run that is
   taking too long has not ended, so the file that explains it does not exist
   yet. **Any record whose subject is a run in progress must be written while
   the run is in progress.**
3. **The number must be derived rather than read.** Before OPEN-87, answering
   *"where did the 2 hours go?"* meant differencing `ts` across 441 adjacent
   lines of JSONL and knowing which adjacencies were model calls. That was
   done, with a throwaway parser, by a session with the source tree open. A
   user filing an issue will not write a parser, and neither will the
   maintainer at triage time. **A log the maintainer has to write code against
   is a log that does not get read.**
4. **The record is one field and the field gets overwritten.** OPEN-44 lost
   six guard halts into `note`, and OPEN-46 lost every build failure into the
   same field, because `note` is rewritten by every branch that finishes a
   task. `halts` and `run_errors` are separate fields for that reason.
   **Diagnostic state is append-only or it is not state.**

### What this means when you write new code

- **A guard that stops something must log what the something spent.** "It
  halted" is a symptom; "it halted after 55 tool calls, 41 of them `glob`" is
  the diagnosis. That histogram is why `log_invocation` carries `tools` and
  not merely a count.
- **A failure costs time too.** `_record_failure` logs `ok: false` with the
  exception type, because a provider 500 costs the user the full wait and buys
  nothing, and a per-role total cannot tell it apart from a fast success that
  reported no tokens.
- **Route it through the `rudra.*` logger tree.** `trace/debug.py` captures
  that whole tree into `debug-<id>.jsonl` and `_JsonLines.format` promotes a
  record's `extra={"event": {...}}` to the top level of the JSON object. So a
  new diagnostic record needs **no new sink, no new file and no new config
  key** — one `logger.debug(..., extra={"event": {"kind": ...}})` and it is in
  the file users are told to attach. Give it a `kind` and name that string as
  a module constant: it is what a maintainer will be told to grep for, and a
  second spelling of it is a second answer to *"why is my filter empty"*.
- **A record is not a `TraceEvent` unless it describes something that was
  DONE.** `TraceKind` is the vocabulary for the model's and Rudra's *actions*
  (`§3`, `trace/events.py`). `model_call` and `subagent_done` are measurements
  *of* those actions and deliberately sit beside that vocabulary rather than
  inside it. Do not widen `TraceKind` to hold timings.
- **Bookkeeping may never end a run.** Every writer here swallows its own
  failure — `write_usage_log`'s rule (`loop/engine.py`), which
  `TraceSink.emit`, `transcript.py`, `debug.py`, `archive.py` and both
  functions added by OPEN-87/OPEN-89 all follow. A run that did its work must
  not be reported failed because a log line could not be written.
- **Never put a secret in a diagnostic.** Redaction happens where the event is
  built, not where it is rendered (`§3`, A1.95), and `archive.py` records the
  models a run used and never `config.toml`, which may hold an `api_key`. A
  new record inherits that obligation: it is going to be pasted into a public
  GitHub issue.

### The reader's checklist, for the folder a user sends you

`.rudra/run/logs/` — or
`$XDG_STATE_HOME/rudra/runs/<project>/<run>/` when the project is gone:

| Question | File | How |
|---|---|---|
| How long, really? | `usage.json` | `run.wall_seconds`, and `suspended_seconds` before you believe any other clock (OPEN-53) |
| Which role burned it? | `usage.json` | `roles.<role>.seconds / calls`. If that is ~100% of `counted_seconds`, the run is model-latency bound and the only lever is **fewer calls** |
| Is the provider healthy? | `usage.json` | `(retries + exhaustions) / calls`. Both, never `retries` alone (OPEN-46) |
| Which call was slow? | `debug-<id>.jsonl` | `"kind": "model_call"` |
| Which invocation was the runaway, and doing what? | `debug-<id>.jsonl` | `"kind": "subagent_done"` — sort by `seconds`, then read `tools` |
| What was it actually thinking? | `debug-<id>.jsonl` | `"kind": "tool_call"` / `"ai_text"`, uncapped payloads |
| What did Rudra stop, and why? | `debug-<id>.jsonl` | `"kind": "notice"` — `guard`, `retry`, `suspended` |
| Which task, how many tries, how long? | `ledger.json` | `seconds`, `attempts`, `halts`, `run_errors`, `files_touched` |
| Did the gate pass, and on whose interpreter? | `verify.log` | the `command:` line of each stage — OPEN-80 was invisible without it |
| Was anything denied? | `permissions.jsonl` | one line per gated decision, every mode. **In the archive too since OPEN-107** — it and `verify.log` were the two instruments the archive did not carry, so neither folder could answer a report alone |
| Which Rudra, which Python, which mode? | `meta.json` | **In `logs/` since OPEN-106**, written at run start. Before that it existed only in the archive, written at run end, and the docs asked the user to gather the same facts by hand — which is the step that gets skipped (OPEN-68's own argument) |
| What did the provider actually say? | `debug-<id>.jsonl` | `"kind": "model_call"` with `"ok": false` → `error_detail` and `traceback`, both redacted. **Before OPEN-109 this was `type(exc).__name__` alone**, so a 400 *model not found*, a 401 and a read timeout were one word; run `f845b496a2aa`'s 240 KB record held 149 model calls and zero tracebacks |
| Did the run die during SETUP? | `debug-<id>.jsonl` | It now exists — the log is opened before the skills cache, the backend, the gate, `.mcp.json`, sqlite and the checkpointer (OPEN-108). A run that leaves `meta.json` and a debug log holding only a traceback died in one of those, which is the class of failure that cannot be reproduced on the maintainer's machine |
| Is Langfuse configured, and why is the project empty? | `debug-<id>.jsonl` | `"logger": "rudra.telemetry*"`. The SDK's export failures are forwarded there rather than to the terminal (OPEN-110); `Langfuse tracing → <url>` on the console says it started at all |
| Did a subagent get stopped for spending too long? | `ledger.json` · `debug-<id>.jsonl` | `halts` naming a `NNNs limit`, and a `"kind": "notice"`, `name: "guard"` line. **Which bound fired is the diagnosis**: "80 tool calls" is a loop, "over the 1200s limit" is a slow provider or a loop (OPEN-91). `meta.json` in the archive records the limit that was in force |
| Did an agent go hunting the machine's filesystem? | `debug-<id>.jsonl` | `"kind": "notice"`, `name: "machine-path"` — one per tool result Rudra had to explain. Non-zero means OPEN-91's defect fired and was answered at call two instead of call forty. **Trustworthy as a count since OPEN-96**, which stopped it firing on the project's own absolute path and on `//x` typos — 3 of 4 firings in run `2cde3406f7d6` were false, so the number meant nothing before that. Read the payload anyway, not only the count: it quotes the spelling, so a false positive is visible by eye without a parser |
| Did edits have to be repaired? | `usage.json` | `roles.<role>.edits_reindented` — non-zero means OPEN-92's gutter defect fired and was caught. Paired with a `"kind": "notice"`, `name: "reindent"` line per repair in `debug-<id>.jsonl` |
| Did an agent write a virtual path into real source? | `usage.json` · `debug-<id>.jsonl` | `roles.<role>.content_paths_flagged`, and a `"kind": "notice"`, `name: "content-path"` line per hit. Non-zero means OPEN-93's defect fired and was explained at the write instead of costing the run. **Read it beside `verify.log`**: a `test` stage failing on a `/`-prefixed path with `content_paths_flagged: 0` means the literal came from somewhere this middleware does not watch — a fact value or a task description, which is where the reported run's copy came from |
| Did an agent stop mid-thought? | `debug-<id>.jsonl` · `ledger.json` | `"kind": "subagent_done"` → `nudged` and `nudge_outcome`. Mostly `continued` = OPEN-98's heuristic is earning its keep; mostly `confirmed_done` = it is firing on agents that had finished, so narrow the opener list; firing on nearly every invocation = it is wrong, revert it. Beside it, `run_errors` carrying `the tester ended without calling run_tests` is the un-nudged case reaching the ledger — **absent on a healthy run** |
| Did an agent write its summary into a file? | `usage.json` · `debug-<id>.jsonl` | `roles.<role>.writes_rejected_as_prose`, and a `"kind": "notice"`, `name: "prose-write"` line per refusal. Non-zero means OPEN-97's defect fired and the bytes never reached disk. **A false positive here is a REFUSED REAL WRITE** — the notice names the path, so check the first one by eye before trusting the count |
| Did the tester write a test nothing can collect? | `usage.json` · `debug-<id>.jsonl` · `verify.log` | `roles.tester.test_writes_rejected`, and a `"kind": "notice"`, `name: "test-extension"` line naming the path. Non-zero means OPEN-99's defect fired and the file never landed. **A false positive here is a REFUSED REAL WRITE** — check the first one by eye. Read it beside `verify.log`: a `test` stage saying *"declares no test command: no .py file was found, but this project holds …"* is the same defect caught one layer later, after the bytes were already on disk |
| Did a planner stage run away? | `usage.json` · `debug-<id>.jsonl` | `roles.planner.planner_halts`, and a `"kind": "notice"`, `name: "planner-guard"` line. **Read the payload, not only the count: each bound names itself** — "40 tool calls in one planner stage" is a loop, "over the 1200s limit" is a slow provider or a loop (OPEN-91's rule at the planner). Before OPEN-100 a planner stage had NO call cap and NO time cap, so the answer to this question was a `CancelledError` in the trace and a human's memory |
| Did the planner try to write the deliverable? | `usage.json` · `debug-<id>.jsonl` | `roles.planner.planner_writes_refused`, and a `"kind": "notice"`, `name: "planner-write"` line naming the path. **1 is the guard working; a number that climbs is the refusal being read and ignored**, and is the evidence to bring before anyone argues for a prompt fix (OPEN-17's rule). Read it beside the planner's output tokens in `"kind": "model_call"`: what costs the run is not the refused call but the 5,000-token document generated before it — in run `d8f742805b9b`, 430.9 s and 42% of model time |
| Was the plan itself the problem? | `usage.json` · `ledger.json` | `roles.planner.plans_refused` is 1 when OPEN-90's guard made the planner re-plan, with a `"kind": "notice"`, `name: "plan-shape"` line saying so. Read it against `files_touched: []`: a run with `plans_refused: 1` and no empty-`files_touched` `DONE` task is that guard working; empty `files_touched` with `plans_refused: 0` is the defect firing in a shape the guard did not see |

**`files_touched: []` on a `DONE` task is the single highest-signal line in
that folder.** It means a coder invocation was dispatched, paid for, and wrote
nothing — six of them in run `fc543fb2b82f`, 4,600 seconds, 63% of the clock.
Read the plan next, not the loop (OPEN-90).

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
