# Step 3 — Correctness Bugs, Ruff, and Dependency Hygiene

**Date:** 2026-08-07
**Section E step:** 3 (Stage I — Make the base sound)
**Depends on:** Step 2 (**DONE** 2026-08-06, commit `6956685`)
**Unblocks:** Step 4 (`C0.5, C0.6, A3.1–A3.4` — pytest suite + CI green), the Stage I hard gate

---

## 1. Scope

Section E step 3 lists: `A1.1, A1.5, A1.7, A1.12–A1.15, C0.4, C0.7, C0.8`.
`A1.11` was dropped from that set on 2026-08-06 (closed by D17's deletion of `watch`).

Two further rows are not named in the step but are pulled in by their own text, which self-directs
into work this step performs:

| Row | Directs itself here | In scope for |
|---|---|---|
| `A2.11` | "Drop it with the rest of the dependency hygiene work (C0.7)" | §4 |
| `A5.1` | "Fix belongs with A1.5/A1.15" | §6 |

`A5.1` is the larger of the two and is the reason §6 is not purely mechanical — see §6.3.

Four additional defects were found while exploring and are **logged as PENDING before being fixed**,
per CLAUDE.md session rule 2. They are listed in §10 as `N1`–`N4`.

### Out of scope

| Deferred item | Why |
|---|---|
| Layered TOML config (`C2.1–C2.5`) | Step 6. §6 here only defers *when* config loads, not *where from* |
| Provider-agnostic model factory (`C1.x`) | Step 5. `ChatOllama` stays hardcoded in this step |
| `planner_agent.py:63`'s static "do NOT call ask_user" rule | Surface #6 of §0.5's static-Q&A purge, owned by `C6.8a` in Step 10. Step 2 deliberately left it standing; this step does not touch it either |
| `A1.7`'s deeper cause — file-existence as the success test (`A1.8`) | Step 9 (`C6.6` deterministic gate) |

---

## 2. Commit sequencing

Six commits. Each is independently verifiable and updates `TODO.md` in the same commit
(CLAUDE.md rule 6).

| # | Commit | Items closed |
|---|---|---|
| 0 | *(no commit)* rebuild `.venv` | precondition, §3 |
| 1 | `docs(todo): log Step-3 findings and repair drifted citations` | new PENDING rows, citation repairs |
| 2 | `fix(pyproject): dependency hygiene` | A1.12, A1.13, A1.14, A2.11, C0.7, C0.8, N1 |
| 3 | `fix: single-source the version from package metadata` | A1.1 |
| 4 | `refactor(config): defer config load to first use, scope dotenv to cwd` | A1.15, A1.5, A5.1, N2, N3 |
| 5 | `fix(plan): reject task descriptions as plan items` | A1.7, N4 |
| 6 | `style: ruff lint and format clean` | C0.4 |

Commit 6 is last so the formatter never obscures a behavioral diff.

---

## 3. Precondition — `.venv/` does not exist

Every verification command in `CLAUDE.md` §8 targets `.venv/bin/`. The directory is absent from the
working tree; `uv.lock` is present and tracked.

```
$ ls .venv/bin/
ls: .venv/bin/: No such file or directory
```

Rebuild with `uv sync` before any other work. Note that commit 2 moves `ruff` and `pytest` into
`[dependency-groups] dev`, which `uv sync` installs by default — so `.venv/bin/ruff` and
`.venv/bin/pytest` remain present after that commit. Re-run `uv sync` after commit 2.

**Baseline re-confirmed out-of-tree** (`uvx ruff@0.15.8 check src/ tests/ --statistics`): **30
errors** — identical to the count Step 2 recorded for `src/` alone, so nothing drifted while the
venv was missing, and `tests/` is already lint-clean. §8 can therefore widen the check to
`src/ tests/` at no cost.

| Rule | Count | Fixability |
|---|---|---|
| `W293` blank-line-with-whitespace | 12 | `--fix` |
| `I001` unsorted-imports | 10 | `--fix` |
| `F541` f-string-missing-placeholders | 3 | `--unsafe-fixes` |
| `E741` ambiguous-variable-name | 2 | manual |
| `W291` trailing-whitespace | 2 | `--fix` |
| `F401` unused-import | 1 | `--fix` |

---

## 4. `pyproject.toml` — dependency hygiene

| Change | Item | Evidence |
|---|---|---|
| Delete the duplicate `langgraph-checkpoint-sqlite>=3.0.3` | A1.13 | `pyproject.toml:33` and `:68`. Ledger cites `:30`/`:58` — stale, repaired in commit 1 |
| Add `aiosqlite>=0.22.1` to `dependencies` | A1.12 | Runtime `import aiosqlite` at `src/rudra/agent/main_agent.py:532`; currently only transitive (`uv.lock:117`) |
| `Homepage`/`Repository` → `https://github.com/archish9/RudraAnvil` | A1.14 | `pyproject.toml:75-76`. Ledger cites `:65-66` — stale, repaired in commit 1 |
| Drop `watchdog>=4.0.0` | A2.11 | `pyproject.toml:65`; `grep -rn "watchdog" src/` → no hits since D17 deleted `watch` |
| Move `ruff>=0.15.8` and `pytest>=9.0.2` to `[dependency-groups] dev` | C0.7 | `pyproject.toml:56`, `:58` — currently shipped to every end user |
| Drop six unused runtime deps | N1 (new) | see table below |
| Verify and mark the `deepagents==0.7.4` exact pin | C0.8 | `pyproject.toml:27` — **already correct**, with the U.4/U.13/C0.8 comment above it. Landed during Step 1 and never marked. This item needs marking, not editing |

### N1 — unused runtime dependencies

Zero import sites anywhere in `src/`:

| Package | Line | Declared as |
|---|---|---|
| `langchain-community>=0.4.1` | `:39` | "LLM Integration" |
| `fastapi>=0.135.2` | `:51` | "Web Server (optional API layer)" |
| `uvicorn[standard]>=0.40.0` | `:52` | "Web Server (optional API layer)" |
| `tavily-python>=0.7.20` | `:60` | "Web Search Tools" |
| `duckduckgo-search>=7.0.0` | `:61` | "Web Search Tools" |
| `requests>=2.32.0` | `:63` | "HTTP Requests" |

All six are dropped. The FastAPI/uvicorn pair is commented as an optional future API layer, but no
such layer exists, no ledger item plans one, and a dependency carried for a hypothetical is a
dependency the user installs today for nothing. If the API layer is ever built, the declaration
returns with the code.

`langchain-community` is the one drop justified by grep alone rather than by absence of a feature —
it could in principle be pulled in transitively at runtime by a `langchain` code path. The live
smoke run in §9 is what actually proves it is not.

---

## 5. `A1.1` — version drift

`src/rudra/__init__.py:3` declares `__version__ = "0.1.0"`; `pyproject.toml:7` declares `0.2.0`.
`rudra --version` (`src/rudra/cli.py:150-153`) and the banner (`cli.py:83`) both print the wrong
number.

Copying the literal across would recreate the same drift on the next bump. Read the installed
metadata instead, making `pyproject.toml` the single source:

```python
"""Rudra - Autonomous Coding Agent CLI"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rudra")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
```

The fallback matters because `pytest` can import `rudra` from `src/` without an install; it must not
raise at import time.

---

## 6. `A1.15` — defer config load; `A1.5`/`A5.1` — dotenv correctness

### 6.1 The defect

`src/rudra/config.py:56` executes `config = Config.load()` at module import. Because every field is
a `field(default_factory=lambda: os.getenv(...))`, the entire environment is frozen at first import
of `rudra.config`. Any `os.environ` change afterwards — including anything a test does — is ignored.

### 6.2 The fix

Replace the module-level instance with a cached accessor:

```python
_config: Config | None = None


def get_config() -> Config:
    """Return the process-wide Config, loading it on first use."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reset_config() -> None:
    """Drop the cached Config so the next get_config() re-reads the environment.

    Test-support only.
    """
    global _config
    _config = None
```

The module-level `config` symbol is **removed outright**, not kept as an alias. An alias would still
be evaluated at import time by any site that binds it, which is the whole defect.

### 6.3 `A5.1` — `load_dotenv()` resolves relative to the module, not the project

`src/rudra/config.py:9` calls bare `load_dotenv()`. `python-dotenv` resolves the path by walking
upward from the file of the *calling frame* — that is, from `src/rudra/config.py`'s own directory.
The result is that a `.env` anywhere above the installed package is loaded **for any process,
regardless of the invocation directory**.

A5.1 documents the consequence concretely: Step 1's smoke run was launched from a `mktemp -d`
wholly outside the repo and still picked up the repo's own `.env`, whose `OLLAMA_NUM_PREDICT=-1`
was forwarded verbatim into `ChatOllama` and rejected by Ollama's cloud endpoint with
`max_tokens must be positive, got: -1`. The workaround was an explicit command-line export.

This lands here because A5.1's own text assigns it: *"Fix belongs with A1.5/A1.15."* It is also the
same call that §6.2 is already moving, so splitting it across two steps would touch `config.py:9`
twice.

**The fix — scope the lookup to the invocation directory, and defer it with the rest of the load:**

```python
def load(cls) -> "Config":
    """Load configuration from the environment, after the project's .env."""
    load_dotenv(Path.cwd() / ".env")     # explicit path: no upward walk from this module
    return cls()
```

Two changes in one: the path becomes explicit (killing the module-relative walk), and the call moves
out of module scope into `Config.load()`, so it is deferred exactly like everything else in §6.2.
`load_dotenv`'s default `override=False` is retained, so a real environment variable still beats
`.env` — which is what made A5.1's workaround function.

**What is deliberately *not* done:** A5.1 offers a second option, *"validate `num_predict > 0` in
`OllamaConfig` and reject/warn on nonsense values."* Rejected. A5.1's own leg-(1) evidence records
that `-1` is **valid** for plain local Ollama, meaning "unbounded"; only the cloud passthrough
rejects it. A validator here would break the local-first default case to protect a hosted one.
Value legality is provider-specific and belongs to the Step 5 model factory (`C1.x`), where a
provider adapter knows which endpoint it is talking to. Recorded on the A5.1 row as it closes.

**Testability note:** `.env` no longer exists in this working tree (`ls .env` → no such file), so
A5.1's original repro artifact is gone. The defect at `config.py:9` is unaffected — it is a
call-site property, not a data property — and §9's test writes a temporary `.env` above a temp cwd
to reproduce it hermetically rather than depending on a gitignored file.

### 6.4 Call sites

Four imports convert from `from rudra.config import config` to `from rudra.config import get_config`,
with each use becoming `get_config().…`:

| File | Import | Uses |
|---|---|---|
| `src/rudra/cli.py` | `:17` | `:179`, `:198-199`, `:276-277` |
| `src/rudra/agent/planner_agent.py` | `:11` | `:78-81` |
| `src/rudra/agent/main_agent.py` | `:11` | `:527-528` |
| `src/rudra/agent/coder_agent.py` | `:8` | `:58-61` |

### 6.5 The non-obvious site: `cli.py:179`

```python
verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", ...)
```

This is a decorator argument. It is evaluated when the module is imported, so a lazy accessor alone
does not fix it — `get_config()` would simply be called at import time instead. The parameter becomes
three-state:

```python
verbose: bool | None = typer.Option(None, "--verbose/--no-verbose", "-V",
                                    help="Show detailed output"),
```

and resolves inside the callback body, before `verbose` is first read:

```python
if verbose is None:
    verbose = get_config().agent.verbose
```

Semantics preserved exactly: flag absent → consult config; `--verbose` → `True`; `--no-verbose` →
`False`. Only the timing of the config read changes.

### 6.6 `.env.example` — A1.5 plus two unlisted drifts

The file's entire purpose is to be copied to `.env` and work. Given §6.3, a copied `.env` now
applies only when `rudra` runs from that project — which makes getting its contents right matter
more, not less. All three divergences from `src/rudra/config.py:16-22` are repaired together;
fixing one of three leaves it broken.

| Key | `.env.example` | `config.py` default | Item |
|---|---|---|---|
| `OLLAMA_NUM_PREDICT` | `2048` (`.env.example:13`) | `131072` (`config.py:22`) | A1.5 — a copied example truncates generated files at 2048 tokens |
| `OLLAMA_TEMPERATURE` | `0.7` (`.env.example:11`) | `0.3` (`config.py:20`) | N2 (new) |
| `OLLAMA_MODEL`, `_PLANNER` | `qwen3:14b` (`.env.example:6`, `:9`) | `qwen3:14b` (`config.py:17-18`) | N3 (new) — below D6's 32B minimum |
| `OLLAMA_MODEL_CODER` | `qwen3-coder:30b` (`.env.example:10`) | falls back to `qwen3:14b` (`config.py:19`) | N3 — 30B is also below the 32B floor |

**Citation repair:** the `A1.5` ledger row cites `.env.example:14`; the actual line is `:13`.
Repaired in commit 1 alongside the A1.13/A1.14 repairs in §10.

N3 changes the example only. Changing `config.py`'s own defaults is a behavior change for existing
users and belongs to Step 5/6, where the model factory and TOML config replace them; the ledger row
records that follow-up.

---

## 7. `A1.7` — plan items that are not filenames

### The defect

`_parse_pending_files` (`src/rudra/agent/main_agent.py:45-50`) treats every line containing `- [ ]`
as a filename:

```python
return [
    line.strip().replace("- [ ]", "").strip()
    for line in plan_content.splitlines()
    if "- [ ]" in line
]
```

A planner emitting `- [ ] Set up auth` produces the string `Set up auth`, which the orchestrator
then hands to a fresh coder as its target file. Since Step 2 deleted `EnforceTargetFileMiddleware`
(§0.1) and the success test is still "does the file exist on disk" (`A1.8`), the coder may create a
file literally named `Set up auth`, and it is left behind.

Prompt-level prevention already exists and is insufficient on its own: `planner_agent.py:45-46`
gives the CORRECT/WRONG example, and `update_plan`'s docstring (`planning_tools.py:42-48`) lists six.
Neither is enforced.

### The predicate

One definition, in `src/rudra/tools/planning_tools.py`, imported by `main_agent.py`:

```python
def looks_like_path(item: str) -> bool:
    """True if a plan checklist item is plausibly a file path, not a prose task."""
    item = item.strip()
    if not item:
        return False
    if any(c.isspace() for c in item):
        return False
    return item[-1] not in ".,:;!"
```

| Input | Result |
|---|---|
| `main.py`, `src/models.py`, `Makefile`, `Dockerfile`, `.gitignore` | accepted |
| `Create main.py`, `Set up auth`, `Install dependencies` | rejected (whitespace) |
| `Add tests.` | rejected (trailing punctuation) |
| `authentication` | **accepted — known gap, see below** |

The whitespace rule is the load-bearing one: every prose task description observed in the ledger and
in both docstrings contains a space. Requiring an extension or a slash instead would close the
single-word gap but would reject `Makefile`, `Dockerfile`, `LICENSE`, and `Procfile` unless a static
allowlist were carried — and §0.5 rules against exactly that class of static lookup table.

### Two enforcement points, different jobs

**`update_plan` (`planning_tools.py:39`) — blocking.** Before the sanitise pass at `:76-84`, collect
offending items and return a `REJECTED:` message naming them, mirroring the shrink-rejection at
`:69-74`. Nothing is written to disk on rejection, so the model gets a correction signal and
re-emits rather than the plan being silently mangled.

```
REJECTED: these checklist items are task descriptions, not filenames:
  - Set up auth
  - Install dependencies
Re-emit the plan with one exact filename per item, e.g. '- [ ] auth.py'.
```

**`_parse_pending_files` (`main_agent.py:45`) — defensive.** Filters non-paths silently. `PLAN.md` is
plain text on disk, editable by the user and reachable by the agents' own `edit_file`, so the parser
cannot assume `update_plan` was the only writer. A silent skip is correct here precisely because the
blocking check upstream is where feedback belongs.

### N4 — accepted risk, recorded in the ledger

A single-word task description (`authentication`, `setup`, `refactor`) passes both checks and still
becomes a filename. Accepted rather than fixed: closing it requires either a static allowlist (§0.5
forbids) or an LLM judgment call (D9's split says gates stay deterministic). The real fix is the
Step 9 completion gate (`C6.6`), which tests behavior rather than file existence; the ledger row
points there.

---

## 8. `C0.4` — ruff clean

Order matters — autofixes first, so the manual pass reviews the smallest possible remainder.

1. `.venv/bin/ruff check src/ tests/ --fix` → clears `W293`, `I001`, `W291`, `F401` (25 errors).
2. `.venv/bin/ruff check src/ tests/ --unsafe-fixes --diff` → review the 3 `F541` hunks by hand
   before applying. They are f-strings with no placeholders; confirm each is genuinely a stray `f`
   prefix and not a half-written interpolation.
3. `E741` by hand: rename `l` → `line` at `src/rudra/tools/planning_tools.py:67` and `:68`.
   Both are comprehension variables inside `update_plan`'s shrink-rejection counter; the rename is
   local and total.
4. Add to `pyproject.toml`:
   ```toml
   [tool.ruff.format]
   docstring-code-format = true
   ```
   `line-length = 100` and `target-version = "py312"` are already set under `[tool.ruff]` and are
   inherited by the formatter.
5. `.venv/bin/ruff format src/ tests/` — **14 files, 686 diff lines**. Measured
   (`ruff format --check src/ tests/` → `14 files would be reformatted, 14 files already
   formatted`): `cli.py`, `config.py`, `agent/main_agent.py`, `agent/planner_agent.py`,
   `compat/deepagents_path.py`, `compat/path_constants.py`, `filesystem/tree.py`,
   `middleware/fix_write_params.py`, `state/project_config.py`, `tools/planning_tools.py`,
   `tests/test_agent_wiring.py`, `tests/test_fix_write_params.py`, `tests/test_project_tree.py`,
   `tests/test_version_guard.py`.

**Target: 0 errors.** This upgrades Step 4's CI contract from CLAUDE.md §8's relative standard
("no increase vs the 164 baseline at merge-base `b322978`") to an absolute gate, which is what a
`ruff check` CI job should assert. Record the new absolute standard in `TODO.md` under `C0.4` so
Step 4 inherits it, and note that CLAUDE.md §8's wording needs the same update when Step 4 lands.

---

## 9. Verification

Nothing is marked `DONE` without pasted command output (CLAUDE.md rule 4).

### Commands

```bash
uv sync
.venv/bin/ruff check src/ tests/           # expect: All checks passed!
.venv/bin/ruff format --check src/ tests/  # expect: N files already formatted
.venv/bin/pytest -q                        # expect: >= 36 passed
.venv/bin/rudra --version                  # expect: Rudra v0.2.0
.venv/bin/rudra --help                     # expect: no traceback, --verbose/--no-verbose present
```

### New tests, written before their fixes

Per `test-driven-development`: each test below is written and observed failing before the
corresponding change in §4–§8 is made.

| Test | Covers | Assertion |
|---|---|---|
| `test_package_version.py` | A1.1 | `rudra.__version__` equals the `[project] version` parsed from `pyproject.toml` — not a hardcoded `"0.2.0"`, so the test survives the next bump. Named to avoid collision with the existing `test_version_guard.py`, which guards the *deepagents* version and is unrelated |
| `test_config.py::test_get_config_is_cached` | A1.15 | `get_config() is get_config()` |
| `test_config.py::test_reset_config_rereads_env` | A1.15 | set `OLLAMA_MODEL` via `monkeypatch` *after* import → `reset_config()` → `get_config().ollama.model` reflects it. **Fails on today's code**, which is the point |
| `test_config.py::test_no_module_level_config` | A1.15 | `not hasattr(rudra.config, "config")` — guards against the alias creeping back |
| `test_config.py::test_dotenv_is_scoped_to_cwd` | A5.1 | write a `.env` setting `OLLAMA_NUM_PREDICT=-1` into a `tmp_path` parent, `chdir` to an unrelated `tmp_path` child of a *different* tree, `reset_config()`, assert `get_config().ollama.num_predict == 131072`. Hermetic — depends on no gitignored file |
| `test_config.py::test_cwd_dotenv_is_honored` | A5.1 | the converse: `.env` in the cwd *is* read. Guards against §6.3 over-correcting into ignoring `.env` entirely |
| `test_plan_items.py::test_looks_like_path` | A1.7 | full table from §7, including the `authentication` gap asserted as *accepted* so the behavior is pinned rather than accidental |
| `test_plan_items.py::test_update_plan_rejects_prose` | A1.7 | `update_plan` with a prose item returns `REJECTED:` **and** leaves `PLAN.md` unwritten |
| `test_plan_items.py::test_parse_pending_files_skips_prose` | A1.7 | defensive filter drops prose from hand-edited plan text |

Existing suite is 36 tests (Step 2, commit `a484551`); the floor after this step is 36 plus the new
ones, with zero regressions.

### Live smoke run

One end-to-end run against `gemma4:31b-cloud` from a `mktemp -d` outside the repo, same protocol as
Step 2's, asserting exit 0 and a non-empty generated file.

Steps 1 and 2 both had to pass `OLLAMA_NUM_PREDICT=131072` explicitly to work around A5.1. **This
run must not.** With §6.3 landed, a tempdir run reads no `.env` at all, so the `config.py:22` default
applies on its own — and the run succeeding without the override is the acceptance evidence for
A5.1. Run it bare; if it fails on `num_predict`, §6.3 is not actually fixed.

This is not redundant with the unit tests. It is the **only** check that catches a wrongly-dropped
transitive dependency from §4 — in particular `langchain-community`, whose removal rests on grep
evidence rather than on the absence of a feature. A unit suite that never constructs a real agent
would import nothing that needs it.

---

## 10. Ledger changes

### New PENDING rows, added in commit 1 before any fix

| ID | Row |
|---|---|
| N1 | Six runtime dependencies with zero import sites in `src/`: `langchain-community` (`pyproject.toml:39`), `fastapi` (`:51`), `uvicorn` (`:52`), `tavily-python` (`:60`), `duckduckgo-search` (`:61`), `requests` (`:63`). Every user installs all six for nothing. Closed by C0.7 |
| N2 | `.env.example:11` sets `OLLAMA_TEMPERATURE=0.7`; `config.py:20` defaults to `0.3`. Copying the example silently changes model behavior |
| N3 | `.env.example:6,9` ship `qwen3:14b` and `:10` ships `qwen3-coder:30b` — all below D6's 32B minimum. Example only; `config.py:17-19`'s own defaults are a Step 5/6 change |
| N4 | `looks_like_path` accepts single-word prose (`authentication`) as a filename. Accepted risk under A1.7; the real fix is C6.6's deterministic completion gate in Step 9 |

Numbering: these are placeholders. They take real `A1.x` / `A2.x` identifiers in the appropriate
Section A tables when commit 1 is written, following the existing convention.

### Citation repairs, same commit

| Item | Says | Actual |
|---|---|---|
| A1.13 | `pyproject.toml:30` and `:58` | `:33` and `:68` |
| A1.14 | `pyproject.toml:65-66` | `:75-76` |
| A1.5 | `.env.example:14` | `.env.example:13` |

`A2.11`'s citation of `pyproject.toml:65` for `watchdog` is correct as written and needs no repair.
`A5.1`'s citations (`config.py:9,16-22`; `planner_agent.py:79-85`) are correct as written.

### Rows closed by this step

`A1.1`, `A1.5`, `A1.7`, `A1.12`, `A1.13`, `A1.14`, `A1.15`, `A2.11`, `A5.1`, `C0.4`, `C0.7`,
`C0.8`, plus `N1`–`N3`. `N4` is opened and immediately marked as accepted-risk, deferred to `C6.6`.

Two rows close with a qualifier rather than a plain edit:

- `C0.8` — **verified, no edit required.** The exact pin landed during Step 1.
- `A5.1` — closes on the `load_dotenv` half only (§6.3). Its alternative `num_predict` validator is
  explicitly declined with rationale and forwarded to `C1.x`; the row records that, so Step 5 does
  not inherit a silently dropped suggestion.

Because `A5.1` sits in Section A5 rather than in the step's listed range, Section E's step 3 row
gets a note explaining why it was pulled in — the same treatment `A2.11` got when it was routed to
`C0.7`.

Section E's step 3 row gets the same completion treatment as steps 1 and 2: measured diff stat,
final `ruff` and `pytest` output, smoke-run summary, and an explicit statement that step 4
(`C0.5, C0.6, A3.1–A3.4`) is unblocked.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Dropping `langchain-community` breaks a runtime import path that grep cannot see | The §9 live smoke run exercises real agent construction and execution. If it fails on an import, the dep returns with a comment naming the importer |
| `ruff format`'s 686-line diff hides a behavioral change | It is the **last** commit, touching no file that commits 2–5 left semantically pending; `pytest` runs before and after with identical results |
| §6.3 scoping dotenv to cwd breaks a contributor who relies on the upward walk — e.g. keeps one `.env` at a monorepo root and runs `rudra` from a subdirectory | Behavior change is intentional and is the fix A5.1 asks for. `test_cwd_dotenv_is_honored` pins the supported case. Real environment variables are unaffected (`override=False` retained), so the documented escape hatch still works. Called out in the A5.1 closing note |
| `--verbose` three-state conversion changes CLI behavior | `test_cli` coverage does not exist yet (Step 4). Verified manually across all three invocations: absent, `--verbose`, `--no-verbose` |
| `update_plan`'s new rejection makes a marginal planner loop on retries | The rejection message names the offending items and gives a corrected example, matching the shrink-rejection pattern already proven at `:69-74`. If a 32B model still loops, the defensive filter in `_parse_pending_files` means the run degrades to today's behavior minus the bad file, not to a hang |
| `importlib.metadata` fallback masks a genuinely broken install | The fallback string is `0.0.0+unknown`, which is visibly wrong in `--version` output rather than silently plausible |
