# Step 10a — The Open Fact Store and the Q&A Purge (C6.8a, C6.8 tool half)

**Date:** 2026-08-13
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.8a`, `A1.30`; closes the tool-contract half of `C6.8`
(batched `ask_user`, question budget). The prompt-level "ask only what you cannot
infer" *sequencing* is Step 10b.
**Depends on:** Step 9c (the loop, the ledger, `SubagentContext`) — by code, not
just by order.
**Blocks:** Step 10b (three-stage planning reads and writes this store), Step 10c
(plan approval renders it).

---

## 0. Decomposition

The owner split Step 10 three ways on 2026-08-13, the same way Step 9 was split:

| Sub-step | Rows | Why separable |
|---|---|---|
| **10a** (this spec) | `C6.8a`, `A1.30`, `C6.8`'s tool contract | No model-sequencing change. Fully unit-testable; one live acceptance run |
| **10b** | `C6.7`, `C6.8`'s budget-and-inference prompt rules | Three-stage planning (clarify → architect → breakdown). Writes decisions **and rationale** into 10a's store |
| **10c** | `C6.9` | Present plan → approve → execute |

10a is first because both later sub-steps write into the store it defines, and
because it is the only one of the three whose acceptance does not depend on the
planner's new shape.

---

## 1. What this replaces

Requirement #1 (owner, quoted in TODO.md §0.5): *"if anything is not cleared from
prompt it should ask user questions. this should be dynamic. no static QA."*

Today's questionnaire is a fixed 4-field model — language / framework / database /
notes — threaded through seven surfaces. Deleting `ProjectContext` alone leaves
six that silently re-impose the same four boxes. The full inventory is TODO.md
§0.5; this spec is its execution. Six of the seven are live; surface #7 (README)
was already removed by an earlier step and §0.5's citation is stale — see §6.

Two facts measured while designing this, which shape the whole thing:

1. **The coder has never seen the project facts at all.** `tech_stack_content`
   flows into exactly one place — the planner's system prompt
   (`planner_agent.py:42`, built once at `main_agent.py:382`). `build.py` gives
   the coder, tester and reviewer `spec.system_prompt` and nothing else
   (`build.py:141`). A project whose `project.json` says Rust has a coder that
   was never told.
2. **`build_agent` runs per invocation** (`runner.py:124`), so anything rendered
   into a subagent's prompt is rebuilt on every dispatch. Facts recorded at
   minute one reach the coder at minute two for free.

---

## 2. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S10a.1** | Step 10 splits into 10a / 10b / 10c | Each sub-step ships with its own spec, plan and acceptance run |
| **S10a.2** | A fact is `{value, why, source}`, not a bare string | `C6.7` requires persisting decisions **and rationale**; 10b writes into this shape with no migration |
| **S10a.3** | The model writes one fact per call: `record_fact(key, value, why, source)` | Four flat typed args. The model never emits JSON structure — the discipline `add_tasks(list[str])` follows and `PLAN.md` failed (C6.10) |
| **S10a.4** | `ask_user` takes a **batch** and records the answers itself | One round trip for related questions; an answer cannot be lost between asking and recording. Keys come from the model, not from Python parsing prose |
| **S10a.5** | `ask_user` is **not registered** when there is nobody to answer | Structural, not advisory — the precedent is S9b.3: the reviewer cannot write because the tool does not exist. No hang, no wasted call |
| **S10a.6** | Budget: `[agent] max_questions`, default 5, enforced in the tool | Same `REJECTED` idiom as `drop_task`. `0` disables asking outright |
| **S10a.7** | Facts reach every agent as a rendered **prompt block**; `run/tech_stack.md` is deleted | One artefact, not two. Diverges from §0.5's literal "render tech_stack.md from facts" — recorded here rather than left as drift |
| **S10a.8** | `.rudra/project.json` is **ignored entirely**, not migrated | No migration code, no dual source of truth. Stated cost: a project that already answered the four questions loses them and the agent re-establishes or re-asks. The file is left on disk, unreferenced |

---

## 3. Architecture

```
src/rudra/facts/
├── __init__.py   Fact · FactStore · facts_block
├── store.py      Fact · FactStore. Pure data plus load/save.
│                 Imports nothing from Rudra — same rule as loop/ledger.py,
│                 so its tests need no model, no backend, no gate
└── render.py     facts_block(store) -> str. Pure. No I/O
```

`tools/interaction_tools.py` is rewritten in place: `create_interaction_tools`
keeps its name and its position in the planner's tool list, and returns
`record_fact` plus — only when a user exists — `ask_user`.

### The record

```json
{
  "language":      {"value": "Rust", "why": "user said 'CLI in Rust'", "source": "inferred"},
  "cli_framework": {"value": "clap", "why": "user answered: which arg parser?", "source": "asked"}
}
```

`source` is one of `asked` · `inferred` · `detected`. Keys are **not enumerated
anywhere in code** — that is the whole point of the row. Validation covers types
and size only:

| Field | Rule |
|---|---|
| key | matches `[a-z0-9_.-]{1,64}` |
| value | non-empty string, ≤ 512 chars |
| why | non-empty string, ≤ 512 chars |
| source | ∈ `{asked, inferred, detected}` |
| store | ≤ 100 facts |

A rejected write returns `REJECTED: <why>` and leaves the store untouched. It is
never a raise: a bad fact must not end a run that is otherwise working.

### Persistence

`.rudra/facts.json`, in D15's **durable** subtree — it is the successor
CLAUDE.md's state table already anticipated ("Becomes `facts.json` at C6.8a").
Written atomically (temp file + `os.replace`) after every successful record, the
same guarantee `Ledger.save` gives (`ledger.py:95-107`). A corrupt or absent file
loads as an empty store rather than raising (`ledger.py:113-116`).

`paths.py` gains `facts_json`; it loses `tech_stack_md`. Both are one-line changes
to `rudra_paths()`, which stays the single source of truth for `.rudra/` paths.

---

## 4. The tools

```python
record_fact(key: str, value: str, why: str, source: str) -> str
ask_user(questions: list[str], keys: list[str]) -> str      # interactive runs only
```

**`record_fact`** validates, records, saves, returns
`Recorded language = "Rust" (inferred).` or `REJECTED: …`.

**`ask_user`** prompts each question in sequence on the Rich console, records each
non-empty answer as `{value: answer, why: "user answered: <question>", source:
"asked"}`, and returns a numbered Q→A block so the model sees exactly what the
user said, not merely that it was stored.

| Case | Behaviour |
|---|---|
| `len(keys) != len(questions)` | `REJECTED: give one key per question.` Nothing prompted, nothing recorded |
| Empty question list | `REJECTED: give at least one question.` |
| Empty answer | Not recorded. Returned as `(no answer)` — an unanswered question must not become a fact |
| A key fails validation | That pair is rejected in the returned block; the others still ask and record |
| Budget exhausted | `REJECTED: question budget spent (N) — infer the rest from the request.` |
| `EOFError` mid-batch | Remaining questions skipped; answers already given are kept and recorded |

The budget counts **questions, not calls**, and lives in a per-run counter closed
over by the factory. A call whose batch would exceed the remaining budget asks the
ones that fit and says so — refusing the whole batch would punish the batching the
row exists to encourage.

### Registration

`ask_user` is included **iff** `cfg.permissions.mode != "auto"` **and**
`stdin_is_interactive()`. `plan` mode keeps it: planning is the one mode where
asking is the entire point, and `ask` mode already has a TTY guaranteed by the
`cli.py:666` check. When it is absent the planner prompt says so, so the model is
not told to call a tool that is not there — the `_tools_for` rule
(`build.py:64-70`) applied to a prompt.

---

## 5. Data flow

```
cli.main
  └─ create_main_agent
       ├─ store = FactStore.load(paths.facts_json)         # empty on greenfield
       ├─ planner  = create_planner_agent(..., facts=store)
       │     prompt: "## PROJECT FACTS" + facts_block(store)      ← built once
       │     tools:  ledger tools + record_fact [+ ask_user]
       ├─ SubagentContext(facts=store, …)
       └─ run_loop
            └─ run_subagent → build_agent → _prompt_for(spec, context)
                  spec.system_prompt + facts_block(context.facts)  ← per dispatch
```

One `FactStore` per run, **shared by reference** — the planner's tools mutate it
and every later subagent build reads it back. A copy would leave the coder reading
a stale store, which is the same failure mode the shared `Ledger` exists to avoid
(`main_agent.py:412-414`).

The asymmetry is deliberate and stated rather than discovered: the planner's own
system prompt is frozen at construction, so facts it records mid-run come back to
it only through its own tool results. Every coder, tester and reviewer dispatch
after that point sees them, because `build_agent` runs per invocation.

`_prompt_for(spec, context)` is a new helper in `build.py`, used by **both**
`build_agent` and `to_subagent_spec`, for the same reason `_tools_for` and
`_middleware_for` are shared: the delegating path must not drift from the direct
one. The existing parity test is extended to assert prompt parity too.

`facts_block` renders in insertion order and returns `""` for an empty store, so a
greenfield run adds no section and spends no tokens.

---

## 6. Surface-by-surface changes

| # | Surface | Evidence | Change |
|---|---|---|---|
| 1 | `ProjectContext` — 4 fixed fields | `state/project_config.py:11-27` | File deleted, with `ProjectConfigManager`. `rudra.state` stops exporting both |
| 2 | `save_project_context` allowlist | `interaction_tools.py:67` | Deleted; `record_fact` replaces it |
| 3 | `ask_user` docstring: language/framework/database, "ONE focused question at a time" | `interaction_tools.py:32` | Rewritten to the batch contract in §4 |
| 4 | `_ensure_agents_md` renders the 4 fields | `main_agent.py:215-224` | Renders `facts_block` instead. **Create-once semantics unchanged** — A1.9 stays open and belongs to C7.3 |
| 5 | `_write_tech_stack_file` + the 8-row inference table | `main_agent.py:242-279` | Function deleted whole, with `paths.tech_stack_md` and the `tech_stack_content` parameter chain. **Closes A1.30** |
| 6 | Planner prompt: "Do NOT call ask_user() if the task already specifies a framework or language" | `planner_agent.py:78` | Replaced by the rule in §7 |
| 7 | README documents the literal 4-question form | §0.5 cites `README.md:626-636` | **Already gone — the citation is stale.** Measured 2026-08-13: `README.md` is 261 lines, and `grep -i "primary language\|framework\|database"` returns nothing. The README was rewritten by an earlier step and §0.5's line numbers were never revisited (the same class as A4.10). Nothing to do here beyond re-running that grep at implementation time and recording the result; the full rewrite stays A4.1 / C9.2 |

Also removed: `AgentContext.project_context` (`main_agent.py:24`),
`load_project_context` (`cli.py:212`), and both `project_context=` call sites
(`cli.py:706`, `cli.py:790`).

---

## 7. The planner prompt rule

Surface #6 is replaced by, in `build_planner_prompt`:

> Ask only what you cannot infer from the request or the codebase. Batch related
> questions into a single `ask_user` call. You have **N** questions for the whole
> run. Record every fact you establish — whether you inferred it or the user
> answered it — with `record_fact`, and say **why** you believe it.

When `ask_user` is not registered, that paragraph is replaced by:

> This run is unattended: there is nobody to ask. Infer what you need from the
> request and the codebase, and record each inference with `record_fact`.

N is `cfg.agent.max_questions`, rendered from config rather than hardcoded, so the
prompt and the enforcement cannot disagree.

Note what does **not** change: the planner still cannot mark anything done, and no
prompt names a `.rudra/` path — `tests/test_rudra_dir_migration.py` asserts that
absence and must keep passing.

---

## 8. Configuration

```toml
[agent]
max_questions = 5     # 0 disables clarification entirely
```

Validated `>= 0` at load, in `config/schema.py`, beside `max_fix_attempts`
(`schema.py:79`, defaults at `schema.py:150`).
`config list` must print it — A1.54 was exactly this defect, a new `[tools]` key
honoured and never shown, so the row-listing path is the one to check.

---

## 9. Testing

| File | Covers |
|---|---|
| `tests/test_facts_store.py` | Every validation rule; atomic save; corrupt-file load; ≤100 cap; insertion order preserved across save/load |
| `tests/test_facts_render.py` | Empty store → `""`; ordering; a value containing Rich markup renders literally (A1.48/A1.67's class of defect) |
| `tests/test_interaction_tools.py` | Budget exhaustion; partial batch at the boundary; key/question length mismatch; empty answer not recorded; `EOFError` mid-batch; `ask_user` absent when unattended, present in `ask` and `plan` |
| `tests/test_subagents_build.py` (extended) | `_prompt_for` parity between `build_agent` and `to_subagent_spec`; a fact recorded after construction appears in the next build's prompt |
| Guard tests | No prompt string in `src/` contains `primary_language`, `framework` or `database` as a field name; `rudra.state` exports no `ProjectContext`; `rudra_paths()` has no `tech_stack_md` |

`uv run pytest -q` must not go below **880 passed, 2 skipped**; `ruff check` and
`ruff format --check` must be clean. Both are absolute gates.

## 10. Acceptance

§0.5 states the test this row exists to pass, and it is used verbatim:

1. **`rudra "build a CLI in Rust with clap"` in an empty directory**, live model,
   from a `mktemp -d` outside the repo under `env -i`. Must record `rust` and
   `clap` as facts **with rationale**, and produce a Rust plan. Today surface #5
   has no Rust row and surface #6 forbids asking, so the run steers to
   Python/JS — the before-and-after is the evidence.
2. **The same request under `--auto`.** `ask_user` must be absent from the
   planner's tool list, the run must complete without prompting, and the facts it
   records must carry `source: "inferred"`.
3. **An ambiguous request under `ask` through a real pty** (e.g. `build a web
   API`): the planner must batch its questions into one `ask_user` call, and the
   answers must land in `facts.json` with `source: "asked"` and a `why` naming the
   question.

Run 3 is the one that proves the row rather than the plumbing: batching is what
C6.8 asks for and what surface #3 forbade.

---

## 11. Out of scope

- **Three-stage planning** (`C6.7`) and the clarify/architect phases — 10b.
- **Plan presentation and approval** (`C6.9`) — 10c.
- **A1.9**, the never-updated `AGENTS.md`. This step changes what it renders, not
  when it is written; the living-document work is `C7.3`.
- **The full README rewrite** (`A4.1` / `C9.2`). Surface #7 needs no edit at all.
- **MemPalace** as a fact backend (Step 14). `facts.json` is a file, deliberately.
