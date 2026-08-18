# Multi-Language Target Support

**Date:** 2026-08-07
**Status:** design approved, not yet implemented
**Trigger:** Step 4 (`C0.5, C0.6, A3.1–A3.4`) was paused mid-brainstorm when the owner stated that
Rudra's users will build Rust and Node projects — including frontend frameworks — not only Python.
**Relates to:** §0.5 (static Q&A purge), D9 (deterministic fix-loop gate), `C3.6` (test-runner tool),
`C6.6` (completion gate), `C6.8a` (open fact store)

---

## 1. The requirement, stated precisely

Two toolchains exist and must never be confused. Almost every design question below resolves once
they are held apart.

| | Rudra's own toolchain | The target project's toolchain |
|---|---|---|
| What it is | Rudra is a Python package built on `langchain` / `deepagents` | Whatever the user's project is written in |
| Test runner | `pytest`, always | detected per project |
| Linter | `ruff`, always | the project's own |
| CI matrix | Python 3.12 / 3.13 | not Rudra's concern |
| Changes with the target project? | **No** | **Yes** |

Rudra's own CI (Step 4's `C0.6`) is therefore a Python CI and stays one. Nothing in this document
changes that.

### Supported target stacks

| Stack | Marker files | Test command source |
|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `requirements.txt` | `pytest` |
| Rust | `Cargo.toml` | `cargo test` |
| Node (generic) | `package.json` | `package.json` → `scripts.test` |
| React / Next.js | `package.json` + `next.config.*` / a React dep | `scripts.test` |
| Angular | `package.json` + `angular.json` | `scripts.test` (conventionally `ng test`) |

### Two owner constraints that bound the design

1. **"Rudra can not force user to do specific things."** Greenfield and brownfield are both
   first-class, and the choice is the user's. Rudra adapts to whatever directory it is pointed at;
   it never requires a scaffold step, a config file, or a particular layout before it will work.
2. **The frontend completion gate is unit tests only.** No build step, no e2e, no browser
   automation, no dev server.

---

## 2. The design tension, and why it is not a real one

§0.5 bans static inference tables. It is deleting the 8-row table at `main_agent.py:505-512`
(`'Flask ...' → Python + Flask`, `'React ...' → JavaScript / TypeScript + React`, …) precisely
because inferring a stack from prose is the twin of static Q&A.

D9 requires the fix loop's gate to be deterministic: *"Test exit code is ground truth."* A test
command cannot be run deterministically without knowing what it is.

Those conflict only if two different operations are conflated:

| Operation | Example | Verdict |
|---|---|---|
| Guessing intent from prose | `"build a Flask app"` → Python | **Inference.** Banned by §0.5, correctly |
| Reading a file off disk | `Cargo.toml` exists → `cargo test` | **Observation.** Required by D9 |

Detection reads facts. It never guesses. Greenfield has no facts to read yet, so **the model**
chooses the stack from the prompt — as §0.5 intends, "inferring the stack is the model's job" — and
writes the marker file. From the next turn onward the marker exists and detection is deterministic
forever after.

No prose→stack lookup table is introduced anywhere by this design. The one at `main_agent.py:505-512`
is still deleted by `C6.8a`.

---

## 3. The component

```
src/rudra/stacks/
├── __init__.py    public API
├── profile.py     StackProfile
├── registry.py    the built-in profiles
└── detect.py      detect(project_path) -> list[StackProfile]
```

### `StackProfile`

A declarative record. No behaviour, no I/O.

| Field | Purpose |
|---|---|
| `name` | `"rust"`, `"python"`, `"node"`, `"react"`, `"angular"` |
| `markers` | filenames whose presence identifies the stack |
| `test_command` | argv list, or `None` when it must be read from the project |
| `skip_dirs` | build-output directories `project_tree` must not list |
| `specificity` | integer; higher wins when several profiles match |

### `detect()` returns a list, not one profile

A Tauri application genuinely is both Rust and Node. Monorepos are ordinary. Returning a single
"the" stack would force a wrong answer in both cases, and forcing is what constraint 1 forbids.

The list is ordered most-specific-first, so a caller that legitimately wants one answer can take the
head without the module having to pretend the others do not exist:

```
package.json                        -> [node]
package.json + angular.json         -> [angular, node]
package.json + next.config.mjs      -> [react, node]
Cargo.toml + package.json           -> [rust, node]      # Tauri
```

### Node's test command is read, not guessed

For Node stacks `test_command` is `None` in the registry and resolved by reading `package.json`'s
`scripts.test`. That is the project's own declared answer. Inspecting `devDependencies` to guess
between jest and vitest would be inference where an observation is available — the same error §0.5
is removing elsewhere.

If `scripts.test` is absent, that is reported as "this project declares no test command" rather than
guessed at. A missing gate is a fact the fix loop can act on; a fabricated one is not.

### The module reads and returns. It never executes.

`detect()` touches the filesystem and returns data. Nothing in `src/rudra/stacks/` runs a
subprocess. Command execution belongs to `C3.6` in Step 8, where the shell backend exists.

This split is what allows detection to land now: it is pure, fully unit-testable, and has no
dependency on shell access, on a model, or on network.

### Configuration overrides detection; it does not replace it

When Step 6's `.rudra/config.toml` lands, an explicit `[stack]` declaration wins over detection.
Until a user writes one, detection is silent and automatic. Requiring configuration before Rudra
would work at all is exactly the forcing that constraint 1 rules out.

---

## 4. What this changes in existing code

### 4.1 `filesystem/tree.py:34` — build-output directories

```python
_ALWAYS_SKIP_DIRS = frozenset({".git", ".rudra", "__pycache__", "node_modules", ".venv", "venv"})
```

Missing for the target stacks: `target/` (Cargo), `.next/`, `out/` (Next.js), `dist/`, `build/`
(most JS toolchains), `.angular/` (Angular cache), `.pytest_cache`, `.ruff_cache`, `.mypy_cache`.

**Where this actually bites — corrected during spec review.** An earlier draft of this design claimed
the skip set only matters on the non-git fallback path. That is wrong. `_is_always_skipped` is
applied at `tree.py:81` to the entry list from *either* source, so it filters the git listing too.
The accurate statement is about redundancy, not reach:

| Path | Effect of the gap |
|---|---|
| `git ls-files` succeeded | Largely masked — `--exclude-standard` already drops `target/`, `.next/`, `dist/` because they are conventionally gitignored. The skip set is belt-and-braces here |
| Fallback walk (`tree.py:75`) | **Load-bearing.** Runs when the directory is not a git repo, and — per the `A1.19` note at `tree.py:58-63` — also when git lists nothing because the root is itself gitignored |

The harm is crowding, not just noise: `project_tree` caps at `max_entries=300` (`tree.py:44`). A
Rust project with a populated `target/` fills that budget with build artefacts and truncates away
the source files the planner actually needs to see.

### 4.2 Prompt examples are uniformly Python

Every filename example shown to the model is a `.py` file:

| File | Lines |
|---|---|
| `src/rudra/tools/planning_tools.py` | `:12-14`, `:60-62`, `:68-69`, `:72-73`, `:113`, `:160`, `:178` |
| `src/rudra/agent/planner_agent.py` | `:46`, `:53`, `:61` |
| `src/rudra/agent/coder_agent.py` | `:28` |

Individually trivial; collectively they are a steady nudge toward Python for every task, including
`"build a Rust CLI"`. Rotate to a mixed set (`src/main.rs`, `package.json`, `src/app/app.component.ts`,
`main.py`) so no single language is the implied default.

Note this is a *prompt* fix, not a logic fix — `looks_like_path` (`planning_tools.py:25-39`) is
already language-agnostic. Verified by execution, not inspection:

```
$ .venv/bin/python -c "from rudra.tools.planning_tools import looks_like_path; ..."
True   Cargo.toml          True   next.config.js
True   package.json        True   src/app/app.component.ts
True   angular.json        True   main.py
True   tsconfig.json       True   vite.config.ts
True   src/main.rs         True   src/lib.rs
```

So no validation logic needs to change for the new stacks — only the examples that steer the model.

### 4.3 Downstream consumers

| Ledger item | Step | Change |
|---|---|---|
| `C3.6` test-runner tool | 8 | Consumes `StackProfile.test_command` instead of implementing its own detection. Its current text already anticipates this: *"detect pytest / jest / go test / cargo"* |
| `C6.6` deterministic completion gate | 9 | Same source, so gate and runner cannot disagree about what "the tests" means |
| `C6.8a` open fact store | 10 | Detected stacks are recorded as ordinary facts (`{"stack": "rust", "test_command": "cargo test"}`), which the open key/value store already accommodates |

---

## 5. The Angular constraint

Angular CLI's default test builder runs Karma against a real browser. Running Angular unit tests
therefore requires a Chrome/Chromium binary on the machine, typically via `ChromeHeadless`.

This is still a unit test, not e2e — it stays within constraint 2. But it is the one target stack
whose unit tests carry an external binary dependency; Rust, Node and Python have none.

**Required behaviour:** when the browser is unavailable, Rudra must fail fast with a message naming
the missing dependency. The failure mode to avoid is a silent hang waiting on a browser that will
never launch — which, inside Step 9's fix loop, would stall the loop rather than fail a round.

Newer Angular versions offer non-browser test builders. Rudra should not hard-code an assumption
either way: the command still comes from `scripts.test`, and this section is about handling the
failure legibly, not about selecting a runner.

---

## 6. Roadmap placement

**No new Stage is required.** Nearly all the machinery is already scheduled; it is simply
Python-flavoured today. The genuinely new piece is `src/rudra/stacks/`.

Detection is pure, dependency-free, and language-agnostic — which makes it precisely the kind of
stable seam Step 4's paused regression net was looking for, and unlike tests written against
`main_agent.py`'s orchestration loop, it will not be deleted by Step 9's rewrite.

**Therefore: Step 4 resumes with `src/rudra/stacks/` as its centrepiece.** Steps 8, 9 and 10 then
consume a tested component instead of each inventing detection independently.

### Step 4, as amended

| Item | Content |
|---|---|
| `C0.5` pytest suite | `src/rudra/stacks/` (new, high coverage) · `compat/deepagents_path.py` (currently **0%** — never imported by any test) · `main_agent.py`'s *stable helpers* only, never its loop · `cli.py` import + `--help` smoke · `interaction_tools.py` |
| Fixtures | Every seam test runs against Rust, Node, React, Angular **and** Python fixtures, so no Python assumption calcifies |
| `C0.6` CI | Python-only, unchanged: `ruff check` (absolute 0), `ruff format --check`, `pytest` on 3.12 + 3.13, **package installed before pytest** (`test_package_version.py` reads installed distribution metadata; an uninstalled tree yields `0.0.0+unknown` and fails) |
| Coverage | Reported, **not** gated. At 47% a gate at today's number is meaningless and one set higher pressures low-value tests against code Steps 5/6/9 replace. Revisit after Step 9 |

### New ledger rows required

| ID | Content |
|---|---|
| `D18` | **Locked decision: multi-language targets are first-class.** Python, Rust, Node, React/Next.js, Angular. Rudra's own toolchain stays Python regardless. Greenfield and brownfield both supported, user's choice |
| `A2.17` | `_ALWAYS_SKIP_DIRS` (`tree.py:34`) omits every build-output directory of the non-Python targets |
| `A2.18` | Prompt filename examples are uniformly `.py` across the 11 locations enumerated in §4.2, spanning 3 files. Counted as *locations*, not lines — several are multi-line blocks. A bare `grep -c "\.py"` over those files returns 19 and overcounts: it also matches code comments that reference Python filenames for unrelated reasons (e.g. `coder_agent.py:53-55`, a docstring recording why `EnforceTargetFileMiddleware` was deleted), which are not model-facing prompt text |
| `C11.1` | Build `src/rudra/stacks/` — profile, registry, detection. Reads only; no execution |
| `C11.2` | Acceptance matrix: one end-to-end case per stack, replacing §0.5's single Rust example |
| `C11.3` | Angular headless-browser dependency: detect absence, fail fast and legibly |

`A1.25` and `A5.2` are the highest existing `A1`/`A5` rows; `A2.16` the highest `A2`; `D17` the
highest decision. IDs above follow those. `C11.x` is a new section — the existing `C` sections are
phase-scoped and none of them owns stack detection.

### Acceptance matrix (`C11.2`)

Replaces §0.5's lone `rudra "build a CLI in Rust with clap"` example. Each case is run in an empty
directory (greenfield) and again inside an existing project of that stack (brownfield):

| Stack | Greenfield prompt | Brownfield check |
|---|---|---|
| Rust | `build a CLI in Rust with clap` | detects `Cargo.toml`, gate is `cargo test` |
| Python | `build a FastAPI service` | detects `pyproject.toml`, gate is `pytest` |
| Node | `build an Express API in TypeScript` | reads `scripts.test` |
| React | `build a React todo app with Vite` | reads `scripts.test` |
| Angular | `add a UserProfile component with tests` | detects `angular.json`, browser check per §5 |

---

## 7. Explicitly out of scope

| Not doing | Why |
|---|---|
| Go, Java, Ruby, C# | Not requested. The registry is extensible; adding a profile is a data change, not a design change |
| e2e / Playwright / dev-server verification | Owner constraint 2: unit tests only |
| A build step in the completion gate | Same. For Angular and React the unit-test run compiles the code under test anyway, so a compile error still fails the gate |
| Per-language coder models | Step 5's `C1.x` model factory could route Rust work to a different model. Out of scope here; noted so it is not lost |
| Changing Rudra's own CI to test Rust/Node | Rudra is a Python package. §1 |
| Deleting the `main_agent.py:505-512` inference table | Already owned by `C6.8a`. This design must not duplicate it |

---

## 8. Open question deliberately left open

Monorepos where stacks nest — an Angular frontend under `web/` beside a Rust backend under
`api/` — are detected correctly by `detect()` (it returns both), but *which* test command the
completion gate should run for a change touching only `web/` is undecided. Deferred to `C6.6` in
Step 9, where the gate is actually built and the question has a concrete caller. Recorded here so it
is not mistaken for an oversight.
