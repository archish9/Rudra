# Step 3 — Correctness Bugs, Ruff, and Dependency Hygiene: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the eleven Stage-I correctness and hygiene defects that Section E step 3 lists, so Step 4 can add CI against a lint-clean, correctly-declared, deterministically-configured codebase.

**Architecture:** Six independent commits against an existing Python package, ordered so that behavioral changes land before the repo-wide formatter runs. Nothing here adds a feature: it fixes a version literal, a duplicated and partly-wrong dependency list, an import-time configuration freeze, a dotenv lookup that escapes its project, and a plan parser that turns prose into filenames.

**Tech Stack:** Python 3.12, `uv` (env + lockfile), `ruff` 0.15.8 (lint + format), `pytest` 9, `typer` (CLI), `python-dotenv`, `langchain-core` (`@tool`).

**Spec:** `docs/superpowers/specs/2026-08-07-step3-correctness-and-hygiene-design.md` (commit `660c993`)

---

## Global Constraints

These apply to **every** task. They come from `CLAUDE.md` §2 and the spec.

- **Never fix a bug on discovery.** It must already exist in `TODO.md` as `PENDING` with `file:line` evidence. Task 1 does that logging for everything new; no later task may fix something Task 1 did not list.
- **Evidence-based only.** Every claim about the codebase cites `file.py:line`. If you cannot verify it, say so rather than asserting it.
- **Verify before claiming done.** Run the command and paste the output. Never write `DONE` next to an unrun command.
- **Update `TODO.md` in the same commit as the code** that closes its rows.
- **Python floor is 3.12** (`pyproject.toml:11`). `tomllib` is in the stdlib; do not add `toml` or `tomli`.
- **`deepagents` stays pinned exactly at `==0.7.4`** (`pyproject.toml:27`). Do not relax it to `>=`; `src/rudra/compat/` monkeypatches its internals.
- **Ruff config is already set** at `pyproject.toml` `[tool.ruff]`: `line-length = 100`, `target-version = "py312"`, `select = ["E", "F", "I", "W"]`, `ignore = ["E501"]`. Do not change these values.
- **Do not touch** `src/rudra/agent/planner_agent.py:63` (the static "do NOT call `ask_user`" rule). It is surface #6 of the §0.5 static-Q&A purge, owned by `C6.8a` in Step 10.
- **Do not touch** the orchestrator's file-exists success test (`A1.8`). It is Step 9's `C6.6`.
- **Do not** introduce a provider abstraction or a TOML config loader. Those are Steps 5 and 6 (`C1.x`, `C2.x`).

### Measured baseline (re-confirmed 2026-08-07, after `uv sync`)

Use these as the before-numbers. They are real, not estimates.

| Command | Output |
|---|---|
| `.venv/bin/pytest -q` | `37 passed in 2.43s` |
| `.venv/bin/ruff check src/ tests/` | `Found 30 errors.` (25 `--fix`, 3 `--unsafe-fixes`, 2 manual) |
| `.venv/bin/ruff format --check src/ tests/` | `14 files would be reformatted, 14 files already formatted` |
| `.venv/bin/rudra --version` | `Rudra v0.1.0` ← wrong, this is A1.1 |
| `.venv/bin/python -c "from importlib.metadata import version; print(version('rudra'))"` | `0.2.0` ← correct |

> The spec says the existing suite is 36 tests, inherited from Step 2's report. The measured count is **37**. Use 37.

---

## File Structure

| File | Change | Responsibility after this plan |
|---|---|---|
| `TODO.md` | Modify | Ledger. Task 1 adds 4 rows + repairs 3 citations; each later task marks its rows `DONE` |
| `pyproject.toml` | Modify | Package metadata + deps. Loses 7 deps, gains 1, gains a `[dependency-groups]` and a `[tool.ruff.format]` |
| `src/rudra/__init__.py` | Modify | Package version, sourced from installed metadata rather than a literal |
| `src/rudra/config.py` | Modify | Config dataclasses **plus** the lazy accessor. `.env` loading moves inside `Config.load()` |
| `src/rudra/cli.py` | Modify | CLI. Converts to `get_config()`; `--verbose` becomes three-state |
| `src/rudra/agent/planner_agent.py` | Modify | `get_config()` conversion only |
| `src/rudra/agent/coder_agent.py` | Modify | `get_config()` conversion only |
| `src/rudra/agent/main_agent.py` | Modify | `get_config()` conversion + `_parse_pending_files` defensive filter |
| `src/rudra/tools/planning_tools.py` | Modify | Gains `looks_like_path()` (module-level, the single definition) + `update_plan`'s blocking rejection |
| `.env.example` | Modify | Must be copyable to `.env` and produce config.py's documented defaults |
| `tests/test_package_version.py` | **Create** | A1.1 regression |
| `tests/test_config.py` | **Create** | A1.15 + A5.1 regressions |
| `tests/test_plan_items.py` | **Create** | A1.7 regressions |

**Why `looks_like_path` lives in `planning_tools.py`:** it is imported by `main_agent.py`. Verified there is no cycle — `planning_tools.py` imports nothing from `rudra` (only `pathlib` and `langchain_core.tools`), and `main_agent.py` already imports from `rudra.config` and `rudra.state` at module level.

---

## Task 1: Log new findings and repair drifted citations

Nothing is fixed in this task. It exists because `CLAUDE.md` §2 rule 2 forbids fixing anything not already listed as `PENDING`, and the spec found four defects that are not in the ledger.

**Files:**
- Modify: `TODO.md` (Section A tables, and the `A1.5`/`A1.13`/`A1.14` rows)

**Interfaces:**
- Consumes: nothing
- Produces: four new ledger row IDs that Tasks 2, 4, and 5 mark `DONE`. Allocate them as the next free numbers in the existing `A2.x` sequence and **record the chosen IDs at the top of your commit message**, because later tasks refer to them by role, not by number:
  - `<N1>` = unused runtime dependencies (closed by Task 2)
  - `<N2>` = `.env.example` temperature drift (closed by Task 4)
  - `<N3>` = `.env.example` model-below-32B drift (closed by Task 4)
  - `<N4>` = `looks_like_path` single-word gap (opened as accepted-risk by Task 5, never closed)

- [ ] **Step 1: Confirm the environment is usable**

The `.venv/` directory may be missing — it is gitignored and was absent when this plan was written.

Run:
```bash
uv sync
.venv/bin/pytest -q
```
Expected: `37 passed`. If `uv sync` fails, stop and report; every later verification depends on it.

- [ ] **Step 2: Verify the four new findings still reproduce**

Do not take the spec's word for any of these. Run each and keep the output for the ledger rows.

```bash
# <N1> — six runtime deps with zero import sites
grep -rn "fastapi\|uvicorn\|tavily\|duckduckgo\|langchain_community\|^import requests\|^from requests" src/

# <N2>, <N3> — .env.example drift
grep -n "OLLAMA_TEMPERATURE\|OLLAMA_MODEL\|OLLAMA_NUM_PREDICT" .env.example
grep -n "temperature\|model\|num_predict" src/rudra/config.py
```

Expected: the first `grep` prints **nothing** (confirming all six are unused). The second pair shows `.env.example:11` = `0.7` vs `config.py:20` = `0.3`, and `.env.example:6,9` = `qwen3:14b` / `:10` = `qwen3-coder:30b`, all below D6's 32B floor.

- [ ] **Step 3: Verify the three drifted citations**

```bash
grep -n "langgraph-checkpoint-sqlite" pyproject.toml   # expect :33 and :68 (ledger says :30, :58)
grep -n "Homepage\|Repository" pyproject.toml          # expect :75-76   (ledger says :65-66)
grep -n "OLLAMA_NUM_PREDICT" .env.example              # expect :13      (ledger says :14)
```

- [ ] **Step 4: Add the four rows to `TODO.md`**

Add to the appropriate Section A table, following the existing row format exactly (`| ID | PENDING | description | evidence |`). Use the real line numbers from Steps 2–3, not the ones written here.

```markdown
| <N1> | PENDING | **Six runtime dependencies have zero import sites in `src/`.** Every user installs `langchain-community` (`pyproject.toml:39`), `fastapi` (`:51`), `uvicorn[standard]` (`:52`), `tavily-python` (`:60`), `duckduckgo-search` (`:61`), `requests` (`:63`) for nothing. The fastapi/uvicorn pair is commented "Web Server (optional API layer)", but no such layer exists and no ledger item plans one. Drop with the rest of the dependency hygiene work (**C0.7**) | `grep -rn "fastapi\|uvicorn\|tavily\|duckduckgo\|langchain_community\|requests" src/` → no hits |
| <N2> | PENDING | `.env.example:11` sets `OLLAMA_TEMPERATURE=0.7`; `config.py:20` defaults to `0.3`. A user copying the example silently gets different sampling behavior from the documented default. Fix with **A1.5** | `.env.example:11` vs `src/rudra/config.py:20` |
| <N3> | PENDING | `.env.example:6,9` ship `qwen3:14b` and `:10` ships `qwen3-coder:30b` — all below **D6**'s 32B minimum, so the shipped example contradicts the locked decision. Example file only; changing `config.py:17-19`'s own defaults is a behavior change belonging to Step 5/6 (`C1.x`/`C2.x`). Fix with **A1.5** | `.env.example:6,9,10` vs TODO.md D6 |
| <N4> | PENDING | **Accepted risk, not a bug to fix here.** `looks_like_path` (A1.7) rejects prose by whitespace and trailing punctuation, so a single-word task description (`authentication`, `setup`) still passes as a filename. Closing it needs either a static allowlist of extensionless filenames (§0.5 rules that out) or an LLM judgment (D9 keeps gates deterministic). The real fix is **C6.6**'s deterministic completion gate in Step 9, which tests behavior instead of file existence | `src/rudra/tools/planning_tools.py` — `looks_like_path` |
```

- [ ] **Step 5: Repair the three drifted citations in place**

Edit the existing rows — do not add new ones.

| Row | Find | Replace |
|---|---|---|
| `A1.13` | `pyproject.toml:30` and `:58` | `pyproject.toml:33` and `:68` |
| `A1.14` | `pyproject.toml:65-66` | `pyproject.toml:75-76` |
| `A1.5` | `.env.example:14` | `.env.example:13` |

- [ ] **Step 6: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): log Step-3 findings and repair drifted citations

Four defects found while designing Step 3, logged PENDING before any fix
per CLAUDE.md rule 2: six unused runtime deps, two .env.example drifts
beyond A1.5, and the accepted single-word gap in A1.7's predicate.

Also repairs three citations that drifted as the files changed:
A1.13 (:30,:58 -> :33,:68), A1.14 (:65-66 -> :75-76), and
A1.5 (.env.example:14 -> :13).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Dependency hygiene in `pyproject.toml`

Closes `A1.12`, `A1.13`, `A1.14`, `A2.11`, `C0.7`, `C0.8`, `<N1>`.

**Files:**
- Modify: `pyproject.toml:25-70` (the `dependencies` array) and `:74-76` (`[project.urls]`)

**Interfaces:**
- Consumes: `<N1>` from Task 1
- Produces: a `[dependency-groups] dev` table containing `ruff` and `pytest`. Task 6 relies on `.venv/bin/ruff` still existing after this change.

- [ ] **Step 1: Record the pre-change state**

```bash
.venv/bin/python -c "import importlib.metadata as m; print(len(list(m.distributions())))"
```
Keep the number. You will compare after Step 5 to show the install actually shrank.

- [ ] **Step 2: Edit the `dependencies` array**

Apply all six edits below to `pyproject.toml`.

1. **Delete** the duplicate at `:68` — the *second* `"langgraph-checkpoint-sqlite>=3.0.3",`, the one on the last line of the array. Keep the one at `:33` under the "Core Agent Framework" comment. *(A1.13)*
2. **Delete** `"watchdog>=4.0.0",` at `:65` **and its comment line** `# File watching for watch command` — the `watch` command was deleted by D17 in Step 2. *(A2.11)*
3. **Delete** these six lines and any comment line that exists only to head them (`# Web Server (optional API layer)`, `# Web Search Tools`, `# HTTP Requests`): *(`<N1>`)*
   ```
   "langchain-community>=0.4.1",
   "fastapi>=0.135.2",
   "uvicorn[standard]>=0.40.0",
   "tavily-python>=0.7.20",
   "duckduckgo-search>=7.0.0",
   "requests>=2.32.0",
   ```
4. **Delete** `"ruff>=0.15.8",` and `"pytest>=9.0.2",` and their comment lines (`# Code Quality & Linting Tools`, `# Testing Framework`) from `dependencies`. *(C0.7)*
5. **Add** to the "Core Agent Framework" block, directly after the `langgraph-checkpoint-sqlite` line: *(A1.12)*
   ```
   # Imported at runtime by main_agent.create_main_agent; was transitive only.
   "aiosqlite>=0.22.1",
   ```
6. **Leave `"deepagents==0.7.4"` at `:27` exactly as it is**, including its comment block. *(C0.8 — this row closes as verified-no-edit; the pin landed in Step 1.)*

- [ ] **Step 3: Add the dev dependency group**

Insert after the `dependencies = [...]` array closes, before `[project.scripts]`:

```toml
[dependency-groups]
dev = [
    "ruff>=0.15.8",
    "pytest>=9.0.2",
]
```

- [ ] **Step 4: Fix the project URLs**

Replace `[project.urls]` at `:74-76`: *(A1.14)*

```toml
[project.urls]
Homepage = "https://github.com/archish9/RudraAnvil"
Repository = "https://github.com/archish9/RudraAnvil"
```

- [ ] **Step 5: Re-lock and verify the tooling survived**

```bash
uv sync
.venv/bin/ruff --version
.venv/bin/pytest -q
.venv/bin/python -c "import importlib.metadata as m; print(len(list(m.distributions())))"
```

Expected: `uv sync` regenerates `uv.lock` and removes packages. `ruff --version` still prints a version — `uv sync` installs the `dev` group by default, which is the whole point of Step 3. `pytest` still prints `37 passed`. The distribution count is **lower** than Step 1's number.

If `.venv/bin/ruff` is gone, `uv sync` did not install the dev group; run `uv sync --group dev` and note it in the commit message so Task 6 and Step 4's CI use the same invocation.

- [ ] **Step 6: Prove `aiosqlite` is now a declared dependency, not a lucky transitive**

```bash
grep -n "aiosqlite" pyproject.toml
.venv/bin/python -c "import aiosqlite; print(aiosqlite.__version__)"
```
Expected: a hit in the `dependencies` array, and a version printed.

- [ ] **Step 7: Smoke the CLI**

Dropping six packages is the riskiest edit in this task. `langchain-community` in particular was dropped on grep evidence alone.

```bash
.venv/bin/rudra --help
```
Expected: the help text, **no traceback**. If this fails with `ModuleNotFoundError`, restore the named package with a comment recording which module imported it, and update `<N1>` in `TODO.md` to say so.

> The definitive check for this is the live agent run in Task 7, which imports far more than `--help` does. Do not consider `<N1>` proven until Task 7 passes.

- [ ] **Step 8: Mark the ledger rows and commit**

In `TODO.md`, mark `A1.12`, `A1.13`, `A1.14`, `A2.11`, `C0.7`, `<N1>` as `DONE` with the Step 5–7 output. Mark `C0.8` as `DONE` with the note that it required **no edit** — the exact pin landed during Step 1 and was never marked.

```bash
git add pyproject.toml uv.lock TODO.md
git commit -m "fix(pyproject): dependency hygiene

- Drop the duplicate langgraph-checkpoint-sqlite entry (A1.13)
- Declare aiosqlite, imported at runtime by create_main_agent but
  previously only transitive (A1.12)
- Point the project URLs at the real remote, archish9/RudraAnvil (A1.14)
- Drop watchdog, orphaned when D17 deleted the watch command (A2.11)
- Drop six runtime deps with zero import sites in src/: langchain-community,
  fastapi, uvicorn, tavily-python, duckduckgo-search, requests
- Move ruff and pytest into [dependency-groups] dev so end users stop
  installing the toolchain (C0.7)

C0.8 verified as already satisfied: the deepagents==0.7.4 exact pin landed
during Step 1 and needed no edit here.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Single-source the version

Closes `A1.1`.

**Files:**
- Modify: `src/rudra/__init__.py` (all 3 lines)
- Test: `tests/test_package_version.py` (create)

**Interfaces:**
- Consumes: nothing
- Produces: `rudra.__version__` — already imported by `src/rudra/cli.py:15` and used at `:83` (banner) and `:153` (`--version`). The name and its `str` type do not change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_package_version.py`:

```python
"""Guards A1.1 — rudra.__version__ drifted from pyproject.toml's [project] version.

__init__.py hardcoded "0.1.0" while pyproject declared "0.2.0", so both
`rudra --version` (cli.py:153) and the startup banner (cli.py:83) printed a
number that had been wrong since the 0.2.0 bump. The fix reads installed
package metadata, making pyproject.toml the single source; this test asserts
the two can never disagree again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import rudra

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_version_matches_pyproject() -> None:
    assert rudra.__version__ == _declared_version(), (
        f"rudra.__version__ is {rudra.__version__!r} but pyproject.toml declares "
        f"{_declared_version()!r}. If you just bumped the version, re-run `uv sync` "
        "so the installed metadata catches up."
    )


def test_version_is_read_from_package_metadata() -> None:
    """The drift returns the moment someone reassigns a literal as the primary source."""
    source = Path(rudra.__file__).read_text(encoding="utf-8")
    assert "importlib.metadata" in source, (
        "__init__.py must derive __version__ from installed metadata, not a literal"
    )
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/test_package_version.py -v`

Expected: **both** tests FAIL. `test_version_matches_pyproject` fails with `'0.1.0' != '0.2.0'`; `test_version_is_read_from_package_metadata` fails because `__init__.py` does not mention `importlib.metadata` yet.

> A first draft of this test asserted `'__version__ = "' not in source`. That is wrong — the `except PackageNotFoundError` branch in Step 3 assigns exactly that literal, so the test would have failed *on the correct implementation*. Asserting the metadata import is the property that actually matters.

- [ ] **Step 3: Rewrite `src/rudra/__init__.py`**

Replace the entire file:

```python
"""Rudra - Autonomous Coding Agent CLI"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rudra")
except PackageNotFoundError:  # imported from a source tree that was never installed
    __version__ = "0.0.0+unknown"
```

The `except` branch matters: `pytest` can import `rudra` from `src/` without an install, and a raise at import time would break collection for the whole suite. The sentinel is deliberately implausible so a broken install is visible in `--version` output rather than passing for a real number.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
.venv/bin/pytest tests/test_package_version.py -v
.venv/bin/rudra --version
```
Expected: 2 passed, and `Rudra v0.2.0` — no longer `v0.1.0`.

- [ ] **Step 5: Confirm nothing else regressed**

Run: `.venv/bin/pytest -q`
Expected: `39 passed` (37 baseline + 2 new).

- [ ] **Step 6: Mark `A1.1` DONE in `TODO.md` and commit**

```bash
git add src/rudra/__init__.py tests/test_package_version.py TODO.md
git commit -m "fix: single-source the version from package metadata

__init__.py hardcoded 0.1.0 while pyproject declared 0.2.0, so
\`rudra --version\` and the banner had printed the wrong number since the
bump (A1.1). Reading importlib.metadata makes pyproject the single source,
so the next bump cannot reintroduce the drift.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Defer config load, scope dotenv to the invocation directory

Closes `A1.15`, `A1.5`, `A5.1`, `<N2>`, `<N3>`. This is the largest task; read it fully before starting.

**Files:**
- Modify: `src/rudra/config.py` (whole file)
- Modify: `src/rudra/cli.py:17`, `:179`, `:198-199`, `:276-277`, and the callback body
- Modify: `src/rudra/agent/planner_agent.py:11`, `:78-81`
- Modify: `src/rudra/agent/coder_agent.py:8`, `:58-61`
- Modify: `src/rudra/agent/main_agent.py:11`, `:527-528`
- Modify: `.env.example:6`, `:9`, `:10`, `:11`, `:13`
- Test: `tests/test_config.py` (create)

**Interfaces:**
- Consumes: `<N2>`, `<N3>` from Task 1
- Produces:
  - `rudra.config.get_config() -> Config` — cached; loads on first call
  - `rudra.config.reset_config() -> None` — drops the cache; test-support only
  - The module-level name `rudra.config.config` is **removed**. Any later task that writes `from rudra.config import config` will `ImportError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
"""Guards A1.15 and A5.1 — configuration was frozen at import, from the wrong .env.

A1.15: config.py ended with `config = Config.load()`, so every
`field(default_factory=lambda: os.getenv(...))` was evaluated the first time
`rudra.config` was imported. Nothing could change the environment afterwards.

A5.1: config.py:9 called bare `load_dotenv()`, which walks upward from the
*calling module's* directory. A `.env` anywhere above the installed package was
therefore loaded for any process, whatever directory it ran in — which is how
Step 1's smoke run, launched from a tempdir outside the repo, still picked up
the repo's own .env and sent num_predict=-1 to the model.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import rudra.config
from rudra.config import get_config, reset_config

OLLAMA_ENV_VARS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_MODEL_PLANNER",
    "OLLAMA_MODEL_CODER",
    "OLLAMA_TEMPERATURE",
    "OLLAMA_TIMEOUT",
    "OLLAMA_NUM_PREDICT",
)


@pytest.fixture(autouse=True)
def clean_config_state(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with no cached Config and no inherited OLLAMA_* vars.

    Without the delenv loop these tests pass or fail based on the developer's
    own shell, which is exactly the class of bug A5.1 describes.
    """
    for name in OLLAMA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def test_get_config_is_cached() -> None:
    assert get_config() is get_config()


def test_reset_config_rereads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.15: an env change after import must be visible, which it was not."""
    monkeypatch.setenv("OLLAMA_MODEL", "first-model")
    assert get_config().ollama.model == "first-model"

    monkeypatch.setenv("OLLAMA_MODEL", "second-model")
    assert get_config().ollama.model == "first-model", "cache should hold until reset"

    reset_config()
    assert get_config().ollama.model == "second-model"


def test_there_is_no_module_level_config() -> None:
    """An alias would still be evaluated at import time by whoever binds it."""
    assert not hasattr(rudra.config, "config")


def test_dotenv_is_looked_up_at_the_cwd_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5.1: the .env path must be explicit, not resolved by an upward walk.

    This one asserts the call, not an observable value, and that is deliberate.
    The original defect was that bare load_dotenv() resolves relative to
    config.py's own location inside site-packages — reproducing it behaviorally
    would mean planting a .env above the installed package, which no test should
    do to a developer's machine. What is checkable, and what actually fixes it,
    is that exactly one explicit path is passed and it is derived from the cwd.
    """
    recorded: list[object] = []

    def spy(*args: object, **kwargs: object) -> bool:
        recorded.append(args[0] if args else kwargs.get("dotenv_path"))
        return False

    monkeypatch.setattr(rudra.config, "load_dotenv", spy)
    monkeypatch.chdir(tmp_path)

    reset_config()
    get_config()

    assert recorded == [tmp_path / ".env"]


def test_dotenv_in_the_cwd_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converse — guards against over-correcting into ignoring .env entirely."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reset_config()
    assert get_config().ollama.num_predict == 4096


def test_a_real_env_var_beats_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """override=False is retained: the documented escape hatch must keep working."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "8192")

    reset_config()
    assert get_config().ollama.num_predict == 8192
    assert os.environ["OLLAMA_NUM_PREDICT"] == "8192"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`

Expected: **collection fails** with `ImportError: cannot import name 'get_config' from 'rudra.config'`. That is the correct failure — the accessor does not exist yet.

- [ ] **Step 3: Rewrite `src/rudra/config.py`**

Replace the whole file. The dataclass bodies are unchanged from the current file; only the `load_dotenv` placement and the module tail differ.

```python
"""Configuration settings for Rudra."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class OllamaConfig:
    """Ollama LLM configuration."""

    base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_planner: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_PLANNER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_coder: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_CODER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    temperature: float = field(default_factory=lambda: float(os.getenv("OLLAMA_TEMPERATURE", "0.3")))
    timeout: int = field(default_factory=lambda: int(os.getenv("OLLAMA_TIMEOUT", "300")))
    num_predict: int = field(default_factory=lambda: int(os.getenv("OLLAMA_NUM_PREDICT", "131072")))


@dataclass
class AgentConfig:
    """Agent execution configuration."""

    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")


@dataclass
class Config:
    """Main configuration container."""

    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)

    # Paths
    checkpoint_dir: str = ".rudra"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from the environment, reading the project's .env first.

        The .env path is explicit rather than bare `load_dotenv()`, which resolves
        by walking upward from *this module's* directory — so a .env anywhere above
        the installed package leaked into every project the user ran Rudra in,
        regardless of the invocation directory. See TODO.md A5.1.

        `override=False` is python-dotenv's default and is deliberately kept: a
        real environment variable still beats .env.
        """
        load_dotenv(Path.cwd() / ".env")
        return cls()

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Get the checkpoint directory path for a project."""
        checkpoint_path = project_dir / self.checkpoint_dir
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        return checkpoint_path


_config: Config | None = None


def get_config() -> Config:
    """Return the process-wide Config, loading it on first use.

    Replaces the module-level `config = Config.load()`, which froze the entire
    environment at first import of this module. See TODO.md A1.15.
    """
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

- [ ] **Step 4: Convert the three agent call sites**

These are mechanical. In each file, change the import and prefix each use.

`src/rudra/agent/planner_agent.py` — line 11 becomes `from rudra.config import get_config`, then lines 78-81:

```python
    cfg = get_config()
    model = ChatOllama(
        model=cfg.ollama.model_planner,
        base_url=cfg.ollama.base_url,
        temperature=cfg.ollama.temperature,
        num_predict=cfg.ollama.num_predict,
        reasoning=True,
    )
```

`src/rudra/agent/coder_agent.py` — line 8 becomes `from rudra.config import get_config`, then lines 58-61 take the same shape: bind `cfg = get_config()` immediately before the `ChatOllama(...)` call and replace `config.ollama.X` with `cfg.ollama.X`. Keep `model=cfg.ollama.model_coder` — the coder uses the coder model.

`src/rudra/agent/main_agent.py` — line 11 becomes `from rudra.config import get_config`, then lines 527-528 inside the `AgentContext(...)` construction:

```python
        planner_model=get_config().ollama.model_planner,
        coder_model=get_config().ollama.model_coder,
```

- [ ] **Step 5: Convert `cli.py`, including the Typer default**

Line 17 becomes `from rudra.config import get_config`.

The panel strings at `:198-199` and `:276-277` become `get_config().ollama.model_planner` / `.model_coder`. Both are inside function bodies, so they need no restructuring.

`:179` is the one that a lazy accessor alone does **not** fix — it is a decorator argument, evaluated at import:

```python
    verbose: Optional[bool] = typer.Option(None, "--verbose/--no-verbose", "-V", help="Show detailed output"),
```

Then resolve it in the callback body. Place this **immediately after** the `if ctx.invoked_subcommand is not None: return` guard at `:186-187` and **before** `project_path = get_project_path(project_dir)` at `:189`, so every later read of `verbose` sees a real `bool`:

```python
    # A default of `config.agent.verbose` here would be evaluated when this
    # module is imported, which is the A1.15 defect. Three-state instead:
    # absent -> consult config, --verbose -> True, --no-verbose -> False.
    if verbose is None:
        verbose = get_config().agent.verbose
```

`Optional` is already imported at `src/rudra/cli.py` — verify with `grep -n "^from typing" src/rudra/cli.py` before relying on it.

- [ ] **Step 6: Run the tests**

```bash
.venv/bin/pytest tests/test_config.py -v
```
Expected: 6 passed.

- [ ] **Step 7: Prove no import site was missed**

```bash
grep -rn "from rudra.config import config\|config\.ollama\|config\.agent" src/
```
Expected: **no hits** for the bare `config.` form. Every remaining hit must read `cfg.ollama.` or `get_config().`. A missed site is an `ImportError` at runtime, and `--help` alone will not always catch it.

- [ ] **Step 8: Verify the three-state flag by hand**

```bash
.venv/bin/rudra --help                  # --verbose/--no-verbose still listed, no traceback
VERBOSE=false .venv/bin/rudra --help    # still no traceback
```

- [ ] **Step 9: Fix `.env.example`**

Apply all five edits so a copied `.env` reproduces `config.py`'s documented defaults:

| Line | From | To | Item |
|---|---|---|---|
| `:6` | `OLLAMA_MODEL=qwen3:14b` | `OLLAMA_MODEL=qwen3:32b` | `<N3>` |
| `:9` | `OLLAMA_MODEL_PLANNER=qwen3:14b` | `OLLAMA_MODEL_PLANNER=qwen3:32b` | `<N3>` |
| `:10` | `OLLAMA_MODEL_CODER=qwen3-coder:30b` | `OLLAMA_MODEL_CODER=qwen3-coder:32b` | `<N3>` |
| `:11` | `OLLAMA_TEMPERATURE=0.7` | `OLLAMA_TEMPERATURE=0.3` | `<N2>` |
| `:13` | `OLLAMA_NUM_PREDICT=2048` | `OLLAMA_NUM_PREDICT=131072` | `A1.5` |

Add above the model block:

```
# Minimum supported local model size is 32B (TODO.md D6). Smaller models
# were the design center for the deleted qwen3:14b workarounds and are no
# longer supported.
```

Then confirm every value matches its `config.py` default:

```bash
grep -n "OLLAMA_" .env.example
grep -n "getenv" src/rudra/config.py
```

- [ ] **Step 10: Full suite, then commit**

```bash
.venv/bin/pytest -q
```
Expected: `45 passed` (39 after Task 3 + 6 new).

Mark `A1.15`, `A1.5`, `<N2>`, `<N3>` DONE. Mark `A5.1` DONE **on the `load_dotenv` half only**, and record on the row that its alternative — validating `num_predict > 0` — was deliberately declined, because A5.1's own leg-(1) evidence shows `-1` is valid for plain local Ollama and only the cloud passthrough rejects it. Forward that to `C1.x` so Step 5 does not inherit a silently dropped suggestion.

```bash
git add src/rudra/config.py src/rudra/cli.py src/rudra/agent/ .env.example tests/test_config.py TODO.md
git commit -m "refactor(config): defer config load to first use, scope dotenv to cwd

config.py ended with a module-level \`config = Config.load()\`, freezing every
os.getenv() default at first import (A1.15). Replaced with a cached
get_config() plus reset_config() for tests; the module-level name is removed
outright rather than aliased, since an alias is still evaluated at import by
whoever binds it. cli.py's --verbose Typer default was the non-obvious case —
a decorator argument — so it becomes three-state and resolves in the callback.

load_dotenv() was also bare, resolving by walking upward from this module's
own directory, so a .env above the installed package applied to every project
regardless of invocation directory. That is how Step 1's tempdir smoke run
still read the repo's .env and sent num_predict=-1 (A5.1). Now an explicit
Path.cwd() / '.env', with override=False retained.

A5.1's alternative fix — validating num_predict > 0 — is declined: -1 is
valid for plain local Ollama and only the cloud endpoint rejects it, so a
validator would break the local-first case. Forwarded to C1.x.

.env.example repaired to match config.py's defaults: NUM_PREDICT 2048 ->
131072 (A1.5), TEMPERATURE 0.7 -> 0.3, and the models raised above D6's 32B
floor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Reject task descriptions as plan items

Closes `A1.7`; opens `<N4>` as accepted risk.

**Files:**
- Modify: `src/rudra/tools/planning_tools.py` (add `looks_like_path` at module level; add the rejection inside `update_plan`)
- Modify: `src/rudra/agent/main_agent.py:45-50`
- Test: `tests/test_plan_items.py` (create)

**Interfaces:**
- Consumes: `get_config` is not needed here; nothing from Task 4
- Produces: `rudra.tools.planning_tools.looks_like_path(item: str) -> bool` — the single definition, imported by `main_agent.py`. No cycle: `planning_tools.py` imports only `pathlib` and `langchain_core.tools`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plan_items.py`:

```python
"""Guards A1.7 — every `- [ ]` line was treated as a filename.

_parse_pending_files filtered only on the checkbox marker, so a planner
emitting `- [ ] Set up auth` handed the orchestrator the string "Set up auth"
as a target file. Since Step 2 deleted EnforceTargetFileMiddleware and the
success test is still "does the file exist on disk" (A1.8), the coder could
create a file literally named `Set up auth` and leave it behind.

Enforcement is split deliberately: update_plan blocks and explains, so the
model can correct itself; _parse_pending_files filters silently, because
PLAN.md is plain text that the user and edit_file can both rewrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.agent.main_agent import _parse_pending_files
from rudra.tools.planning_tools import create_planning_tools, looks_like_path


@pytest.mark.parametrize(
    "item",
    ["main.py", "src/models.py", "Makefile", "Dockerfile", ".gitignore", "requirements.txt"],
)
def test_real_filenames_are_accepted(item: str) -> None:
    assert looks_like_path(item)


@pytest.mark.parametrize(
    "item",
    [
        "Create main.py",
        "Set up auth",
        "Install dependencies",
        "Create FastAPI project structure",
        "Add tests.",
        "",
        "   ",
    ],
)
def test_prose_is_rejected(item: str) -> None:
    assert not looks_like_path(item)


def test_single_word_prose_is_a_known_accepted_gap() -> None:
    """N4: pinned deliberately so the gap is a recorded decision, not a surprise.

    Closing it needs a static allowlist of extensionless filenames (ruled out
    by TODO.md §0.5) or an LLM judgment (ruled out by D9). The real fix is
    C6.6's deterministic completion gate in Step 9.
    """
    assert looks_like_path("authentication")


def test_update_plan_rejects_prose_without_writing(tmp_path: Path) -> None:
    update_plan, _read_plan, _assign = create_planning_tools(tmp_path, task="build an app")

    result = update_plan.invoke(
        {"plan_markdown": "- [ ] main.py\n- [ ] Set up auth\n- [ ] Install dependencies"}
    )

    assert result.startswith("REJECTED:")
    assert "Set up auth" in result
    assert "Install dependencies" in result
    assert not (tmp_path / ".rudra" / "PLAN.md").exists(), "nothing may be written on rejection"


def test_update_plan_accepts_a_clean_plan(tmp_path: Path) -> None:
    update_plan, _read_plan, _assign = create_planning_tools(tmp_path, task="build an app")

    result = update_plan.invoke({"plan_markdown": "- [ ] main.py\n- [ ] models.py"})

    assert result.startswith("Plan saved.")
    assert (tmp_path / ".rudra" / "PLAN.md").read_text(encoding="utf-8") == (
        "- [ ] main.py\n- [ ] models.py"
    )


def test_parse_pending_files_skips_prose() -> None:
    """The defensive half: PLAN.md is user-editable, so update_plan is not the only writer."""
    plan = "- [ ] main.py\n- [ ] Set up auth\n- [x] done.py\n- [ ] src/models.py"
    assert _parse_pending_files(plan) == ["main.py", "src/models.py"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `.venv/bin/pytest tests/test_plan_items.py -v`

Expected: collection fails with `ImportError: cannot import name 'looks_like_path'`.

- [ ] **Step 3: Add the predicate to `planning_tools.py`**

Insert at module level, after the `from langchain_core.tools import tool` import at `:22` and before `def create_planning_tools(...)` at `:25`:

```python
def looks_like_path(item: str) -> bool:
    """True if a plan checklist item is plausibly a file path, not a prose task.

    Guards A1.7. The whitespace rule carries the weight: every prose form the
    ledger and the update_plan docstring record contains a space. Requiring an
    extension or a slash instead would close the single-word gap (N4) but would
    reject Makefile, Dockerfile, LICENSE and Procfile unless a static allowlist
    were carried — and TODO.md §0.5 rules out exactly that kind of lookup table.
    """
    item = item.strip()
    if not item:
        return False
    if any(character.isspace() for character in item):
        return False
    return item[-1] not in ".,:;!"
```

- [ ] **Step 4: Add the blocking rejection to `update_plan`**

Insert inside `update_plan`, **after** the shrink-rejection block that ends at `:74` and **before** the `# Sanitise: drop any .rudra/ items` comment at `:76`:

```python
        # A1.7 — refuse prose before anything reaches disk, so the model gets a
        # correction signal instead of a silently mangled plan.
        offenders: list[str] = []
        for line in plan_markdown.splitlines():
            if "- [ ]" not in line and "- [x]" not in line:
                continue
            item = line.strip().replace("- [ ]", "").replace("- [x]", "").strip()
            if item and not looks_like_path(item):
                offenders.append(item)

        if offenders:
            listed = "\n".join(f"  - {offender}" for offender in offenders)
            return (
                "REJECTED: these checklist items are task descriptions, not filenames:\n"
                f"{listed}\n"
                "Re-emit the plan with one exact filename per item, "
                "e.g. '- [ ] auth.py' instead of '- [ ] Set up auth'."
            )
```

Ordering matters: it must come **after** the shrink check so a plan that both shrinks and contains prose reports the shrink first (that check protects an existing plan already on disk), and **before** the write at `:87-88` so a rejected plan leaves `PLAN.md` untouched.

- [ ] **Step 5: Add the defensive filter to `main_agent.py`**

Add to the imports at the top of `src/rudra/agent/main_agent.py`, next to the existing `from rudra.state import ProjectContext` at `:12`:

```python
from rudra.tools.planning_tools import looks_like_path
```

Replace `_parse_pending_files` at `:45-50` entirely:

```python
def _parse_pending_files(plan_content: str) -> list[str]:
    """Pending filenames from PLAN.md, skipping anything that is not a path.

    The skip is silent by design. update_plan blocks prose and explains why
    (A1.7), which is where the model needs feedback; here the file may have
    been hand-edited by the user or rewritten by the agent's own edit_file, so
    there is no author to correct.
    """
    items = (
        line.strip().replace("- [ ]", "").strip()
        for line in plan_content.splitlines()
        if "- [ ]" in line
    )
    return [item for item in items if looks_like_path(item)]
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `.venv/bin/pytest tests/test_plan_items.py -v`
Expected: **17 passed** — 6 accepted-filename cases + 7 rejected-prose cases + the pinned N4 gap + 2 `update_plan` behavior tests + 1 parser test.

- [ ] **Step 7: Confirm no import cycle was introduced**

```bash
.venv/bin/python -c "import rudra.agent.main_agent; print('ok')"
.venv/bin/pytest -q
```
Expected: `ok`, then `62 passed` (45 after Task 4 + 17 new).

- [ ] **Step 8: Mark the ledger and commit**

Mark `A1.7` DONE. Leave `<N4>` `PENDING` — it is an accepted risk, deferred to `C6.6`; add a note that `test_single_word_prose_is_a_known_accepted_gap` pins the current behavior.

```bash
git add src/rudra/tools/planning_tools.py src/rudra/agent/main_agent.py tests/test_plan_items.py TODO.md
git commit -m "fix(plan): reject task descriptions as plan items

_parse_pending_files treated every '- [ ]' line as a filename, so a planner
emitting '- [ ] Set up auth' handed the orchestrator that string as a target
file — and with EnforceTargetFileMiddleware gone since Step 2 and success
still measured as file-exists (A1.8), a file by that literal name could be
created and left behind (A1.7).

Enforcement is split: update_plan blocks with a REJECTED message naming the
offenders, so the model can re-emit, and nothing is written on rejection;
_parse_pending_files filters silently, because PLAN.md is plain text that the
user and edit_file can both rewrite, so there is no author to correct.

The predicate rejects whitespace and trailing punctuation rather than
requiring an extension, which would reject Makefile and Dockerfile unless a
static allowlist were carried (ruled out by §0.5). Single-word prose still
passes; recorded as an accepted risk, deferred to C6.6.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Ruff lint and format clean

Closes `C0.4`. Last of the code commits, so the formatter never obscures a behavioral diff.

**Files:**
- Modify: `pyproject.toml` (add `[tool.ruff.format]`)
- Modify: `src/rudra/cli.py:70`, `:73`, `:79` (F541) and `src/rudra/tools/planning_tools.py:67-68` (E741)
- Modify: every file `ruff format` touches

**Interfaces:**
- Consumes: `.venv/bin/ruff`, which Task 2 moved into the `dev` group
- Produces: a lint- and format-clean tree. Step 4's CI asserts `ruff check` exits 0.

- [ ] **Step 1: Re-measure — the counts have moved**

Tasks 3–5 added three new test files and rewrote `config.py`, so the 30-error baseline is stale.

```bash
.venv/bin/ruff check src/ tests/ --statistics
.venv/bin/ruff format --check src/ tests/
```
Record both. Everything below is written against the *original* 30; if a rule now has a different count, trust the fresh output.

- [ ] **Step 2: Apply the safe autofixes**

```bash
.venv/bin/ruff check src/ tests/ --fix
.venv/bin/ruff check src/ tests/ --statistics
```
Expected: `W293`, `I001`, `W291`, `F401` clear. Remaining should be `F541` and `E741` only.

- [ ] **Step 3: Review the unsafe fixes before applying them**

```bash
.venv/bin/ruff check src/ tests/ --unsafe-fixes --diff
```

The three known `F541` sites are `src/rudra/cli.py:70`, `:73`, `:79` — all `f`-prefixed strings inside `print_banner()` with no `{}` in them. Read each diff hunk and confirm it is a stray prefix, not a half-written interpolation someone meant to finish. `cli.py:83` **does** contain `{__version__}` and must keep its prefix; ruff does not flag it, so an `f` disappearing from line 83 means something went wrong.

Then apply:
```bash
.venv/bin/ruff check src/ tests/ --unsafe-fixes --fix
```

- [ ] **Step 4: Fix the two `E741` by hand**

At `src/rudra/tools/planning_tools.py:67-68`, `l` is ambiguous with `1` and `I`. Both are comprehension variables inside `update_plan`'s shrink-rejection counter, so the rename is local and total:

```python
            existing_count = sum(
                1 for line in existing_text.splitlines() if "- [ ]" in line or "- [x]" in line
            )
            new_count = sum(
                1 for line in plan_markdown.splitlines() if "- [ ]" in line or "- [x]" in line
            )
```

Check that the enclosing scope has no other `line` binding that this would shadow before committing to the name.

- [ ] **Step 5: Confirm lint is clean**

Run: `.venv/bin/ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 6: Add the formatter config**

Append to `pyproject.toml`, after the `[tool.ruff.lint]` table:

```toml
[tool.ruff.format]
docstring-code-format = true
```

`line-length` and `target-version` under `[tool.ruff]` are inherited by the formatter — do not restate them.

- [ ] **Step 7: Format, then prove nothing behavioral changed**

```bash
.venv/bin/pytest -q          # record the count BEFORE formatting
.venv/bin/ruff format src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q          # must match exactly
```
Expected: the format run reports the reformatted count; `--check` then reports all files already formatted; **both `pytest` counts are identical**. A formatter that changes a test result has changed behavior — stop and investigate.

- [ ] **Step 8: Commit**

Update `C0.4` in `TODO.md` to `DONE`, and record the new **absolute** standard: `ruff check src/ tests/` must exit 0. This replaces `CLAUDE.md` §8's relative rule ("no increase vs the 164 baseline at merge-base `b322978`"). Note on the row that `CLAUDE.md` §8 needs the same wording update when Step 4 lands — do not edit `CLAUDE.md` here.

```bash
git add -A
git commit -m "style: ruff lint and format clean

Clears all 30 outstanding lint errors: 25 by --fix, 3 F541 stray f-string
prefixes in print_banner reviewed individually before applying --unsafe-fixes,
and 2 E741 ambiguous 'l' comprehension variables renamed by hand in
update_plan's shrink-rejection counter (C0.4).

Adds [tool.ruff.format] and formats src/ and tests/. Landed last so the
formatting diff cannot obscure a behavioral change; the test count is
identical before and after.

Step 4's CI can now assert an absolute standard — ruff check exits 0 —
replacing CLAUDE.md §8's relative 'no increase vs 164' baseline.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: End-to-end verification and Section E sign-off

No source changes. This is the gate that proves Task 2's dependency drops were safe and that A5.1 is genuinely fixed.

**Files:**
- Modify: `TODO.md` (Section E step 3 row)

**Interfaces:**
- Consumes: everything from Tasks 2–6
- Produces: the `DONE` record Step 4 reads to know it is unblocked

- [ ] **Step 1: Full local verification**

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
.venv/bin/rudra --help
```
Expected: `All checks passed!`; all files already formatted; `62 passed`; `Rudra v0.2.0`; help text with no traceback.

- [ ] **Step 2: Measure the diff**

```bash
git diff --stat 6956685..HEAD -- src/ tests/ pyproject.toml .env.example
```
Record the output — the Section E row carries it, as steps 1 and 2 did.

- [ ] **Step 3: Live smoke run — bare, with no `num_predict` override**

Steps 1 and 2 both had to export `OLLAMA_NUM_PREDICT=131072` to work around A5.1. **This run must not.** With Task 4 landed, a tempdir run reads no `.env` at all, so `config.py:22`'s default applies on its own — and the run succeeding *without* the override is the acceptance evidence for A5.1.

```bash
WORKDIR=$(mktemp -d)
cd "$WORKDIR"
/Users/archish/Documents/ai-ml/Rudra/.venv/bin/rudra "build a CLI that reverses a string"
echo "exit=$?"
cat .rudra/PLAN.md
ls -la
```

Expected: exit 0; `PLAN.md` items are all real filenames (no prose — Task 5's rejection at work); at least one non-empty generated source file. Run the generated program and confirm it does what was asked.

If it fails with `max_tokens must be positive, got: -1`, Task 4's §6.3 change is not actually working — reopen `A5.1`.

If it fails with `ModuleNotFoundError`, Task 2 dropped a package that is needed transitively. Restore that one package, record which module imported it on the `<N1>` row, and re-run.

- [ ] **Step 4: Write the Section E step 3 completion row**

Follow the format steps 1 and 2 used. It must state:

- Which rows closed: `A1.1`, `A1.5`, `A1.7`, `A1.12`, `A1.13`, `A1.14`, `A1.15`, `A2.11`, `A5.1`, `C0.4`, `C0.7`, `C0.8`, `<N1>`–`<N3>`
- That `A5.1` and `A2.11` were pulled in from outside the step's listed range because their own rows self-direct here, and why
- That `C0.8` closed **verified-no-edit** — the pin landed in Step 1
- That `<N4>` is open as an accepted risk deferred to `C6.6`
- That `A5.1` closed on the `load_dotenv` half only, with the `num_predict` validator declined and forwarded to `C1.x`
- The measured diff stat, final `ruff`/`pytest` output, and the smoke-run summary
- That the smoke run passed **without** the `OLLAMA_NUM_PREDICT` override that steps 1 and 2 required
- **That step 4 (`C0.5, C0.6, A3.1–A3.4`) is unblocked**

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): close out Step 3 — correctness bugs, ruff, dep hygiene

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** — every section maps to a task:

| Spec § | Task |
|---|---|
| §3 venv precondition | Task 1 Step 1 |
| §4 pyproject dep hygiene (incl. N1, C0.8-verify) | Task 2 |
| §5 A1.1 version | Task 3 |
| §6.1–6.2 A1.15 lazy config | Task 4 Steps 1–7 |
| §6.3 A5.1 dotenv scoping | Task 4 Steps 1, 3, 10 |
| §6.5 cli.py:179 Typer default | Task 4 Step 5 |
| §6.6 .env.example (A1.5, N2, N3) | Task 4 Step 9 |
| §7 A1.7 predicate + both enforcement points | Task 5 |
| §8 C0.4 lint + format | Task 6 |
| §9 verification + live smoke | Task 7 |
| §10 ledger rows + citation repairs | Task 1, then each task's own mark-DONE step |
| §11 risks | Mitigations placed inline: langchain-community → Task 2 Step 7 + Task 7 Step 3; format churn → Task 6 Step 7; dotenv behavior change → Task 4 Step 1's `test_dotenv_in_the_cwd_is_honored`; verbose three-state → Task 4 Step 8; update_plan loop → Task 5's split enforcement |

**Type consistency** — `get_config() -> Config` and `reset_config() -> None` are defined in Task 4 Step 3 and used with those exact names in Task 4 Steps 4–5 and in `tests/test_config.py`. `looks_like_path(item: str) -> bool` is defined in Task 5 Step 3 and used with that exact name in Task 5 Steps 4–5 and in `tests/test_plan_items.py`. `create_planning_tools` returns `[update_plan, read_plan, write_task_assignment]` (`planning_tools.py:156`) — the three-way unpack in `tests/test_plan_items.py` matches that order, verified by running it.

**Cumulative test counts** are stated per task and chain: 37 baseline → 39 (Task 3, +2) → 45 (Task 4, +6) → 62 (Task 5, +17) → unchanged through Tasks 6–7.

**Known imprecision, called out rather than hidden:** the ledger IDs `<N1>`–`<N4>` are placeholders. Task 1 allocates the real numbers and records them in its commit message; later tasks refer to them by role. Task 6 Step 1 re-measures the lint counts rather than trusting the baseline, because Tasks 3–5 add three files the 30-error figure predates.
