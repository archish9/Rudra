# Step 8 — Git Tools and the Test Runner: Design

**Date:** 2026-08-11
**Ledger rows:** `C3.5`, `C3.6`, closes `A1.33(b)` and `A1.53`
**Depends on:** Step 7 (`C3.1`–`C3.4`, `U.7` — shell execution and the permission layer) — DONE
**Unblocks:** Step 9 (`C6.1`–`C6.6` — the agentic loop rewrite). `C6.6`'s deterministic
completion gate consumes this step's `TestResult`; `C6.5`'s fix loop has nothing to
loop on until it exists.

---

## 1. Goal

Give Rudra two things it has never had: a way to know whether the code it wrote
passes, and a way to isolate its work from the user's branch.

| Row | Deliverable |
|---|---|
| `C3.5` | Git: status, diff, branch, commit, log. Auto-branch before an agent run; never commit unless asked |
| `C3.6` | Test runner: detect the stack's test command, run it, parse failures into structured results the fix loop consumes |

Both are shell commands underneath, and shell landed in Step 7 gated. Nothing
here weakens that gate; everything here goes through it.

The load-bearing deliverable is `C3.6`. Step 9's whole premise — code → test →
review → fix — is a loop with no exit condition until something can answer *did
the tests pass*. `C3.5` rides along because it shares the same primitive and
because Step 9's reviewer needs a working-tree diff.

---

## 2. Findings that shaped the design

### 2.1 `resolve_test_command` already exists

`src/rudra/stacks/detect.py:68` already answers "what is this project's test
command", returning an argv list or `None`. Step 4 built it and Step 4's own
docstring reserved the rest for here: *"it does not execute anything (that is
C3.6's job, Step 8)"* (`detect.py:4`).

So `C3.6` is not detection. It is execution, parsing, and the result type.

### 2.2 `registry.py`'s Python test command cannot launch — `A1.33(b)`

`PYTHON.test_command` is `("pytest",)` (`src/rudra/stacks/registry.py:14`) — a
bare executable name. It is not on `PATH` in any project virtualenv Rudra did
not itself activate, which is the common case, and it is simply wrong for a
Django project (`manage.py test`) or a stdlib-`unittest` project that never
installed pytest.

This row has been `PENDING` since Step 4 and is closed here rather than deferred,
because `C3.6` consumes exactly this field. Shipping the runner against it would
produce a "no tests" answer for a project that has tests — a false negative fed
straight into Step 9's gate.

### 2.3 The gate fails open on unrecognised tool names — `A1.53`

Logged to the ledger before this spec was written, per session rule 2.

`PermissionEngine.decide` falls through to the mode default for any tool outside
its four sets, returning `Decision("ask", …)` in the default mode
(`src/rudra/permissions/rules.py:244`). `build_interrupt_on` registers entries
only for `MUTATING_TOOLS` (`src/rudra/permissions/interrupts.py:43`). And
`RudraPermissionMiddleware._check` falls through to the handler for any effect
that is not `deny` (`src/rudra/permissions/middleware.py:76-78`).

`ask` with no interrupt registered is therefore neither a prompt nor a denial.
The call runs, and `AuditLog.record` is never reached, so nothing records it.

Reachable today via `read_plan`, which is in none of the four sets. Harmless in
itself — it reads `.rudra/run/PLAN.md` — which is why ~190 permission tests did
not surface it. **The defect is the fall-through, not `read_plan`.** This step
registers two new tool names and would land on it directly, so it is fixed here.

### 2.4 The `go test` row contradicts D18

`C3.6`'s row text says "detect pytest / jest / go test / cargo".
`src/rudra/stacks/registry.py:4` says: *"Deliberately absent: Go, Java, Ruby, C#
— not requested (TODO.md D18)."* D18 names five targets: Python, Rust, Node,
React/Next.js, Angular.

`registry.py` is the source of truth. The row text predates D18. **No Go.** See
§8, decision S8.6.

### 2.5 Unattended runs cannot self-verify without an explicit opt-in

`A1.49` resolved that `execute` is denied under `--auto` unless the user passes
`--allow-shell` or sets `[tools] shell_in_auto = true`. The test runner is gated
as `execute` (§4.2), so it inherits that.

This is correct and stays. `pytest` executes the test files the *model* wrote,
so it is a genuine arbitrary-code path — the same class `A1.49` measured, not a
lesser one. The consequence, stated here so Step 9 inherits it rather than
rediscovering it: **`--auto` alone produces unverified code.** Loop-engineering
runs want `--auto --allow-shell`. `A1.49`'s own row already anticipated this:
*"`C3.6`'s test runner and `C6.5`'s fix loop need this flag to run unattended."*

---

## 3. Scope

**In:** `C3.5`, `C3.6`, `A1.33(b)`, `A1.53`.

**Out, and deliberately so:**

| Row | Why not here |
|---|---|
| `C11.2` | Per-stack acceptance matrix. Not in Step 8's row; needs one live agent run per stack |
| `C11.3` | Angular's headless-browser preflight. Not in Step 8's row. §7's timeout keeps a missing browser from hanging the loop forever, which is the failure `C11.3` exists to make *legible*, not the fix itself |
| `C6.x` | The fix loop consumes `TestResult`; it does not ship here |
| A git commit tool | See §5.4 — Rudra never commits, and no tool is built for it |

The owner's instruction on 2026-08-11 was to follow Step 8's two rows as written,
neither narrowed nor widened. `A1.33(b)` and `A1.53` are inside that boundary
because this step's code cannot be correct without them, and both were already
in the ledger — neither is new work.

---

## 4. Architecture

### 4.1 Module layout

```
src/rudra/stacks/            pure detection — unchanged except registry.py (§6.1)
        │
src/rudra/shell/runner.py    run_gated()  ← the ONLY subprocess call site in Rudra
        │                            │
src/rudra/git/core.py        src/rudra/testing/runner.py
src/rudra/tools/git_tools.py src/rudra/tools/testing_tools.py
```

> **Amended 2026-08-11, after implementation.** As designed, the two tool
> files sat inside their own packages (`git/tools.py`, `testing/tools.py`).
> The owner pointed out that `src/rudra/tools/` already existed and is where
> a reader looks to answer "what can the model actually do?" — and that
> splitting tool definitions across three directories makes that question
> harder than it should be.
>
> The three approaches offered during design all assumed new top-level
> packages, so that alternative was never on the menu. The justification
> given at the time — that `tools/` holds tool definitions while these are
> subsystems — was also weaker than stated: `tools/planning_tools.py`
> already exported `looks_like_path`, a pure helper `main_agent` imports
> directly. The difference was proportion, not category.
>
> Resolved by moving only the tool surface: `tools/` now holds every tool
> and nothing else, while `git/core.py` and `testing/{runner,parse}.py`
> keep the subsystem logic the orchestrator calls with no model in the loop.
> The cohesion argument the original layout rested on is preserved for the
> code that actually carries it. Everything else in this spec stands.

Three modules rather than two. Both `C3.5` and `C3.6` need the same primitive —
*run a command, through the gate, capture output* — and that primitive is the
security-relevant half. Two copies of a gate check is the thing that drifts, so
there is one.

`git/` and `testing/` do not import each other. `testing/` imports `stacks/`;
`git/` does not.

`stacks/` stays pure. Putting the runner in `stacks/run.py` would sit it beside
the detection it consumes, but it would also contradict `detect.py:4`'s own
promise that the module executes nothing.

### 4.2 Everything reaches the gate as `execute`

New tools do **not** get new permission-rule names. `run_gated` renders argv with
`shlex.join` and asks the engine to decide as `tool="execute"` with the real
command string.

Three properties fall out, and all three are why:

1. **No new config vocabulary.** `ALL_GATED_TOOLS` (`rules.py:38`) is a closed
   set and `parse_rule` raises on anything outside it (`rules.py:88`). Users
   already write `allow = ["execute:pytest*"]` and `deny = ["execute:git push*"]`;
   those keep working, unchanged, and now cover the new tools too.
2. **The audit log records the real command**, not an opaque `run_tests`.
3. **`A1.49`'s `--auto` opt-in covers them automatically** — correct, since they
   *are* shell.

### 4.3 `run_gated`

```python
@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    command: str              # shlex.join rendering: what the gate matched, what the audit logged
    exit_code: int | None     # None = denied, timed out, or the binary was missing
    stdout: str
    stderr: str
    denied: bool
    denial_reason: str | None
    timed_out: bool

def run_gated(argv, *, cwd, gate, console, timeout, read_only=False) -> CommandResult
```

Decision handling:

| Engine effect | Behaviour |
|---|---|
| `allow` | run; `audit.record(..., outcome="allow")` |
| `deny` | return `denied=True`; `audit.record(..., outcome="deny")`; never raise |
| `ask` | `gate.prompt([{"name": "execute", "args": {"command": cmd}}], console)` |

The `ask` path reuses `Gate.prompt` (`permissions/__init__.py:67`) verbatim. It
already accepts exactly that request shape and returns
`[{"type": "approve" | "reject"}]`, so a Python-side command gets the same panel,
the same `[A]lways` grant, and the same audit line as a model-initiated one.
**No new prompt code, and no second place where an approval decision is made.**

`env=scrubbed_env(cfg)` — the same scrubbing `build_backend` applies (`A1.44`),
so a subprocess cannot leak keys the shell tool cannot.

`run_gated` never raises for a non-zero exit, a denial, a timeout, or a missing
binary. Every one of those is a value. See §7.

### 4.4 Read-only git bypasses the decision

`read_only=True` skips the engine decision for a fixed frozenset of read-only git
subcommands — `rev-parse`, `status --porcelain`, `log`, `diff`, `branch --list`.

The reason is that `auto_branch` fires three of them before the planner starts,
and prompting for `git rev-parse --is-inside-work-tree` in `ask` mode on every
run would make the gate an annoyance rather than a control.

It is consistent with policy already in force, not an exception carved for
convenience: reads are never gated (`rules.py:228`) and are never a floor
violation (`floor.py:32-33`).

The bypass is bounded by construction: it applies only to argv **Rudra composes**
from that frozenset, and never to any command that writes. Owner-approved during
design (S8.7).

**The one place a model-supplied value enters a bypassed command** is
`git_diff(path=…)`. That argument is not passed through: it is resolved against
the project root using the same logic as `PermissionEngine._resolve`
(`rules.py:164`), and a path landing outside the root drops out of the bypass
into a full gate decision. Without this, `git_diff("../../other-repo")` would be
a read of an arbitrary repository that never reached the engine. `run_tests`
takes no path argument at all — its argv comes entirely from `testing.resolve`
(§6.1).

---

## 5. `C3.5` — git

### 5.1 Surface

One Python module and exactly one model-facing tool.

The model already has gated `execute`, so it can run `git status` today. A new
tool only earns its place by parsing output or constraining argv — otherwise it
is a second spelling of a capability that already exists, costing schema tokens
in a 32B window (D6).

**`git/core.py`** — the Python API, used by the orchestrator and by Step 9:

| Function | Notes |
|---|---|
| `is_repo(path)` | `git rev-parse --is-inside-work-tree` |
| `current_branch(path)` | `None` on detached HEAD |
| `is_clean(path)` | `git status --porcelain` empty |
| `create_branch(path, name)` | |
| `status(path)` | porcelain parse → index/worktree codes + path |
| `diff(path, *, staged=False, max_lines)` | capped |
| `log(path, n)` | `--format=%H%x00%an%x00%s`, NUL-separated so subjects containing colons parse |
| `auto_branch(path, task, …)` | the orchestrator entry point — §5.3 |

**`tools/git_tools.py`** — `git_diff` and nothing else. A capped, parsed working-tree
diff is precisely what raw `execute` does badly: unbounded diff output blows a
32B context. Cap ~400 lines / 8 KB with an explicit truncation notice.

### 5.2 Why not five tools

The literal reading of the `C3.5` row is one tool per verb. Rejected: four of the
five would duplicate `execute` without parsing anything, and `commit` is not
built at all (§5.4). What remains is one tool that does something `execute`
cannot.

`C3.5`'s row is still satisfied — status, diff, branch, and log all exist as
functions, reachable by the orchestrator and by the model through `execute`.

### 5.3 Auto-branch is opt-in

```toml
[tools]
auto_branch = false      # default
```

When enabled, `auto_branch` runs only if **all** preconditions hold:

- inside a git work tree
- HEAD is not detached
- the working tree is clean

Any precondition failing prints which one and returns `None`. **It never fails
the run.**

The dirty-tree precondition is not fussiness: `git checkout -b` carries
uncommitted changes onto the new branch, and fails outright where it would
clobber. Branching a dirty tree is the case most likely to surprise someone who
ran Rudra in a repo they care about.

Default-off follows the precedent D5 set and `A1.49` reinforced: unrequested
changes to the user's state are opt-in. Auto-branch mutates the user's repo
before the model has done anything.

**Branch naming:** `rudra/<slug>`, slug derived from the task — lowercased,
non-alphanumeric runs collapsed to `-`, capped at ~40 characters, validated
against git ref rules (no leading `-`, no `..`, no trailing `.`). A name already
in use gets a numeric suffix.

### 5.4 "Never commit unless asked"

Rudra's orchestrator has **no commit code path**. That is the whole of the
enforcement on Rudra's own side.

For the model, the mechanism is the config template and the prompt, not a
shipped deny rule. Shipping defaults into `permissions.deny` would be a trap:
deny is evaluated at `rules.py:212` and allow only at `:223`, so a shipped deny
would be **un-overridable** by any allow rule the user writes.

Instead, `rudra init` scaffolds these commented out, with a sentence explaining
them:

```toml
# Uncomment to stop the agent committing or pushing on its own.
# deny = ["execute:git commit*", "execute:git push*", "execute:git reset --hard*"]
```

Plus a planner-prompt rule: do not commit unless the task asks for it.

This delivers nothing automatically, which is acceptable because the exposed path
is already narrow. Under `--auto` without `--allow-shell`, `execute` is denied
outright (`rules.py:239`). In `ask` mode the user reads every command before it
runs. The only gap is `--auto --allow-shell`, which is itself an explicit opt-in
to exactly this class of risk.

---

## 6. `C3.6` — the test runner

### 6.1 Command resolution, and `A1.33(b)`

`testing.resolve(project_path)` wraps `stacks.detect` + `resolve_test_command`,
then applies the fix `A1.33(b)` requires for Python. Resolution order, first hit
wins:

1. the project's own virtualenv — `.venv/bin/pytest`, `venv/bin/pytest`
2. `python -m pytest` (via the venv's interpreter where one exists)
3. Django — `manage.py test`, when `manage.py` is present
4. stdlib — `python -m unittest`

Rust, Node, React, and Angular are unchanged: `cargo test`, and `npm test` where
`package.json` declares `scripts.test`.

Rudra's own toolchain is not involved. D18 is explicit that Rudra being a Python
project and the *user's* project being a Python project must never be conflated;
resolution reads the target project's layout, never Rudra's `.venv`.

### 6.2 `TestResult`

```python
@dataclass(frozen=True)
class TestResult:
    available: bool           # False = the project declares no test command
    command: tuple[str, ...] | None
    stack: str | None
    exit_code: int | None
    passed: bool
    total: int | None         # None when the summary line did not parse
    failed: int | None
    skipped: int | None
    output_tail: str
    launch_error: str | None
    denied: bool
    timed_out: bool
```

`available=False` is a real answer, not a failure — `detect.py:68` already
established that, and Step 9's gate can act on "this project has no tests".

`launch_error` is separate from `available` on purpose: *"no test command
declared"* and *"the command is declared but the binary would not start"* must
not look alike to a completion gate. Collapsing them is how a broken environment
gets reported as a passing project with no tests.

### 6.3 Parsing depth

Exit code, counts, and a failure tail.

`exit_code` is always authoritative. Counts come from each runner's own
human-readable summary line via one small regex per stack, and are `None` when
the line does not match — degrading honestly rather than guessing.

Deeper structure was rejected for a measured reason, not a stylistic one:
`pytest --json-report` requires a third-party plugin Rudra cannot install into a
user's project, and `cargo test --format json` is nightly-only. A precise path
that exists for some stacks and not others means Step 9 must handle the degraded
shape regardless — so the degraded shape is the contract.

### 6.4 Output offload

Full stdout/stderr is written to `.rudra/run/logs/tests.log`; only the tail
(~8 KB) travels in `TestResult.output_tail`.

This is D17's stated intent — *"big output → `.rudra/logs/`, short preview
returned"* — which `C3.2` correctly declined to build for **tool results**,
because deepagents already evicts those. A Python-side orchestrator call gets no
deepagents eviction, so it needs doing here. Note `A1.47`: the eviction threshold
is unreachable through `create_deep_agent`, which is another reason the Python
path cannot rely on it.

### 6.5 Registration — planner only

`run_tests` and `git_diff` are registered on the planner. The coder stays
`tools=[]`.

The coder's system prompt says *"After write_file() returns successfully, STOP.
Do not write more files"* (`coder_agent.py:56`). Handing it a test runner would
contradict its own instructions in the same context window, which is the failure
class D6 assumes away and D4's accepted-risk note warns about. The planner is the
only agent with a tool list and it persists across the whole run.

Step 9 re-decides this when subagents become a designed feature (`C6.2`).

### 6.6 The `A1.53` fix

Two changes, both minimal:

1. **`RudraPermissionMiddleware` receives the `interrupt_on` keys.** An effect of
   `ask` for a tool with no interrupt entry becomes a **deny**, with a message
   naming the unregistered tool. Fails closed, and requires no enumeration of
   every tool deepagents might register.
2. **`read_plan` joins `READ_ONLY_TOOLS`**, closing the instance that exists
   today.

The two new tool names are added to `ALL_GATED_TOOLS` and decided as
`allow` with source `"wrapped-execute"`: their inner `run_gated` performs the
real `execute:` decision, so deciding them again at the middleware would
double-prompt for one command.

---

## 7. Error handling

Every failure is a return value. Nothing in this step aborts a run.

| Condition | Result |
|---|---|
| `git` binary absent | `CommandResult(exit_code=None)`; `auto_branch` → `None` + printed reason |
| Not a git work tree | `auto_branch` → `None` + reason; `git_diff` returns an explanatory string |
| Detached HEAD / dirty tree | `auto_branch` skips, naming the precondition; the run continues |
| Branch name taken | numeric suffix |
| Denied by the gate | `denied=True`; the tool returns the denial text so the model adapts instead of retrying |
| Timeout | `timed_out=True`, process group killed |
| No test command declared | `TestResult(available=False)` |
| Declared but binary missing | `available=True`, `launch_error` set — distinct from the row above (§6.2) |
| Summary line unparseable | counts stay `None`; `exit_code` remains authoritative |

`[tools] test_timeout = 600` (seconds) is a real config key rather than a
constant. Angular's Karma builder hangs indefinitely without a browser — that is
`C11.3`'s exact concern — and no single number fits both a three-second unit
suite and a real integration run. Inside Step 9's loop a hang stalls the loop
instead of failing a round, so the timeout is what keeps that failure legible.

Both new keys (`auto_branch`, `test_timeout`) are live on arrival. `A1.47`
established that an inert config key is worse than no key.

---

## 8. Owner decisions taken during design

| # | Decision |
|---|---|
| S8.1 | The test runner is **both** a Python core and a thin tool wrapper. Step 9's orchestrator and gate call the Python function directly; the agent gets a tool so it is not blind to test state mid-task |
| S8.2 | New tools are gated as **`execute:<command>`**, not as new rule names. No growth in config vocabulary; the audit log records the real command |
| S8.3 | Auto-branch is **opt-in, default off**, and fires only from a clean tree on a non-detached HEAD. It never fails the run |
| S8.4 | Git ships as a **Python module plus one read-only `git_diff` tool**. Not five tools, not one subcommand blob |
| S8.5 | "Never commit unless asked" is delivered by **commented deny lines in the config template** plus a prompt rule — not a shipped deny (un-overridable) and not a floor rule (too coarse) |
| S8.6 | **D18's five stacks only. No Go.** `C3.6`'s row text predates D18; `registry.py` is the source of truth. `A1.33(b)` is fixed here because `C3.6` consumes the field it breaks |
| S8.7 | Read-only git commands **bypass the gate decision**, bounded to a fixed frozenset of Rudra-composed argv. Consistent with reads never being gated (`rules.py:228`, `floor.py:32`) |

---

## 9. Testing

All of the following run offline, with no model:

| File | Covers |
|---|---|
| `test_shell_runner.py` | allow / deny / ask paths against a fake gate; real subprocess via `python -c`; timeout; missing binary |
| `test_git_core.py` | real `git init` temp repos — git's presence was already verified in `C3.1` |
| `test_git_auto_branch.py` | clean, dirty, detached, not-a-repo, name collision |
| `test_testing_runner.py` | command resolution per stack in temp dirs |
| `test_testing_parse.py` | **captured real** pytest / jest / cargo output as fixtures, never invented strings |
| `test_permissions_unknown_tool.py` | `A1.53` — `ask` with no interrupt entry denies; `read_plan` is allowed silently |
| `test_registry_python_command.py` | `A1.33(b)` — venv, `python -m`, Django, unittest resolution |

Fixtures must be captured from real runs. A regex written against invented output
tests the regex against itself.

Existing suites needing updates: `test_config_schema.py` and
`test_config_permissions_and_tools.py` for the two new `[tools]` keys;
`test_permissions_middleware.py` for the `A1.53` signature change.

The gate remains `.venv/bin/ruff check src/ tests/` → `All checks passed!` and
`.venv/bin/pytest -q` at or above the measured baseline.

**Baseline measured 2026-08-11 on a clean tree at `d93541c`:**
`.venv/bin/pytest -q` → `516 passed, 2 skipped, 35 warnings in 3.33s`. Note this
is one higher than the `515 passed, 2 skipped` Step 7's ledger row records, and
`CLAUDE.md` §8 says `489` — both are stale. 516 is the number to beat; the
implementing session should re-measure rather than trust any of the three.

---

## 10. Acceptance

Four runs, mirroring Step 7's bar — from a `mktemp -d` outside the repo, under
`env -i` with no `RUDRA_*`/`OLLAMA_*` set, configured only by
`.rudra/config.toml`:

1. **`ask` through a real pty.** The agent calls `run_tests`; the approval panel
   renders `execute  pytest -q`; approve; tests run; the audit line records the
   real command string, not `run_tests`.
2. **`--auto` alone.** `run_tests` is denied with `source: "auto-shell"`, and the
   run still completes — confirming §2.5's consequence is real and legible rather
   than a crash.
3. **`--auto --allow-shell`.** Tests execute; counts parse; `tests.log` holds the
   full output and `output_tail` holds the tail.
4. **`auto_branch = true`.** A clean repo gets `rudra/<slug>`; a dirty repo skips
   with a printed reason and the run continues on the current branch.

Evidence goes in the ledger row itself, not a scratch file — the `U.12` lesson,
whose `/tmp` smoke logs no longer resolve.

---

## 11. Ledger deltas

| Row | Change |
|---|---|
| `C3.5` | PENDING → DONE |
| `C3.6` | PENDING → DONE |
| `A1.33` | (b) closed; (a) and (c) stay PENDING — out of scope |
| `A1.53` | PENDING → DONE |
| `C4.6` | No change. Recorded here only as a note for Step 13: `C3.5` now covers git natively, so a git MCP server in the default bundle would duplicate it. That decision belongs to Step 13, not this step |
| D19 | Unchanged. Confirmed 2026-08-11: `VersionControlHelperMCP` stays documentation-only and is not integrated here |
