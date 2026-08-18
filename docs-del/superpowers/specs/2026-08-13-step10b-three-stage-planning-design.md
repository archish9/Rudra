# Step 10b — Three-Stage Planning (C6.7, C6.8's sequencing)

**Date:** 2026-08-13
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.7`, `C6.8` (its remaining sequencing half), `A1.72`.
**Depends on:** Step 10a (the fact store, `record_fact`, batched `ask_user`) — by
code, not just by order. Also Step 9c (`run_loop`, `consult_planner`).
**Blocks:** Step 10c (`C6.9`, plan approval) — there is no structured plan worth
presenting until this exists.

---

## 0. What this replaces

Requirement #4 (owner): *"Plan before coding. Dynamic clarifying questions (no
static Q&A scripts), tracked progress."*

Today the planner is consulted **once** before work starts
(`loop/engine.py:394`), with one message: "Break this request into tasks". It
holds every tool it will ever have — the ledger tools plus, since 10a,
`record_fact` and `ask_user` — and its prompt asks it to establish facts *and*
declare work in the same turn.

That is one stage doing three jobs, and the failure it produces is ordering:
nothing stops a 32B model calling `add_tasks` before it has asked anything, so
the questions that would have shaped the plan get asked, if at all, after the
plan exists. `C6.8`'s row asks for a "bounded clarification **phase**", and a
phase is precisely what a single consult cannot be.

10a shipped the *tools* for clarification — batching, a budget, provenance per
fact. 10b ships the *sequencing*, which is the half its row still carries.

---

## 1. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S10b.1** | Python drives three consults, **one agent per stage**, each with its own tool set | A stage cannot do another stage's job because the tool is not registered — S9b.3 and S10a.5's enforcement, not a prompt. Matches S9c.1: the model picks content, Python owns control flow |
| **S10b.2** | The architect persists **facts**, not a new artefact | `C6.7`'s "decisions AND rationale" is exactly `{value, why, source}`. No second store, no second place to look. Cost accepted: 512 characters per value, so a decision is stated, not essayed |
| **S10b.3** | Re-consultation enters **breakdown only** | Clarify and architect run once per run. This is what keeps the model-call cost bounded, the same reason S9c consults on a stall and never on an ordinary success |
| **S10b.4** | Clarify runs in **every** mode, as an inference stage when it cannot ask | One code path, and the facts land before the architect reasons about them. 10a's live run showed the planner inferring and recording unprompted already |
| **S10b.5** | `A1.72` is fixed here: `record_fact` coerces `source="asked"` → `"inferred"` when `interactive=False` | Python knows with certainty nobody was asked. 10b's clarify stage is the first consumer that would otherwise trust a wrong label |

---

## 2. Architecture

```
run_loop(request)
  ├─ consult(stage="clarify")     ask_user* · record_fact · read-only fs
  ├─ consult(stage="architect")   record_fact · read-only fs
  ├─ consult(stage="breakdown")   add_tasks · drop_task · read_ledger
  └─ while pending:
        run_task ...
        on BLOCKED or empty ledger → consult(stage="breakdown", reason=...)

  * ask_user is absent when cfg.permissions.mode == "auto" or stdin is not a TTY
```

Each stage is a separate `create_deep_agent` with its own thread id
(`{session_id}-{stage}`), built through one function so no second assembly path
can forget the gate — the rule `subagents/build.py` already follows.

### The facts store is the only channel between stages

Nothing carries from clarify to architect except what was **recorded**. Three
consequences, all wanted:

1. The architect must write its decisions down rather than reason at length in a
   transcript nobody keeps.
2. Every stage is independently testable by seeding a `FactStore` — no model, no
   transcript replay.
3. The coder sees the architecture the architect chose, because 10a already
   renders the facts block into every subagent prompt at `build_agent` time.

### What each stage may do

| Stage | Tools | Job | Stops when |
|---|---|---|---|
| **clarify** | `ask_user` (interactive only), `record_fact`, `read_file`/`ls`/`glob`/`grep` | Establish the facts the work depends on: ask what cannot be inferred, infer the rest | Facts recorded, or there is nothing to establish |
| **architect** | `record_fact`, `read_file`/`ls`/`glob`/`grep` | Decide layout, module boundaries, error handling, test strategy — each recorded as a fact with its `why` | Decisions recorded |
| **breakdown** | `add_tasks`, `drop_task`, `read_ledger` | Turn the request plus the facts into units of **work** | `add_tasks` has been called once |

The breakdown stage can neither record facts nor ask questions: by the time it
runs, those are settled. **No stage can write `DONE` or `BLOCKED`** — S9c.1 is
untouched, and the ledger tools still cannot express either.

An architect fact looks like any other:

```json
{
  "layout":         {"value": "src/parser.rs parsing; src/main.rs CLI entry",
                     "why": "keeps the parser unit-testable without the CLI",
                     "source": "inferred"},
  "error_handling": {"value": "anyhow at the boundary, no panics in lib code",
                     "why": "a CLI should exit non-zero, not abort",
                     "source": "inferred"}
}
```

---

## 3. Control flow

`consult_planner`'s three reasons (`initial`, `ledger_empty`, `blocked` —
`planner_agent.py:361-372`) become a **stage** plus a **reason**:

| Call site | Stage | Reason | Message |
|---|---|---|---|
| `run_loop` start | `clarify` | `initial` | "Establish what this work depends on: …" |
| `run_loop` start | `architect` | `initial` | "Decide how this will be built: …" |
| `run_loop` start | `breakdown` | `initial` | "Break this request into tasks: …" (today's message) |
| Task blocked | `breakdown` | `blocked` | Today's message, unchanged |
| Ledger empty | `breakdown` | `ledger_empty` | Today's message, unchanged |

`create_planner_agent` gains `stage: str` and stops being one constructor that
registers everything; `_tools_for_stage(stage, …)` becomes the single place that
decides what a stage may do, and `build_planner_prompt` takes the stage too.

Constructing three agents rather than one is not free, but it is construction,
not inference: `build_model` "makes no network call — LangChain chat model
constructors do no I/O" (`llm/factory.py:64-68`), and Step 3's offline substitute
measured a full `create_deep_agent` graph compile with no model invoked. Note it
is **not** cached — three stages construct three `BaseChatModel` instances for
the same role. That is cheap and worth stating, because an implementer who
assumes caching might introduce it and hand three stages one shared object.

### The cost, stated rather than discovered

**Three model calls before the first line of code**, where 9c made one. On a
trivial request two of those are overhead. Two things bound it: each stage's
prompt says to stop as soon as it has nothing to add, and each stage carries
three tools rather than nine, so its schema is smaller. If measurement shows it
hurts, the lever is a config key (`[agent] planning_stages`), not a redesign —
and it is deliberately **not** shipped now, because an inert config key is worse
than no key (the `[tools]` reasoning in CLAUDE.md §6).

---

## 4. Failure modes

| Case | Behaviour |
|---|---|
| Clarify records nothing | Not an error. Architect proceeds; an obvious request needs no facts |
| Architect records nothing | Not an error. Breakdown proceeds on the request alone |
| Breakdown adds no tasks | Ledger empty → the existing `ledger_empty` consult fires, then the run ends reporting zero tasks. Unchanged from 9c |
| Question budget spent during clarify | `ask_user` returns `REJECTED`; the stage records what it inferred instead |
| A stage's guard halts it (`_stream_planner_turn` returns False) | The run continues to the next stage. A stalled clarify must not cost the user their run |
| A stage raises | Propagates as today. `A1.39` (no retry around model invocation) is unchanged and out of scope |
| `--auto` / non-TTY | `ask_user` is not registered; `record_fact` coerces `asked` → `inferred` (S10b.5) |

---

## 5. Testing

| File | Covers |
|---|---|
| `tests/test_planner_stages.py` (new) | Each stage's tool list is **exactly** its contract: `add_tasks` absent from clarify and architect, `record_fact` and `ask_user` absent from breakdown, `ask_user` absent from every stage when unattended; each stage's prompt names its own job and not another's |
| `tests/test_planner_stages.py` | A fact recorded in clarify appears in the architect's rendered prompt — the only channel between stages |
| `tests/test_interaction_tools.py` (extend) | S10b.5: `record_fact(source="asked")` yields `inferred` when `interactive=False`, and is left alone when interactive |
| `tests/test_loop_run.py` (extend) | The three stages run in order before any `run_task`; a block re-enters **breakdown only**, never clarify or architect |
| `tests/test_agent_wiring.py` (extend) | Every stage carries the permission gate and the same `interrupt_on` |

`uv run pytest -q` must not fall below **977 passed, 2 skipped**. `ruff check`
and `ruff format --check` clean. Both absolute.

---

## 6. Acceptance

Three live runs, from `mktemp -d` git repos outside the repo under `env -i`,
configured only by `.rudra/config.toml`.

1. **Ambiguous request in `ask` mode through a real pty** (`build a web API`):
   clarify batches its questions into **one** `ask_user` call, the answers land
   with `source: "asked"` and a `why` naming the question, the architect records
   layout and error-handling facts with rationale, and the breakdown's tasks
   reference those decisions. **This is also the batching evidence 10a could not
   produce** — its run asked one combined question and was then cut short.
2. **The §0.5 Rust request under `--auto --allow-shell`:** all three stages run,
   `ask_user` appears zero times in the trace, every fact reads
   `source: "inferred"` (S10b.5 proven live, closing `A1.72`), and the coder's
   prompt carries the architect's layout fact.
3. **Brownfield:** a repo whose `facts.json` already records the stack, given a
   small request. The run must not re-ask what is already recorded, and the
   architect must build on the existing facts rather than contradict them.

---

## 7. Out of scope

- **Plan presentation and approval** (`C6.9`) — Step 10c. This spec produces the
  structured plan that one presents.
- **`AGENTS.md` as a living document** (`A1.9`, `C7.3`).
- **Retry around model invocation** (`A1.39`). Three stages mean three more
  places a transient provider error can end a run; that is the same defect, not
  a new one, and fixing it here would hide it.
- **The gate, the fix loop, termination.** S9c.1 holds: no stage can mark
  anything done.
