# Step 9a — Deterministic Verification Gate (C6.6)

**Date:** 2026-08-12
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.6`. Re-points `A1.24`. Leaves `A1.8`, `C11.2` open by design.
**Depends on:** Step 8 (`C3.5`, `C3.6`) — shell, git, and the test runner.
**Blocks:** Step 9c (fix loop), which cannot iterate until a gate defines "done".

---

## 0. Why this is its own spec

TODO.md §E carried Step 9 as one row of eight items (`C6.1–C6.6, C6.5a, C6.10, U.11`)
spanning three separable subsystems. The owner decomposed it on 2026-08-12 into three
specs, each with its own plan and merge:

| Spec | Rows | What it is |
|---|---|---|
| **9a** (this one) | `C6.6` | The deterministic verification gate. Pure Python, no model. |
| **9b** | `C6.2`, `C6.3`, `C6.4`, `U.11` | Subagent architecture: coder / tester / reviewer. |
| **9c** | `C6.1`, `C6.5`, `C6.5a`, `C6.10` | The agentic loop, fix loop with bounds, self-maintained ledger. |

9a is first because TODO.md §E already mandates the order: *"Build C6.6 (deterministic
gate) **before** C6.5 — the loop has nothing to loop on until the gate exists."*

It is also the only one of the three that is fully testable without a model backend,
which makes it the cheapest to get right.

### The defect this closes

Rudra's current success check is that a file exists on disk:

```python
file_on_disk = self.context.project_path / filename
if file_on_disk.exists():          # src/rudra/agent/main_agent.py:438-439
    _check_off_file(self.plan_path, filename)
```

A zero-byte file, a truncated file, a file containing only `def main(): pass` — all
three are recorded as success and ticked off the plan. That is `A1.8`.

---

## 1. Owner decisions

Recorded as `S9a.1`–`S9a.6`, in the order they were made. Each changed the design.

| # | Decision | Consequence |
|---|---|---|
| **S9a.1** | Step 9 splits into three specs, not one and not two | This document covers `C6.6` only |
| **S9a.2** | Lint is **advisory**; syntax, typecheck, test, and stub scan **block** | A style rule can never fail a task or feed the fix loop. A type error can |
| **S9a.3** | **Bundle the Python tooling with Rudra** rather than treating absence as a state | `ruff` and `mypy` become runtime dependencies |
| **S9a.4** | For stacks pip cannot vendor, **document the install and error with a pointer** when a needed tool is missing | New `Documentation/10-verification.md`; every `missing_tool` message carries an anchor into it |
| **S9a.5** | Stub scan covers **changed files only**; every other stage covers the whole project | A pre-existing `TODO` in code Rudra never touched is not Rudra's defect |
| **S9a.6** | Ship a **library plus a `rudra verify` command**; every stage stays **gated as `execute`** | 9a is independently acceptance-testable, and no new permission vocabulary is introduced |

### S9a.4 in detail — what bundling can and cannot reach

`ruff` and `mypy` are pip-installable, so Rudra ships them. `tsc`, `eslint`, and
`clippy` are npm and rustup artifacts; a Python wheel cannot vendor them. The gate
therefore uses, in order:

1. A **native** check where one exists and costs nothing (Python `ast.parse`).
2. The **project's own** tool, found via the existing `_venv_executable`
   (`src/rudra/stacks/detect.py:76`).
3. Rudra's **bundled** tool, invoked as `sys.executable -m <tool>`.
4. Otherwise `missing_tool` — a blocking error naming the tool and the doc anchor.

Bundled `mypy` is not equivalent to the project's `mypy`. `ruff` is a static parser
and does not care whose interpreter runs it; `mypy` resolves imports, so running
Rudra's copy against a project whose dependencies live elsewhere produces a flood of
`Cannot find implementation or library stub for module`. The fallback therefore adds
`--ignore-missing-imports` and says so in the stage detail. It still catches type
errors within the project's own code, which is the majority case for generated code.

---

## 2. Architecture

```
src/rudra/verify/
├── __init__.py      verify_project() — the one public entry point
├── result.py        Finding, StageResult, VerifyReport — frozen data, no behaviour
├── pipeline.py      stage order, short-circuit policy, verdict computation
├── stubs.py         the stub scanner — native, no subprocess
└── report.py        rendering: Rich table for humans, dict for --json

src/rudra/stacks/
├── profile.py       + lint_command, typecheck_command
├── registry.py      + those commands on all five profiles (a data change)
└── detect.py        + resolve_lint_command(), resolve_typecheck_command()

src/rudra/cli.py     + @app.command("verify")
```

### Why the commands live in `stacks/`, not in `verify/`

`src/rudra/stacks/registry.py:3-4` states the rule: *"Adding a language is a data
change here, not a design change."* A second stack table inside `verify/` would put
the five stack names in two modules, and this repo has already been bitten by exactly
that — `A1.30` is an inference table that drifted out of agreement with `registry.py`,
mapping three of its eight rules to Python and omitting Angular entirely.

The new resolvers mirror `resolve_test_command` (`detect.py:152`) precisely, including
its convention that `None` on the profile means "work it out from the project's own
layout" rather than "no command".

### Boundaries

- `verify/` never starts a subprocess. It calls `run_gated` (`src/rudra/shell/runner.py:91`),
  which remains the only subprocess call site in Rudra.
- `verify/` never interprets policy. `run_gated` asks the engine.
- `stacks/` never executes. It answers "what is the command", as it already does for tests.
- `stubs.py` knows no stack profiles. It takes paths and a language hint, and returns findings.
- The test stage **reuses `run_tests()` whole** (`src/rudra/testing/runner.py:111`). It is
  not reimplemented, wrapped, or partially duplicated.

### Entry point

```python
def verify_project(
    project_path: Path,
    *,
    changed_files: Sequence[str] = (),
    gate: Any,
    console: Console,
    cfg: Any,
    _command_override: dict[str, list[str]] | None = None,
) -> VerifyReport
```

`gate`, `console`, and `cfg` are threaded rather than read from module state, mirroring
`run_tests`. `A1.52` is an open defect about factories reading `Path.cwd()` instead of
the path they were handed; this signature does not repeat it.

`_command_override` is test-only, and follows the precedent its docstring in
`testing/runner.py:117-123` sets: it exercises the missing-binary and timeout paths
without requiring the machine to lack a toolchain.

---

## 3. The five stages

### Outcomes — six, not two

Collapsing outcomes is the `A1.57` failure mode: pytest's exit 5 ("collected nothing")
read as "tests failed" would have sent the fix loop to repair working code. The
`TestResult` record already refuses that collapse (`testing/runner.py:39-43`), and this
gate inherits the discipline.

| Outcome | Meaning | Blocks | `escalate` |
|---|---|---|---|
| `passed` | ran, clean | no | no |
| `failed` | ran, found problems | yes, unless advisory | no |
| `not_applicable` | this stack has no such stage — plain JS has no typechecker, ever | no | no |
| `covered_by` | subsumed by a later stage; `cargo check` / `tsc --noEmit` are the parse step | no | no |
| `missing_tool` | the stage applies, the binary is absent | yes | **yes** |
| `denied` | the permission gate refused the command | yes | **yes** |

`not_applicable` and `missing_tool` must stay distinct. S9a.4's rule — missing tool is
an error pointing at the docs — is right for a TypeScript project with no `tsc`, and
wrong for a plain-JS project where no typechecker was ever expected. One state cannot
carry both.

### Command table

| # | Stage | Blocking | Python | Rust | Node (plain JS) | React / Angular / Node+TS |
|---|---|---|---|---|---|---|
| 1 | syntax | yes | `ast.parse` — native, **no subprocess, no gate** | `covered_by: typecheck` | `node --check <file>` per changed file | `covered_by: typecheck` |
| 2 | lint | **advisory** | `ruff check` (project venv → bundled) | `cargo clippy` | `eslint` when declared | `eslint` when declared |
| 3 | typecheck | yes | `mypy` (project venv → bundled `--ignore-missing-imports`) | `cargo check` | `not_applicable` | `tsc --noEmit` |
| 4 | test | yes | `run_tests()`, reused whole — all stacks | ← | ← | ← |
| 5 | stubs | yes | native scanner, changed files only — all stacks | ← | ← | ← |

### Which stack, when several match

`detect()` returns a list, most specific first, because a Tauri app genuinely is both
Rust and Node and monorepos are ordinary (`stacks/detect.py:51-59`). The gate resolves
its commands from `profiles[0]`, the most specific match — the same choice `run_tests`
already makes (`testing/runner.py:126-127`). Running every matched stack's toolchain
would triple the gate's cost on a monorepo and produce a verdict nobody asked for; the
report names which profile was used, so the choice is visible rather than assumed.

`covered_by` is reported, never silently skipped. Rust and TypeScript have no cheap
native parse step, and `cargo check` / `tsc --noEmit` already answer "does it parse";
a separate syntax pass would be a second full compile for no new information.

Python's syntax stage runs no subprocess at all. `ast.parse` on each changed file is
free, instant, ungated, and catches precisely the truncated-or-empty file that
`main_agent.py:439` currently calls success.

### Stub scan

Changed files only (S9a.5). Patterns:

| Language | Detected |
|---|---|
| Python (via `ast`) | function or method whose entire body is `pass`, `...`, or `raise NotImplementedError`; `TODO`/`FIXME` comments |
| Rust | `todo!()`, `unimplemented!()`, `TODO`/`FIXME` |
| JS / TS | `throw new Error("not implemented")` and variants, empty function bodies, `TODO`/`FIXME` |

Python uses `ast`, not regex, because a bare `pass` is legitimate in `except:` blocks,
in `class Foo: pass`, and in `while True: pass`. A line-based matcher cannot tell those
from a stubbed function body. Non-Python stacks use line regex and the report says so —
being shallower is acceptable; pretending otherwise is not.

The stub scan is the entire reason `C6.6` exists as a separate item from "run the
tests". An agent can pass a test suite and still leave placeholders, and no test-only
gate catches that.

### Ordering and short-circuit

Stages run in table order. The pipeline **stops at the first blocking failure**.
Advisory stages never short-circuit.

A failing advisory stage is fully reported — its `findings` and `output_tail` are
populated and rendered exactly like a blocking stage's — and it never sets
`passed=False`. "Advisory" governs the verdict, not the visibility: the user and, in
9c, the model both still see what lint said.

Rationale: a file that does not parse makes lint, typecheck, and test output pure
noise, and feeding all of it to a 32B model consumes the context the fix loop needs to
act on the real failure (D6).

---

## 4. Data flow and types

```python
# src/rudra/verify/result.py

@dataclass(frozen=True)
class Finding:
    file: str
    line: int | None
    message: str

@dataclass(frozen=True)
class StageResult:
    name: str                            # syntax | lint | typecheck | test | stubs
    outcome: str                         # see the outcome table
    blocking: bool                       # False for lint, True otherwise
    command: tuple[str, ...] | None
    stack: str | None
    findings: tuple[Finding, ...] = ()
    output_tail: str = ""                # capped at MAX_TAIL_CHARS, reused from testing.runner
    detail: str = ""                     # why not_applicable / which tool / what covers it
    docs_anchor: str | None = None       # set only on missing_tool

@dataclass(frozen=True)
class VerifyReport:
    passed: bool                         # no blocking stage failed, none missing_tool, none denied
    stages: tuple[StageResult, ...]
    blocker: StageResult | None
    escalate: bool
```

### `escalate` is the field 9c branches on

- `passed=False, escalate=False` → the fix loop receives `blocker.output_tail` and iterates.
- `passed=False, escalate=True` → stop, print the docs pointer, return to the user.

Without the split, the loop would spend its whole attempt budget (C6.5a) asking a model
to install `tsc` or to grant itself `--allow-shell`. Neither is something a model can do.

### Flow

```
verify_project(project, changed_files=[...])
  → detect(project)                        stacks/detect.py:51 — profiles, most specific first
  → for each stage in order:
        resolve command                    stacks/detect.py resolvers
        run_gated(argv, cwd=project, …)    shell/runner.py:91 — audited as execute:<command>
        parse → StageResult
        if blocking and failed: stop
  → write full output to .rudra/run/logs/verify.log
  → VerifyReport
```

The log write mirrors `_write_log` (`testing/runner.py:95-108`), including its
"a log is never worth failing a run over" `OSError` swallow, and lands in D15's
volatile subtree via `rudra_paths(project).logs`.

### Where `changed_files` comes from

- **9c** passes the exact paths the coder wrote — the precise answer.
- **`rudra verify`** has no such context and defaults to the git working tree
  (modified plus untracked, via `src/rudra/git/core.py`'s `status`).
- `--all` scans the whole project; `--changed <path>…` names files directly. The two
  are mutually exclusive: passing both exits 2 with a message, rather than silently
  letting one win.
- Outside a git repo with no flag, it scans everything and says so in the report.
  Scanning nothing would report a clean stub stage over an unexamined tree.

---

## 5. Error handling

| Case | Outcome | Blocks | `escalate` | Reason |
|---|---|---|---|---|
| Gate denied the command | `denied` | yes | **yes** | `--auto` without `--allow-shell`. User action, not a code defect |
| Binary absent, or will not start | `missing_tool` | yes | **yes** | S9a.4 — error plus docs pointer |
| Timed out | `failed` | yes | no | An infinite loop in generated code is a real defect the loop can fix; the detail names `[tools] test_timeout` for the other case |
| Tests ran and failed | `failed` | yes | no | The fix loop's primary input |
| No test command declared | `not_applicable` | no | no | `TestResult.available=False` is a real answer (`runner.py:39`) |
| No tests collected | `not_applicable` | no | no | `TestResult.no_tests_collected` — neither pass nor failure (`A1.57`) |
| Internal error inside a stage | `failed` | yes | **yes** | A bug in Rudra must not be reported as a bug in the user's code. Caught per stage so one broken stage cannot kill a run (`A1.39` class); traceback to `verify.log` |

Under plain `--auto`, the report reads: syntax passed (native), stubs passed (native),
lint/typecheck/test `denied`, `escalate=True`, exit 2. That is honest and it is the
consequence CLAUDE.md §8 already states — an unattended run cannot verify its own
output unless the user opts into shell.

### The verdict never says a bare "passed"

It always names what did not run:

```
passed — 2 of 5 stages did not run
  (typecheck: not applicable to plain JS; test: no tests collected)
```

A gate reporting success while three stages were skipped is `A1.57` at report level.

---

## 6. Dependencies

`pyproject.toml`:

- `ruff>=0.15.8` moves from `[dependency-groups] dev` (`pyproject.toml:62`) into
  `[project] dependencies`.
- `mypy>=1.14` is added to `[project] dependencies` — new.

### Finding the bundled tools

`[sys.executable, "-m", "ruff", "check", …]`, never `shutil.which("ruff")`. PATH is
unreliable under `uv tool install`, and `src/rudra/permissions/env.py:4` already
records that `scrubbed_env` produces an environment in which `which pytest git ruff`
finds nothing.

This is the one place `sys.executable` is correct, and it must be stated explicitly
because `stacks/detect.py:112-116` forbids it. That prohibition is about the **target
project's** interpreter: D18 is explicit that Rudra being a Python project and the
target being one must never be conflated, so the user's tests never run under Rudra's
Python. Here the call deliberately invokes **Rudra's own bundled tool**, which is
exactly what `sys.executable` names. The project's own tool still wins when present,
through `_venv_executable` (`detect.py:76`).

---

## 7. CLI surface

```bash
rudra verify                        # git working-tree changes; Rich table
rudra verify --all                  # stub-scan the whole project
rudra verify --changed src/a.py …   # name files explicitly
rudra verify --json                 # machine-readable
```

**Exit codes:** `0` passed · `1` a blocking stage failed · `2` escalation (denied,
missing tool, internal error). `2` already means "the environment is wrong, not your
code" in this CLI — a non-TTY under `mode = "ask"` exits 2 before any model call
(CLAUDE.md §8) — so escalation maps onto it rather than inventing a code.

Registered as a top-level `@app.command("verify")` beside `doctor` (`cli.py:389`) and
`init` (`cli.py:490`). It takes flags, not subcommands, so it is not a sub-app.

Output:

```
Stage      Outcome            Detail
syntax     passed             3 files parsed
lint       passed (advisory)
typecheck  failed             2 errors
test       —                  not run (stopped at typecheck)
stubs      —                  not run (stopped at typecheck)

✗ failed — typecheck
src/models.py:14: error: Argument 1 to "save" has incompatible type "str"; expected "int"
```

---

## 8. Documentation

Per S9a.4, the docs are part of the deliverable, not a follow-up:

1. **New `Documentation/10-verification.md`** — what the gate checks, the five stages,
   the six outcomes, and a per-stack **install matrix**:

   | Stack | Needs installing |
   |---|---|
   | Python | nothing — `ruff` and `mypy` ship with Rudra |
   | Rust | `rustup component add clippy` (`cargo check` ships with the toolchain) |
   | TypeScript | `typescript` in `devDependencies` |
   | Any JS/TS | `eslint` in `devDependencies`, for the advisory lint stage |

   Every `missing_tool` message ends with an anchor into this file, so the error and
   its fix are one hop apart. The `docs_anchor` field on `StageResult` carries it, and
   `report.py` composes the message once rather than at each resolution site.

2. **`Documentation/04-cli-reference.md`** gains the `verify` entry.
3. **`README.md`** gains one line in the command list.

---

## 9. Configuration

**No new config keys.** The test stage reuses `[tools] test_timeout`; the other stages
use the same value. Per-stage blocking is fixed in code — lint advisory, the rest
blocking — because the owner rejected a per-stage TOML section, and CLAUDE.md §6 is
explicit that an inert config key is worse than no key.

---

## 10. Testing

### Unit layer — no model, no network

| File | Covers |
|---|---|
| `tests/test_verify_stubs.py` | The AST scanner. Must distinguish a stubbed body (`def f(): pass`) from legitimate `pass` in `except:`, `class Foo: pass`, and `while True: pass` — the case a regex gets wrong |
| `tests/test_verify_pipeline.py` | Stage order, short-circuit at first blocking failure, lint never short-circuiting, verdict computation, `escalate` propagation |
| `tests/test_verify_outcomes.py` | Every row of §5's table, driven by a fake gate and `_command_override` |
| `tests/test_verify_report.py` | Verdict string always naming skipped stages; `--json` shape; exit-code mapping |
| `tests/test_stacks_commands.py` | New resolvers: project venv beats bundled; `sys.executable -m ruff` fallback; plain JS → `not_applicable`; TS project → `tsc --noEmit` |
| `tests/test_cli_verify.py` | Flag parsing, `--all` vs `--changed`, exit codes 0/1/2 |

Fixture projects are built under `tmp_path` from marker files only — no `npm install`,
no `cargo build`. Stages whose tools are genuinely absent in CI assert the
`missing_tool` path, which is a real assertion rather than a skipped test.

### Acceptance — three runs, no model required

1. **Dogfood.** `rudra verify --all` against the Rudra repo itself: the Python path end
   to end with real `ruff`, real `mypy`, and the real suite. The strongest single check
   available, and it costs nothing.
2. **Deliberate breakage.** In a `mktemp -d` Python project — first a truncated file
   (syntax blocks, nothing downstream runs), then a stubbed function that passes its
   tests (stubs blocks after test passes). The second half is `C6.6`'s entire
   justification, so it gets a run rather than a unit test.
3. **Denial.** The same project under `--auto` with no `--allow-shell`: syntax and stubs
   pass natively, the three command stages report `denied`, `escalate=True`, exit 2, and
   `.rudra/run/logs/permissions.jsonl` records each denial with `source: "auto-shell"`.

### Gates — unchanged and absolute

```
uv run ruff check src/ tests/         → All checks passed!
uv run ruff format --check src/ tests/
uv run pytest -q                      → must not go below the current count
```

Run under `uv run`, never a drifted `.venv` — CLAUDE.md §9 records two defects that
reached `main` under exactly that noise.

---

## 11. Ledger impact

### Closed

- **`C6.6`** → DONE. The gate exists, is tested, and is callable.

### Open by design, with reasons recorded

- **`A1.8`** stays PENDING. The gate exists, but nothing consumes it until 9c replaces
  `main_agent.py:438-439`. Marking it DONE would claim a fix that is not wired.
- **`A1.24`** is **re-pointed**, not closed. Its row defers to "C6.6's deterministic
  completion gate", but that text predates this decomposition: `A1.24` is about
  `looks_like_path` mis-classifying single-word prose in a *plan checklist*
  (`src/rudra/tools/planning_tools.py:28`), and this gate never parses plans. Its real
  home is `C6.10`'s self-maintained ledger, in 9c. The row text is corrected in the
  same commit.
- **`C11.2`** stays PENDING. The fixture projects cover command *resolution* per stack;
  the row wants a live end-to-end agent run per stack, which needs a model backend.

### TODO.md §E is edited, not appended to

Step 9's single row becomes 9a / 9b / 9c with their row allocations and dependency
order. Leaving the old row in place would send the next session into the wrong step,
which is the exact failure §E exists to prevent.

---

## 12. Out of scope

Stated explicitly so 9b and 9c inherit it cleanly:

- No agent, subagent, or prompt changes. `planner_agent.py` and `coder_agent.py` are untouched.
- No `run_verify` model-facing tool. It costs roughly ten lines on top of this, but it
  would wire a tool into an agent architecture that 9b replaces.
- No fix loop, no retry bounds, no progress ledger. The gate returns a verdict; deciding
  what to do with a failure is 9c's job.
- `src/rudra/agent/main_agent.py` is not modified at all in 9a.
