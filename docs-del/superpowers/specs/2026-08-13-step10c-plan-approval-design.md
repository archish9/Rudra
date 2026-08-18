# Step 10c — Plan Mode: Present → Approve → Execute (C6.9)

**Date:** 2026-08-13
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.9`. Completes the Step 10 decomposition (10a/10b/10c)
and with it Requirement #4, *plan before coding*.
**Depends on:** Step 10b (the three stages, and `breakdown` being the one
re-enterable stage) and Step 10a (the fact store) — by code, not just by order.

---

## 0. What this replaces

`C6.9` asks for "plan mode: present plan → approve → execute". Today none of the
three happens:

- **Present** — nothing prints a plan. The user sees a live trace of tool calls
  and finds out what Rudra decided by watching it happen.
- **Approve** — no gate exists between planning and the first coder dispatch.
- **`--plan`** — is a *permission mode* that denies every write and command
  (`permissions/rules.py:260`), so it produces a run that plans, is denied
  everything, and prints nothing anyone would call a plan. The flag's help text
  says "Plan only: make no project changes, run no commands" (`cli.py:603-605`),
  which is true and useless.

10b made this worth building: before it, "the plan" was a list of task strings.
Now it is facts with rationale plus tasks derived from them, which is something a
human can actually approve or reject.

---

## 1. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S10c.1** | Approval lands in `ask` mode; `--plan` presents and stops | The flag finally does what its name promises. `--auto` skips the gate, as it must |
| **S10c.2** | Verbs are **approve / revise / cancel** | Revise re-enters `breakdown` with the user's words, reusing the one stage 10b made re-enterable. A rejected plan costs one model call, not three |
| **S10c.3** | `run_loop` splits into `plan()` and `work()` | The conceptual seam is real: planning produces a ledger, working consumes one. `run_loop` survives as their composition so nothing that drives it breaks |
| **S10c.4** | Revisions are bounded at **3**, hard-coded | A user revising a fourth time wants to change the request, not the plan. An inert config key is worse than no key (CLAUDE.md §6) |
| **S10c.5** | `EOF` at the prompt is **cancel**, never approve | A plan must not execute because a pipe closed |

---

## 2. Architecture

```python
async def plan(request, *, context, planner, ledger=None) -> Ledger
    """The three 10b stages. Returns the ledger the breakdown filled."""

async def work(request, *, context, planner, ledger) -> AgentResult
    """The task loop, the reviewer, the summary."""

async def run_loop(request, *, context, planner, ledger=None) -> AgentResult
    """plan() then work(). Signature and behaviour unchanged."""
```

`run_loop` keeping its exact signature is load-bearing, not politeness: thirteen
cases in `tests/test_loop_run.py` plus the 9c/10b engine tests drive it, and a
seam that forces them all to be rewritten would be a seam bought with the
regression net. The new functions are used by `RudraAgent.run`, which is where
the decision belongs — the loop should not know what a terminal is.

```
RudraAgent.run
  ├─ ledger = await plan(...)
  ├─ present_plan(ledger, facts, console)
  ├─ if cfg.permissions.mode == "plan":   → report, stop. work() never runs
  ├─ if interactive:
  │     decision = ask_approval(console)
  │       approve → fall through
  │       revise  → planner(stage="breakdown", reason="revision", feedback=…)
  │                 re-present, up to 3 times
  │       cancel  → stop, exit 0
  └─ await work(...)
```

`--auto` and non-TTY never reach `ask_approval`: `mode == "ask"` already requires
a TTY and exits 2 otherwise (`cli.py:660`), so there is no third case.

---

## 3. What is presented

After 10b, the plan *is* the facts plus the tasks:

```
Plan
  facts:  language=Rust (asked) · cli_framework=clap (asked)
          layout=src/parser.rs parsing; src/main.rs CLI entry (inferred)
  t1  write a CSV parser that handles quoted commas
  t2  write tests for the parser

[a]pprove  [r]evise  [c]ancel >
```

Facts are shown **with their source**, because that is the difference between
"you told me this" and "I guessed this", and a user scanning a plan should have
their attention drawn to the guesses. Everything rendered goes through
`rich.markup.escape`: a fact value or task description containing `[bold]` must
appear, not vanish — that is A1.48 and A1.67's shared defect, and this is a new
surface with the same exposure.

### Revision

Revise prompts for one sentence and re-enters `breakdown` with a new reason,
`revision`, carrying the user's words **verbatim** — the same principle that
sends the gate's blocker back unparaphrased in 9c, and for the same reason: a
summary of an instruction is a worse instruction.

`consult_planner`'s re-entry guard (10b) already permits re-entry only for
`breakdown`, so clarify and architect cannot run again — the facts a user just
approved are not re-litigated by a revision to the task list.

On the fourth revision Rudra says the budget is spent and offers approve or
cancel only.

---

## 4. Failure modes

| Case | Behaviour |
|---|---|
| Breakdown declared no tasks | Present "no tasks were declared", stop. Approving an empty plan is meaningless |
| Revise, planner adds nothing | Re-present unchanged, say so, and count the revision |
| Revise with empty feedback | Re-prompt once. Empty input is a slip, not an instruction |
| `EOF` at either prompt | **Cancel** (S10c.5) |
| Unparseable answer | Re-prompt — `Prompt.ask(choices=…)` does this natively |
| `mode == "plan"` | Present, then stop before `work()`. The deny floor stays armed, so even a bug reaching `work()` writes nothing |
| `--auto` | No presentation, no prompt, straight to `work()` |
| Cancel | Exit **0**. Declining a plan is a successful run, not an error. `.rudra/` exists already from `ensure_layout`; nothing in the project is touched |

---

## 5. Testing

**No existing behaviour may change.** `run_loop` keeps its signature, so every
9c/10b test stands unmodified — and that is asserted rather than assumed.

| File | Covers |
|---|---|
| `tests/test_loop_split.py` (new) | `plan()` runs exactly the three stages and returns the filled ledger; `work()` runs the task loop against a pre-filled ledger and consults neither clarify nor architect; **`run_loop()` equals `plan()` then `work()`** — driven with identical fakes, comparing the resulting ledger and `AgentResult` |
| `tests/test_plan_approval.py` (new) | approve → `work()` runs; cancel → it does not, exit 0, ledger untouched; revise → `breakdown` re-entered with `reason="revision"` and the feedback verbatim; the 3-revision cap; empty feedback re-prompts; **EOF → cancel, never approve**; unparseable input re-prompts |
| `tests/test_plan_render.py` (new) | Facts and tasks both appear; an empty fact store renders no facts line; a task description containing `[bold]` survives verbatim |
| `tests/test_agent_wiring.py` (extend) | `--auto` never calls the approval function; `mode == "plan"` presents and never calls `work()` |
| `tests/test_cli_smoke.py` (extend) | `--plan` exits 0 having created no project file |

Approval is an **injected callable** on `RudraAgent`, defaulting to auto-approve,
so every test drives it with no TTY and no model — the property that makes 9c's
loop testable at all.

`uv run pytest -q` must not fall below **1017 passed, 2 skipped**. `ruff check`
and `ruff format --check` clean. Both absolute.

---

## 6. Acceptance

From `mktemp -d` git repos outside the repo, under `env -i`, against
`gemma4:31b-cloud`.

1. **`--plan "build a CLI in Rust with clap"` through a real pty** — the plan
   prints with facts and tasks, the process exits **0**, and `git status` shows
   **no** source file created. This is the run that proves the flag's help text
   for the first time.
2. **`ask` mode, approve** — plan presented, `a` typed, the run proceeds and
   completes as 10b's run 2 did.
3. **`ask` mode, revise then approve** — the feedback visibly changes the task
   list, and the revised plan then executes.
4. **`--auto`** — no prompt, no pause, behaviour unchanged from 10b.

Run 3 is the one that proves the row rather than the plumbing: a plan the user
changed, executed as changed.

---

## 7. Out of scope

- **Editing tasks by hand.** The revise loop covers it via the planner, and a
  hand-edit surface is a second way to mutate the ledger.
- **Persisting the approval** in the ledger. Nothing reads it, and D15's volatile
  subtree is not an audit log — `permissions.jsonl` is.
- **`A1.74`** (overlapping tasks blocking already-done work). Approval makes it
  *visible* to a user before it costs them a run, which is a mitigation, not the
  fix.
- **The gate, the fix loop, termination.** S9c.1 holds: the user approves *what
  to attempt*, and Python still decides what is done.
