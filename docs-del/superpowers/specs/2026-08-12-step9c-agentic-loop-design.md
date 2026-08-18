# Step 9c — The Agentic Loop (C6.1, C6.5, C6.5a, C6.10)

**Date:** 2026-08-12
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.1`, `C6.5`, `C6.5a`, `C6.10`, `A1.8`, `A1.24`, `A1.25`.
**Depends on:** Step 9a (`verify_project`) and Step 9b (`run_subagent`) — both by code, not just by order.
**Completes:** the Step 9 decomposition. Requirement #3 (loop engineering).

---

## 0. What this replaces

Rudra's current loop is a hardcoded Python `for` over filenames
(`main_agent.py:362-465`). It plans a list of bare filenames, writes each with a
fresh coder, and ticks an item off when **the file exists on disk**
(`main_agent.py:438-439`). A zero-byte file, a truncated file, and
`def main(): pass` all count as success. That is `A1.8`.

Step 9a built the gate that can tell the difference. Step 9b built the subagents
that do the work. Neither is wired to anything: `verify_project` has no caller in
an agent run, and `run_subagent` is invoked only by tests. 9c is where they meet.

### The contradiction this spec resolves

`C6.1` says "a real agentic loop: **the agent owns the todo list, picks the next
action**". D9 says "**no LLM judge decides termination**", and 9b's `S9b.1` chose
deterministic invocation for the same reason.

The owner's answer (`S9c.1`): **split them**. The model decides what work exists
and what to do next; Python decides when a task is done and when to stop. This is
what D9's own table already says — deterministic gate blocking, LLM advisory —
and it is why `RubricMiddleware` was rejected: not for being an LLM, but for
holding the exit door (`rubric.py:92`).

---

## 1. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S9c.1** | The model picks the work; Python owns the gate, the budget, and termination | `C6.1` and D9 are both satisfied structurally |
| **S9c.2** | A ledger entry is a **task**, with touched files recorded as an outcome | Tests and multi-file work become expressible; `changed_files` for the gate comes free |
| **S9c.3** | No-progress detection compares the **blocking stage plus its normalised findings** | Stable across runs; `output_tail` is excluded because it never repeats |
| **S9c.4** | A gate `escalate` stops the whole run; attempts-exhausted or no-progress blocks one task and continues | Environment problems stop once; code problems do not discard unrelated work |
| **S9c.5** | The tester fires in-loop when the gate produced no test judgement; the reviewer runs **once at the end**, advisory | Both 9b subagents gain a real consumer without either deciding anything |

---

## 2. Architecture

```
src/rudra/loop/
├── __init__.py   run_loop, Ledger, Task, TaskStatus
├── ledger.py     Task · TaskStatus · Ledger. Pure data plus load/save
├── tools.py      add_tasks · drop_task · read_ledger — typed, agent-facing
├── bounds.py     failure_signature() · tests_produced_no_judgement().
│                 Pure functions over a VerifyReport, no I/O
└── engine.py     run_loop() · run_task() · git_snapshot() · summarise().
                  The orchestrator, and the only module here that reaches a
                  model or the filesystem
```

Two helpers the pseudocode below uses, so their homes are not left implicit:
`tests_produced_no_judgement(report)` is a pure predicate over a `VerifyReport`
and lives in `bounds.py`; `git_snapshot(project)` wraps `git/core.py`'s `status`
and lives in `engine.py`, because it touches the filesystem.

### Deleted

From `src/rudra/agent/main_agent.py`: `RudraAgent.run` (`:362-465`),
`_parse_pending_files` (`:48`), `_check_off_file` (`:64`),
`_run_coder_for_file` (`:343`), `_stream_coder` (`:237`),
`_request_task_assignment` (`:328`).

From `src/rudra/tools/planning_tools.py`: `update_plan`, `read_plan`,
`write_task_assignment`, `looks_like_path` — the whole module. All four exist to
make a bare-filename checklist work, and the ledger replaces the checklist.

`_stream_planner` survives: the planner is still an agent that streams. Its
guards are the same ones `subagents/runner.py` already carries, and the two
should be read together when `C9.1` reworks the trace.

### Survives, because it is setup rather than loop

`build_backend`, `create_main_agent`, `AgentContext`, `AgentResult`,
`_ensure_agents_md`, `_write_tech_stack_file`, `_maybe_auto_branch`.
`create_main_agent` becomes the thing that assembles a `SubagentContext` and a
planner; `RudraAgent.run` becomes a thin call into `run_loop`.

### Dependency direction, one way only

```
cli.py → agent/main_agent.py → loop/engine.py → { loop/ledger, loop/bounds,
                                                  verify/, subagents/ }
```

`loop/` imports `verify` and `subagents`; neither imports `loop`. `ledger.py` and
`bounds.py` import nothing from Rudra at all — they are the two modules a test
drives with no model, no backend and no gate, which is where most of 9c's test
weight sits.

### The result contract is unchanged

`AgentResult(success, message, files_created, files_modified, iterations)` keeps
its five fields, because `cli.py:719-725` and `:801-806` render them.
`iterations` changes meaning honestly — from "files planned" to "tasks
attempted". A richer result is a Step 15 concern.

---

## 3. The ledger

```python
# loop/ledger.py

class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"          # the gate passed. Only the engine sets this
    BLOCKED = "blocked"    # gave up. Only the engine sets this
    DROPPED = "dropped"    # the agent retracted it


@dataclass
class Task:
    id: str                             # "t1", "t2" -- short, stable, quotable
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    files_touched: tuple[str, ...] = ()
    last_signature: str | None = None
    note: str = ""                      # why blocked, or why dropped


@dataclass
class Ledger:
    tasks: list[Task]

    @classmethod
    def load(cls, path: Path) -> Ledger: ...
    def save(self, path: Path) -> None: ...   # temp file + os.replace
    def next_pending(self) -> Task | None: ...
    def counts(self) -> dict[str, int]: ...
```

Stored at `.rudra/run/ledger.json`, added to `RudraPaths` (`state/paths.py:60`) —
never built by hand, per CLAUDE.md §3.

### Three tools, and the omission is the design

| Tool | The agent can |
|---|---|
| `add_tasks(descriptions: list[str])` | declare work, including work discovered mid-run |
| `drop_task(task_id: str, reason: str)` | retract work it judges unnecessary |
| `read_ledger()` | see current state |

**There is no tool that sets `DONE`.** Only `engine.py` writes it, and only when
`VerifyReport.passed` is true. `BLOCKED` is likewise engine-only. This is the
same structural move as 9b's missing `middleware` field: D9's rule is enforced by
there being no way to express the alternative, not by a prompt asking nicely.

The tools take **typed arguments**, so the model never emits JSON syntax. That is
what lets the format be JSON without reintroducing the parsing problem `PLAN.md`
had — the format the model writes and the format Rudra reads are no longer the
same artefact.

### `drop_task` is a deliberate, bounded risk

Without it, a planner that adds a mistaken task condemns the loop to burn the
full attempt budget on it. With it, the agent has a way to empty the ledger and
end the run. It is bounded by visibility rather than by prohibition: a drop
requires a reason, is printed when it happens, and every dropped task appears in
the final summary. A run that dropped its way to nothing reports exactly that,
and is not a success (§6).

### This closes A1.25 by construction

`A1.25` is `total = len(pending_files)` computed *after* a silent filter
(`main_agent.py:374`), so a requested item vanished from both the work and the
summary. Here nothing is filtered: every task the agent declares exists in the
ledger with a terminal status, and the summary reports
requested / done / blocked / dropped. Counts that do not add up become
impossible rather than merely unlikely.

---

## 4. The loop

```python
def run_loop(context, request) -> AgentResult:
    ledger = Ledger(tasks=[])
    consult_planner(ledger, request, reason="initial")
    consulted_on_empty = False

    while True:
        task = ledger.next_pending()
        if task is None:
            if consulted_on_empty:
                break                      # the planner had nothing more to add
            consult_planner(ledger, request, reason="ledger_empty")
            consulted_on_empty = True
            continue

        outcome = await run_task(task, ledger)
        if outcome is STOP_RUN:
            break
        if outcome is BLOCKED:
            consult_planner(ledger, request, reason="blocked", task=task)

    if any(t.status is TaskStatus.DONE for t in ledger.tasks):
        await review_once(context)         # advisory, printed, acts on nothing
    return summarise(ledger)
```

```python
async def run_task(task, ledger) -> Outcome:
    tested = False
    while task.attempts < cfg.agent.max_fix_attempts:
        task.attempts += 1

        before = git_snapshot(project)
        result = await run_subagent("coder", prompt_for(task), context=context)
        task.files_touched = git_snapshot(project) - before

        if result.error:
            return STOP_RUN                # never ran: environment-class

        # result.halted_reason needs no branch: a guard firing means this
        # attempt went badly, and the gate below is what says how badly.
        # Recording it on the task is for the summary, not for control flow.
        if result.halted_reason:
            task.note = result.halted_reason

        if not task.files_touched:
            task.note = "the coder wrote nothing"
            continue                       # wrote nothing -- see §5

        report = verify_project(project, changed_files=task.files_touched, …)

        if report.escalate:
            return STOP_RUN
        if report.passed:
            if not tested and tests_produced_no_judgement(report):
                tested = True
                await run_subagent("tester", tester_prompt(task), context=context)
                continue                   # re-verify with the new tests
            task.status = TaskStatus.DONE
            return DONE

        signature = failure_signature(report)
        if signature == task.last_signature:
            task.note = "no progress: the same failure twice"
            return BLOCKED
        task.last_signature = signature

    task.note = f"{task.attempts} attempts exhausted"
    return BLOCKED
```

### `files_touched` comes from git, not from the model

A snapshot before and after each coder call, differenced through `git/core.py`'s
`status`. Asking the coder what it wrote invites a wrong answer at exactly the
moment the answer matters, because it feeds 9a's stub scan. Outside a git
repository the snapshot is unavailable and the gate falls back to whole-project
scanning, the same choice `rudra verify` makes.

### The planner is consulted on stalls, not on successes

Once at the start, once when the ledger empties, and once each time a task
blocks — where it can add a different approach or drop something it now judges
wrong. **Never after an ordinary success.** That is what bounds this design's
model-call cost to roughly one extra call per stall rather than one per task.

### The tester fires at most once per task

Only when the gate reports the test stage produced no judgement
(`not_applicable` — no command declared, or nothing collected). The loop then
re-verifies. `tested` is per-task, so a project that genuinely has no tests
cannot ping-pong between the tester and the gate.

### One 9b artefact changes: the coder's prompt

`subagents/registry.py`'s `_CODER_PROMPT` says "write the ONE file you are asked
to write" and "After write_file() returns successfully, STOP". That was correct
for the file-per-item loop being deleted and is wrong for task-shaped work that
may touch three files. It becomes: do what the task describes, write every file
it needs, stop when the task is complete.

The retry prompt appends the gate's blocker **verbatim** — findings with
`file:line`, not a summary. A paraphrase of a type error is a worse input than
the type error.

### One new config key, and it is live

`[agent] max_fix_attempts = 3`, matching the existing three-attempt convention
(`main_agent.py:408`). No other key is added; CLAUDE.md §6 is explicit that an
inert config key is worse than no key.

---

## 5. Bounds and failure handling

```python
# loop/bounds.py -- pure functions, no I/O

def failure_signature(report: VerifyReport) -> str:
    """A stable fingerprint of *what is wrong*, ignoring how it was said."""
    blocker = report.blocker
    if blocker.findings:
        parts = sorted(f"{f.file}:{f.line}:{f.message}" for f in blocker.findings)
    else:
        parts = [blocker.detail]          # e.g. "3 file(s) do not parse"
    return sha256("\n".join([blocker.name, *parts]).encode()).hexdigest()[:16]
```

`output_tail` is excluded deliberately: it carries durations, absolute temp
paths, and pytest's ordering seed, so hashing it would almost never match and the
detector would never fire — the failure mode `C6.5a` exists to prevent. Findings
are already structured records from 9a, which is what makes this stable. A
changed line number counts as progress, correctly: the model moved the defect.

### Every terminal condition

| Case | Outcome | Why |
|---|---|---|
| `report.passed` | `DONE` | The gate is the only thing that grants this |
| `report.escalate` | **STOP_RUN** | Missing tool, denied command, internal error — every remaining task hits the identical wall |
| `SubagentResult.error` | **STOP_RUN** | Set only for a build failure or a mid-stream exception; both recur task after task |
| `SubagentResult.halted_reason` | failed attempt → retry | A guard fired: this subagent misbehaved on this task. Local, so the fix loop absorbs it |
| Failed, new signature, budget left | retry | Something changed |
| Failed, same signature twice | `BLOCKED` | `C6.5a`'s no-progress rule |
| `attempts == max_fix_attempts` | `BLOCKED` | Budget exhausted |
| Coder wrote nothing, `ok=True` | failed attempt | See below |

The `error` / `halted_reason` split is 9b's `SubagentResult` used exactly as
designed: "never ran" and "misbehaved" are different failures with different
remedies, which is why they were separate fields.

### The empty-diff guard

With `files_touched` empty, 9a's syntax stage reports "0 files parsed" and the
stub scan "0 changed file(s) scanned", so a task where the coder did nothing
could **pass the gate and be marked `DONE`**. An empty diff is therefore a failed
attempt, not a pass. It is the one place the loop must not trust a green report,
and it exists because the gate is scoped to changed files (`S9a.5`) — a correct
decision whose edge this loop is responsible for.

### Durability, and what is not resumed

The ledger is written after every status change, via temp file plus
`os.replace`, so a crash leaves a readable record of how far the run got.

It is **not resumed**. Each run starts a fresh ledger: the file lives in `run/`,
D15's volatile subtree, and a second invocation carries a different request for
which last run's pending tasks are noise. Resumption is `C7.2`'s `--continue`,
and **`A1.2` stays open** — 9c does not touch checkpointing.

---

## 6. The planner's new job, and what the user sees

The planner stops being a file-lister. Its tools change from `planning_tools`
(deleted) to `loop/tools`, and its prompt drops every mention of `PLAN.md`,
`current_task.md`, and bare filenames. It now:

- reads the request, the project tree, and `tech_stack.md`
- calls `add_tasks([...])` with descriptions of *work*, not filenames
- may call `ask_user` when it genuinely cannot infer something
- never writes project code, and **cannot mark anything done**

CLAUDE.md §3 warns that agent-facing prompts name `.rudra/` paths as literal
strings and must move in the same commit as the path. Here the paths stop being
named at all — the ledger is reached through tools, so the prompt has no path to
drift from. `tests/test_rudra_dir_migration.py` guards this and needs its
`PLAN.md` expectations updated in the same commit.

### Four consult reasons

| Reason | What it asks |
|---|---|
| `initial` | Break this request into tasks |
| `ledger_empty` | Everything is done; is anything missing before we stop? |
| `blocked` | Task *t3* failed with *«blocker findings»*. Add a different approach, or drop it |
| — | *(no consult after an ordinary success)* |

### The final summary, where A1.25 dies

```
Tasks: 4 requested · 3 done · 1 blocked · 0 dropped

  ✓ t1  add a CSV parser
  ✓ t2  write tests for the parser
  ✗ t3  handle quoted commas          no progress: the same failure twice
  ✓ t4  update the README

Files created: 3 · modified: 1
```

**A `STOP_RUN` leaves tasks still `pending`, and they are reported too** — this is
the case the summary most needs to get right, because it is the one where the run
ended early and the user is most likely to assume the rest simply passed:

```
Tasks: 4 requested · 1 done · 1 blocked · 2 never attempted

  ✓ t1  add a CSV parser
  ✗ t2  write tests for the parser    lint, typecheck and test were denied
  ·  t3  handle quoted commas          not attempted — the run stopped
  ·  t4  update the README             not attempted — the run stopped

Run stopped early: the permission gate denied every command. Re-run with
--allow-shell, or see Documentation/10-verification.md.
```

Every task the agent ever declared appears with a status and, when it is not
`done`, the reason. There is no filter between what was requested and what is
reported, and `requested == done + blocked + dropped + pending` is asserted as a
test rather than assumed (§7).

`success` is `True` when every task is `DONE` or `DROPPED` **and at least one is
`DONE`**. A blocked task makes the run unsuccessful even if three others landed —
the user needs a non-zero exit to notice. A run that dropped its way to nothing is
unsuccessful too.

The live trace keeps today's shape (`_log_always`, Rich, one line per event) and
gains the gate verdict per attempt, so a user watching sees *why* a retry
happened rather than only that it did.

---

## 7. Testing

### Unit layer

`ledger.py` and `bounds.py` import nothing from Rudra, so they need no fakes.

| File | Covers |
|---|---|
| `tests/test_loop_ledger.py` | Round-trip; `os.replace` atomicity (a crash mid-write leaves the old file intact); `next_pending` ordering; counts |
| `tests/test_loop_bounds.py` | Signature stable across identical failures; *changes* when one of four findings is fixed; excludes `output_tail`; the no-findings fallback to `detail` |
| `tests/test_loop_tools.py` | `add_tasks` / `drop_task` / `read_ledger`. **The invariant: no tool can produce `DONE` or `BLOCKED`**, asserted by enumerating the tools' schemas so a fourth tool cannot quietly grant it |
| `tests/test_loop_engine.py` | Every row of §5's table, with `run_subagent` and `verify_project` monkeypatched: escalate → STOP_RUN, `error` → STOP_RUN, `halted_reason` → retry, same-signature → BLOCKED, budget → BLOCKED, empty diff → failed attempt, tester fires once, planner consulted on block and on empty but **not** on success |
| `tests/test_loop_summary.py` | `A1.25` as an executable claim: for any ledger, `requested == done + blocked + dropped + pending`, and every task appears in the rendered summary |

### Acceptance — three live runs

From a `mktemp -d` git repo outside the repo, under `env -i`, configured only by
`.rudra/config.toml`.

1. **Greenfield, end to end.** `rudra --auto --allow-shell "build a CSV parser
   with tests"`. Expect the planner to declare tasks, the coder to write them,
   the gate to pass, `ledger.json` to show every task `done`, and the summary to
   match the files on disk. The generated code is then executed by hand to
   confirm it works — the bar Steps 2, 5 and 6 used.

2. **The environment wall.** The same project under `--auto` *without*
   `--allow-shell`. The gate's command stages are denied, `escalate` is true, and
   the loop must **stop on the first task** rather than reproducing one message
   for every task. Deterministic, and it exercises STOP_RUN on real machinery.

3. **No-progress, forced deterministically.** Seed the project with a
   pre-existing type error in a module the request never mentions. Typecheck is
   project-wide, so every attempt fails with the *same* signature whatever the
   coder writes. Expect attempt 1 to fail, attempt 2 to produce an identical
   signature, the task to be `BLOCKED` with `no progress: the same failure
   twice`, and the run to report it rather than looping to the budget.

Run 3 earns its place as an acceptance case rather than a unit test because it is
a real situation — it is `A1.59` on Rudra's own tree — and because a loop that
cannot stop is precisely what `C6.5a` exists to prevent.

### Gates, unchanged

```
uv run ruff check src/ tests/      → All checks passed!
uv run ruff format --check src/ tests/
uv run pytest -q                   → never below 827 passed, 2 skipped
```

---

## 8. Ledger impact

### Closed

- **`C6.1`** — the file-by-file Python loop is deleted; the agent owns the task list.
- **`C6.5`** — `write → verify → diagnose → fix → reverify`, deterministic gate only.
- **`C6.5a`** — attempt budget plus no-progress detection on a stable signature.
- **`C6.10`** — the self-maintained ledger; `PLAN.md` and its tools are deleted.
- **`A1.8`** — success becomes `VerifyReport.passed` instead of "the file exists".
  This is the row 9a built the gate for and 9b left open.
- **`A1.25`** — no filter between requested and reported; §3 explains why the
  counts are structurally forced to agree.
- **`A1.24`** — closed **by deletion**. Its row is about `looks_like_path`
  mis-classifying single-word prose in a plan checklist; the function and the
  checklist both go. It was re-pointed here from `C6.6` during 9a for exactly
  this reason.

### Open by design

- **`A1.2` / `A1.3`** — checkpoint continuity. The ledger is written continuously
  and never resumed; `--continue` is `C7.2`.
- **`A1.39`** — no retry around model invocation. The loop *stops* on a provider
  error rather than retrying: containment, not a fix.
- **`A1.9`** — `AGENTS.md` still written once, never updated.
- **`A1.20`**'s remaining half — the `processed`-counter shape, re-pointed to
  `C9.1` during 9b.
- **`A1.59`** — Rudra's own 69 mypy errors, untouched. Acceptance run 3
  deliberately reproduces the same class in a scratch project.

### Re-pointed rather than closed

- **`A1.40`** (`--dry-run` previews nothing) currently lives in deleted code —
  the early return at `main_agent.py:365-372`. The flag keeps today's behaviour:
  `run_loop` returns before consulting the planner. The row stays open, its
  citation moved to its new home.
- **`A1.52`** (factories read `Path.cwd()` rather than their argument) is
  adjacent to every constructor 9c touches. Not fixed — it is a separate defect
  with its own evidence — but its citations move.

---

## 9. Out of scope

- No `--continue`, no checkpoint resumption, no retry policy.
- No three-stage planning, no clarifying-question budget, no plan mode — those
  are `C6.7`–`C6.9`, Step 10.
- No changes to `verify/` or `subagents/` beyond the coder prompt revision in §4.
- No new CLI commands; `AgentResult` keeps its five fields.
- No fix for `A1.59`, `A1.39`, `A1.9`, or `A1.2`.
