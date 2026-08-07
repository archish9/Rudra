# Step 4 (amended) — Stack Detection, Regression Net, and CI: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/rudra/stacks/` (deterministic, read-only target-stack detection for Python, Rust, Node, React and Angular), raise a regression net over the seams that survive later rewrites, and stand up GitHub Actions CI — closing Stage I's hard gate.

**Architecture:** A new pure-data module maps marker files on disk to stack profiles and test commands; it reads and returns, never executes. Tests then cover the stable, language-agnostic seams — the stacks module, the currently-untested `compat/deepagents_path.py` monkeypatch, and `main_agent.py`'s helper functions — while deliberately not covering the orchestration loop Step 9 will delete. CI runs Rudra's own Python toolchain only.

**Tech Stack:** Python 3.12, `uv`, `ruff` 0.15.8 (lint + format), `pytest` 9, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-07-multi-language-targets-design.md` (commit `71e54e0`)

---

## Global Constraints

Binding on **every** task. From `CLAUDE.md` §2 and the spec.

- **Never fix a bug on discovery.** It must already exist in `TODO.md` as `PENDING` with `file:line` evidence. Task 1 does all new logging; no later task fixes something Task 1 did not list.
- **Evidence-based only.** Every claim cites `file.py:line`, verified by a command actually run.
- **Verify before claiming done.** Paste real command output. Never write `DONE` beside an unrun command.
- **Update `TODO.md` in the same commit as the code** that closes its rows.
- **Two toolchains, never conflated.** Rudra's own is always Python — `pytest`, `ruff`, 3.12/3.13. The *target project's* is detected. CI is a Python CI and stays one.
- **`src/rudra/stacks/` never executes a subprocess.** It reads files and returns data. Command execution belongs to `C3.6` in Step 8.
- **No prose→stack inference table.** Detection reads marker files off disk. The existing table at `main_agent.py:505-512` is owned by `C6.8a` — do not touch or duplicate it.
- **Do not test `main_agent.py`'s orchestration loop** (the per-file dispatch, the 3-attempt retry). Step 9 replaces it; those tests would be written to be deleted. Test its *helpers* only.
- **`deepagents` stays pinned `==0.7.4`** (`pyproject.toml:30`). `src/rudra/compat/` monkeypatches its internals.
- Python floor 3.12 — `tomllib` is stdlib. Do not add `toml`/`tomli`.
- Coverage is **reported, never gated**. No `--fail-under`.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

### Measured baseline (2026-08-07, commit `71e54e0`)

| Command | Output |
|---|---|
| `.venv/bin/pytest -q` | `62 passed` |
| `.venv/bin/ruff check src/ tests/` | `All checks passed!` |
| `.venv/bin/ruff format --check src/ tests/` | `31 files already formatted` |
| `.venv/bin/rudra --version` | `Rudra v0.2.0` |
| coverage, `src/*` | **47%** overall; `compat/deepagents_path.py` and `cli.py` never imported by any test |
| suite on Python 3.13 | `62 passed` (`uv run --python 3.13 --isolated`) |

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/rudra/stacks/__init__.py` | **Create** | Public API: `detect`, `resolve_test_command`, `StackProfile`, `ALL_SKIP_DIRS` |
| `src/rudra/stacks/profile.py` | **Create** | `StackProfile` — frozen dataclass, pure data, no behaviour |
| `src/rudra/stacks/registry.py` | **Create** | The five built-in profiles + `ALL_SKIP_DIRS` |
| `src/rudra/stacks/detect.py` | **Create** | `detect()` and `resolve_test_command()` — filesystem reads only |
| `src/rudra/filesystem/tree.py:34` | Modify | `_ALWAYS_SKIP_DIRS` sources build dirs from the registry (single source of truth) |
| `src/rudra/tools/planning_tools.py` | Modify | De-Python-ise prompt examples (7 locations) |
| `src/rudra/agent/planner_agent.py` | Modify | Same (3 locations) |
| `src/rudra/agent/coder_agent.py` | Modify | Same (1 location) |
| `.github/workflows/ci.yml` | **Create** | ruff + pytest on 3.12/3.13 |
| `tests/test_stacks.py` | **Create** | Detection + command resolution |
| `tests/test_deepagents_path.py` | **Create** | The 0%-covered monkeypatch |
| `tests/test_main_agent_helpers.py` | **Create** | Stable helpers only |
| `tests/test_cli_smoke.py` | **Create** | Import + `--help` |
| `TODO.md` | Modify | Ledger |

---

## Task 1: Log new findings before fixing anything

Fixes nothing. Exists because `CLAUDE.md` §2 rule 2 forbids fixing what is not first recorded.

**Files:** Modify `TODO.md`

**Interfaces:**
- Consumes: nothing
- Produces: ledger IDs later tasks close. **Record the IDs you allocate in your commit message** — later tasks refer to them by role. Highest existing: `A1.25`, `A2.16`, `A5.2`, `D17`.

- [ ] **Step 1: Verify the environment**

```bash
uv sync
.venv/bin/pytest -q
```
Expected: `62 passed`. If `.venv` is missing, `uv sync` recreates it.

- [ ] **Step 2: Reproduce each finding before recording it**

```bash
# F1 — tree.py skip set omits every non-Python build dir
grep -n "_ALWAYS_SKIP_DIRS" src/rudra/filesystem/tree.py

# F2 — prompt filename examples are uniformly .py
grep -n "\.py" src/rudra/tools/planning_tools.py src/rudra/agent/planner_agent.py src/rudra/agent/coder_agent.py

# F3 — deepagents_path.py's docstring premise is false under 0.7.4
.venv/bin/python -c "
from deepagents.backends.utils import validate_path
print(repr(validate_path('/abs/x.py')))
print(repr(validate_path('main.py')))"
```

F3's expected output is `'/abs/x.py'` and `'/main.py'` — i.e. `validate_path` **does not raise** on absolute paths, contradicting `src/rudra/compat/deepagents_path.py:5-6` which states it "rejects absolute paths with a hard ValueError". Record what you actually observe.

- [ ] **Step 3: Add the locked decision**

Add to the Section 0 decisions table, following its existing format:

```markdown
| D18 | **Multi-language targets are first-class: Python, Rust, Node, React/Next.js, Angular** | Rudra's *own* toolchain stays Python (`pytest`, `ruff`, 3.12/3.13) regardless of what the user's project is written in — the two must never be conflated. Greenfield and brownfield are both supported and the choice is the user's; Rudra never requires a scaffold step, a config file, or a particular layout before it will work. Frontend completion gate is **unit tests only** — no build step, no e2e, no browser automation. Design: `docs/superpowers/specs/2026-08-07-multi-language-targets-design.md` |
```

- [ ] **Step 4: Add the defect rows**

Allocate the next free IDs in the appropriate Section A tables. Use the real line numbers you observed in Step 2.

```markdown
| <F1> | PENDING | **`_ALWAYS_SKIP_DIRS` omits every build-output directory of the non-Python targets.** `tree.py:34` lists `.git`, `.rudra`, `__pycache__`, `node_modules`, `.venv`, `venv` — missing `target/` (Cargo), `.next/`, `out/` (Next.js), `dist/`, `build/` (JS toolchains), `.angular/`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`. The set is applied to **both** listing paths (`tree.py:81`), so this is not fallback-only; on the git path it is largely masked because `--exclude-standard` already drops conventionally-gitignored build dirs, and on the walk path it is load-bearing — the walk runs in a non-git directory, and per the A1.19 note at `tree.py:58-63` also when git lists nothing because the root is itself gitignored. Harm is crowding, not noise: `max_entries=300` (`tree.py:44`) gets filled with build artefacts, truncating away the source the planner needs | `src/rudra/filesystem/tree.py:34,44,81` |
| <F2> | PENDING | **Prompt filename examples are uniformly Python**, steering the model toward `.py` for every task including "build a Rust CLI". 11 locations: `planning_tools.py:12-14,60-62,68-69,72-73,113,160,178`; `planner_agent.py:46,53,61`; `coder_agent.py:28`. Prompt-only — the validation logic is already language-agnostic (`looks_like_path` accepts `Cargo.toml`, `package.json`, `angular.json`, `src/main.rs`, verified by execution) | the 11 locations above |
| <F3> | PENDING | **`compat/deepagents_path.py`'s stated rationale is false under 0.7.4.** Its docstring (`:5-6`) says deepagents' `validate_path` "rejects absolute paths with a hard ValueError". Measured on the pinned 0.7.4: `validate_path('/abs/x.py')` returns `'/abs/x.py'` and `validate_path('main.py')` returns `'/main.py'` — it raises nothing and prepends `/` (virtual-root semantics). The module still does useful work (stripping real-machine and sandbox prefixes so the virtual path is correct), so this is a **documentation** defect, not dead code. Do not delete the module on the strength of this row; correct the docstring and re-derive what it is actually for | `src/rudra/compat/deepagents_path.py:5-6`; probe output recorded in this row |
```

- [ ] **Step 5: Add the new `C11` work section**

The existing `C` sections are phase-scoped and none owns stack detection. Add a new section following their format:

```markdown
| C11.1 | PENDING | Build `src/rudra/stacks/` — `StackProfile`, registry, `detect()`, `resolve_test_command()`. Reads files and returns data; **never executes a subprocess** (execution is C3.6's, in Step 8) |
| C11.2 | PENDING | Acceptance matrix: one end-to-end case per stack (Rust, Python, Node, React, Angular), greenfield and brownfield, replacing §0.5's single Rust example. Needs a live model backend — cannot run until one is available |
| C11.3 | PENDING | Angular unit tests require a headless browser (Angular CLI's default builder runs Karma against Chrome). Detect its absence and fail fast with a message naming the missing dependency, rather than hanging on a browser that never launches — inside Step 9's fix loop a hang stalls the loop instead of failing a round |
```

- [ ] **Step 6: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): log D18 and the Step-4 findings before fixing them

D18 locks multi-language targets as first-class and records the two
constraints that shape the design: Rudra's own toolchain stays Python
regardless of target, and the frontend gate is unit tests only.

Three defects logged PENDING per CLAUDE.md rule 2: tree.py's skip set
omits every non-Python build dir, the prompt filename examples are
uniformly .py across 11 locations, and deepagents_path.py's docstring
premise is false under 0.7.4 (validate_path does not raise on absolute
paths; measured).

New C11 section for stack detection — no existing phase-scoped C section
owns it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: `StackProfile` and the registry

Closes part of `C11.1`.

**Files:**
- Create: `src/rudra/stacks/__init__.py`, `src/rudra/stacks/profile.py`, `src/rudra/stacks/registry.py`
- Test: `tests/test_stacks.py`

**Interfaces:**
- Consumes: nothing
- Produces, relied on by Tasks 3 and 4:
  - `StackProfile` — frozen dataclass with fields `name: str`, `markers: tuple[str, ...]`, `requires: tuple[str, ...]`, `dependency: str | None`, `test_command: tuple[str, ...] | None`, `skip_dirs: frozenset[str]`, `specificity: int`
  - `PROFILES: tuple[StackProfile, ...]`
  - `ALL_SKIP_DIRS: frozenset[str]` — union of every profile's `skip_dirs`

- [ ] **Step 1: Write the failing test**

Create `tests/test_stacks.py`:

```python
"""Tests for rudra.stacks — deterministic target-stack detection (C11.1).

Rudra's own toolchain is always Python. This module is about the *user's*
project, which may be Python, Rust, Node, React or Angular (TODO.md D18).

Detection reads marker files off disk. It never infers a stack from prose --
that is the model's job for greenfield work, per TODO.md §0.5 -- and it never
executes a subprocess; running the commands it reports belongs to C3.6.
"""

from __future__ import annotations

from rudra.stacks import ALL_SKIP_DIRS, PROFILES, StackProfile


def test_profile_is_immutable():
    """Profiles are shared module-level constants; mutation would leak globally."""
    import dataclasses
    import pytest

    profile = PROFILES[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        profile.name = "mutated"


def test_every_target_stack_has_a_profile():
    assert {p.name for p in PROFILES} == {"python", "rust", "node", "react", "angular"}


def test_profile_names_are_unique():
    names = [p.name for p in PROFILES]
    assert len(names) == len(set(names))


def test_node_stacks_defer_their_test_command_to_the_project():
    """package.json's scripts.test is the project's own declared answer.

    Guessing between jest and vitest from devDependencies would be inference
    where an observation is available.
    """
    for name in ("node", "react", "angular"):
        profile = next(p for p in PROFILES if p.name == name)
        assert profile.test_command is None, f"{name} must read scripts.test, not hardcode"


def test_non_node_stacks_declare_their_test_command():
    commands = {p.name: p.test_command for p in PROFILES if p.test_command is not None}
    assert commands == {"python": ("pytest",), "rust": ("cargo", "test")}


def test_more_specific_stacks_outrank_generic_node():
    node = next(p for p in PROFILES if p.name == "node")
    for name in ("react", "angular"):
        specific = next(p for p in PROFILES if p.name == name)
        assert specific.specificity > node.specificity


def test_all_skip_dirs_covers_every_targets_build_output():
    for expected in ("target", ".next", "out", "dist", "build", ".angular",
                     "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"):
        assert expected in ALL_SKIP_DIRS


def test_all_skip_dirs_is_the_union_of_the_profiles():
    union = frozenset().union(*(p.skip_dirs for p in PROFILES))
    assert ALL_SKIP_DIRS == union
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/pytest tests/test_stacks.py -v`
Expected: collection fails, `ModuleNotFoundError: No module named 'rudra.stacks'`.

- [ ] **Step 3: Write `src/rudra/stacks/profile.py`**

```python
"""The StackProfile record — pure data, no behaviour, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StackProfile:
    """How to recognise one target stack, and how its tests are run.

    Attributes:
        name: Stable identifier, e.g. "rust".
        markers: Filenames whose presence makes this stack a candidate.
            ANY one is enough.
        requires: Filenames that must ALL also be present. Used where a
            marker alone is ambiguous -- angular.json only means Angular in a
            project that also has package.json.
        dependency: A package.json dependency that must be declared. Used
            where the marker files cannot distinguish, e.g. React vs any
            other Vite project.
        test_command: The command as an argv tuple, or None when the project
            declares its own (Node's package.json scripts.test).
        skip_dirs: Build-output directories that must never appear in the
            model's view of the project.
        specificity: Higher wins when several profiles match. Angular beats
            plain Node in an Angular workspace.
    """

    name: str
    markers: tuple[str, ...]
    requires: tuple[str, ...] = ()
    dependency: str | None = None
    test_command: tuple[str, ...] | None = None
    skip_dirs: frozenset[str] = field(default_factory=frozenset)
    specificity: int = 10
```

- [ ] **Step 4: Write `src/rudra/stacks/registry.py`**

```python
"""The built-in stack profiles.

Adding a language is a data change here, not a design change. Deliberately
absent: Go, Java, Ruby, C# -- not requested (TODO.md D18).
"""

from __future__ import annotations

from rudra.stacks.profile import StackProfile

PYTHON = StackProfile(
    name="python",
    markers=("pyproject.toml", "setup.py", "requirements.txt"),
    test_command=("pytest",),
    skip_dirs=frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
                         ".venv", "venv", ".tox", "build", "dist"}),
    specificity=10,
)

RUST = StackProfile(
    name="rust",
    markers=("Cargo.toml",),
    test_command=("cargo", "test"),
    skip_dirs=frozenset({"target"}),
    specificity=10,
)

NODE = StackProfile(
    name="node",
    markers=("package.json",),
    test_command=None,  # read from scripts.test
    skip_dirs=frozenset({"node_modules", "dist", "build", "coverage"}),
    specificity=10,
)

REACT = StackProfile(
    name="react",
    markers=("package.json",),
    dependency="react",
    test_command=None,
    skip_dirs=frozenset({"node_modules", "dist", "build", ".next", "out", "coverage"}),
    specificity=20,
)

ANGULAR = StackProfile(
    name="angular",
    markers=("angular.json",),
    requires=("package.json",),
    test_command=None,
    skip_dirs=frozenset({"node_modules", "dist", ".angular", "coverage"}),
    specificity=30,
)

PROFILES: tuple[StackProfile, ...] = (PYTHON, RUST, NODE, REACT, ANGULAR)

ALL_SKIP_DIRS: frozenset[str] = frozenset().union(*(p.skip_dirs for p in PROFILES))
```

- [ ] **Step 5: Write `src/rudra/stacks/__init__.py`**

```python
"""Target-stack detection for the user's project (TODO.md D18, C11.1).

Rudra's own toolchain is always Python. This package is about whatever the
user's project is written in.
"""

from __future__ import annotations

from rudra.stacks.profile import StackProfile
from rudra.stacks.registry import ALL_SKIP_DIRS, PROFILES

__all__ = ["ALL_SKIP_DIRS", "PROFILES", "StackProfile"]
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_stacks.py -v`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/stacks/ tests/test_stacks.py
git add src/rudra/stacks/ tests/test_stacks.py
git commit -m "feat(stacks): add StackProfile and the built-in registry

Five target stacks per D18: python, rust, node, react, angular. Node-family
profiles carry test_command=None because package.json's scripts.test is the
project's own declared answer -- guessing between jest and vitest from
devDependencies would be inference where an observation is available.

Pure data. No I/O, no subprocess.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: `detect()` and `resolve_test_command()`

Completes `C11.1`.

**Files:**
- Create: `src/rudra/stacks/detect.py`
- Modify: `src/rudra/stacks/__init__.py` (export the two functions)
- Test: `tests/test_stacks.py` (append)

**Interfaces:**
- Consumes: `StackProfile`, `PROFILES` from Task 2
- Produces:
  - `detect(project_path: Path) -> list[StackProfile]` — most-specific-first; `[]` when nothing matches
  - `resolve_test_command(project_path: Path, profile: StackProfile) -> list[str] | None` — `None` means the project declares no test command

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stacks.py`:

```python
import json
from pathlib import Path

from rudra.stacks import detect, resolve_test_command


def _write(root: Path, rel: str, content: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _package_json(root: Path, *, deps: dict | None = None, test: str | None = None) -> None:
    body: dict = {"name": "app"}
    if deps is not None:
        body["dependencies"] = deps
    if test is not None:
        body["scripts"] = {"test": test}
    _write(root, "package.json", json.dumps(body))


def test_empty_directory_detects_nothing(tmp_path: Path):
    """Greenfield: the model picks the stack and writes the marker file."""
    assert detect(tmp_path) == []


def test_cargo_toml_detects_rust(tmp_path: Path):
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    assert [p.name for p in detect(tmp_path)] == ["rust"]


def test_pyproject_detects_python(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'app'\n")
    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_requirements_txt_alone_detects_python(tmp_path: Path):
    """markers are ANY-of: one Python marker is enough."""
    _write(tmp_path, "requirements.txt", "flask\n")
    assert [p.name for p in detect(tmp_path)] == ["python"]


def test_package_json_alone_detects_node(tmp_path: Path):
    _package_json(tmp_path, test="jest")
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_angular_outranks_node_and_both_are_reported(tmp_path: Path):
    _package_json(tmp_path, test="ng test")
    _write(tmp_path, "angular.json", "{}")
    assert [p.name for p in detect(tmp_path)] == ["angular", "node"]


def test_react_needs_the_dependency_not_just_package_json(tmp_path: Path):
    _package_json(tmp_path, deps={"vue": "^3.0.0"}, test="vitest run")
    assert [p.name for p in detect(tmp_path)] == ["node"]

    _package_json(tmp_path, deps={"react": "^18.0.0"}, test="vitest run")
    assert [p.name for p in detect(tmp_path)] == ["react", "node"]


def test_tauri_style_project_reports_both_stacks(tmp_path: Path):
    """A repo genuinely can be two stacks. Forcing one answer would be wrong."""
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    _package_json(tmp_path, test="vitest run")
    assert sorted(p.name for p in detect(tmp_path)) == ["node", "rust"]


def test_angular_json_without_package_json_is_not_angular(tmp_path: Path):
    """`requires` is ALL-of: angular.json alone is not an Angular workspace."""
    _write(tmp_path, "angular.json", "{}")
    assert detect(tmp_path) == []


def test_malformed_package_json_does_not_crash_detection(tmp_path: Path):
    """A half-written package.json is normal mid-task. Degrade, don't raise."""
    _write(tmp_path, "package.json", "{ not valid json")
    assert [p.name for p in detect(tmp_path)] == ["node"]


def test_missing_directory_detects_nothing(tmp_path: Path):
    assert detect(tmp_path / "does-not-exist") == []


def test_resolve_test_command_returns_the_declared_command(tmp_path: Path):
    _write(tmp_path, "Cargo.toml", '[package]\nname = "app"\n')
    rust = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, rust) == ["cargo", "test"]


def test_resolve_test_command_reads_scripts_test_for_node(tmp_path: Path):
    _package_json(tmp_path, test="vitest run")
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) == ["npm", "test"]


def test_resolve_test_command_is_none_when_node_declares_no_test(tmp_path: Path):
    """Reported as absent, never fabricated. A missing gate is actionable;
    an invented one is not."""
    _package_json(tmp_path)
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None


def test_resolve_test_command_is_none_when_package_json_is_malformed(tmp_path: Path):
    _write(tmp_path, "package.json", "{ not valid json")
    node = detect(tmp_path)[0]
    assert resolve_test_command(tmp_path, node) is None
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_stacks.py -v`
Expected: `ImportError: cannot import name 'detect' from 'rudra.stacks'`.

- [ ] **Step 3: Write `src/rudra/stacks/detect.py`**

```python
"""Detection: read marker files off disk and report the target stacks.

This module observes. It does not infer a stack from prose (that is the
model's job for greenfield work, TODO.md §0.5) and it does not execute
anything (that is C3.6's job, Step 8).
"""

from __future__ import annotations

import json
from pathlib import Path

from rudra.stacks.profile import StackProfile
from rudra.stacks.registry import PROFILES


def _load_package_json(project_path: Path) -> dict:
    """package.json as a dict, or {} when absent or unparseable.

    A half-written package.json is a normal intermediate state while an agent
    is working. Degrading to {} keeps detection usable; raising would abort a
    run over a file the agent is about to finish writing.
    """
    path = project_path / "package.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _declares_dependency(package_json: dict, name: str) -> bool:
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = package_json.get(section)
        if isinstance(block, dict) and name in block:
            return True
    return False


def _matches(project_path: Path, profile: StackProfile, package_json: dict) -> bool:
    if not any((project_path / marker).is_file() for marker in profile.markers):
        return False
    if not all((project_path / required).is_file() for required in profile.requires):
        return False
    if profile.dependency is not None:
        return _declares_dependency(package_json, profile.dependency)
    return True


def detect(project_path: Path) -> list[StackProfile]:
    """Target stacks present in `project_path`, most specific first.

    Returns a list, not one profile: a Tauri app genuinely is both Rust and
    Node, and monorepos are ordinary. Returning a single "the" stack would
    force a wrong answer, and D18 forbids Rudra forcing the user's hand.

    An empty list means greenfield -- no marker files yet.
    """
    project_path = Path(project_path)
    if not project_path.is_dir():
        return []

    package_json = _load_package_json(project_path)
    matched = [p for p in PROFILES if _matches(project_path, p, package_json)]
    return sorted(matched, key=lambda p: (-p.specificity, p.name))


def resolve_test_command(project_path: Path, profile: StackProfile) -> list[str] | None:
    """The argv for this stack's tests, or None if the project declares none.

    None is a real answer, not a failure: the fix loop can act on "this
    project has no test command". Inventing one would give it a gate that
    does not correspond to anything.
    """
    if profile.test_command is not None:
        return list(profile.test_command)

    scripts = _load_package_json(Path(project_path)).get("scripts")
    if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
        return ["npm", "test"]
    return None
```

- [ ] **Step 4: Export from `src/rudra/stacks/__init__.py`**

Replace the file:

```python
"""Target-stack detection for the user's project (TODO.md D18, C11.1).

Rudra's own toolchain is always Python. This package is about whatever the
user's project is written in.
"""

from __future__ import annotations

from rudra.stacks.detect import detect, resolve_test_command
from rudra.stacks.profile import StackProfile
from rudra.stacks.registry import ALL_SKIP_DIRS, PROFILES

__all__ = [
    "ALL_SKIP_DIRS",
    "PROFILES",
    "StackProfile",
    "detect",
    "resolve_test_command",
]
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_stacks.py -v`
Expected: 23 passed (8 from Task 2 + 15 new).

- [ ] **Step 6: Prove the no-subprocess constraint holds**

```bash
grep -rn "subprocess\|os.system\|popen\|Popen" src/rudra/stacks/
```
Expected: **no output.** A hit means the module executes something, violating a global constraint.

- [ ] **Step 7: Commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/rudra/stacks/ tests/test_stacks.py
.venv/bin/pytest -q
git add src/rudra/stacks/ tests/test_stacks.py
git commit -m "feat(stacks): add detect() and resolve_test_command()

detect() returns a list, most-specific-first: a Tauri app genuinely is both
Rust and Node, and monorepos are ordinary, so forcing a single answer would
be wrong. An empty list means greenfield -- no markers yet, so the model
chooses and writes one.

resolve_test_command() reads package.json's scripts.test for Node stacks and
returns None when none is declared. None is a real answer the fix loop can
act on; a fabricated command would be a gate corresponding to nothing.

Malformed package.json degrades to {} rather than raising -- a half-written
manifest is a normal intermediate state while an agent works.

Closes C11.1.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Wire the build-output skip dirs into `project_tree`

Closes finding `<F1>`.

**Files:**
- Modify: `src/rudra/filesystem/tree.py:34`
- Test: `tests/test_project_tree.py` (append)

**Interfaces:**
- Consumes: `ALL_SKIP_DIRS` from Task 2
- Produces: nothing new

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project_tree.py`. It already has `_write` and `_init_repo` helpers — reuse them, do not redefine.

```python
def test_build_output_dirs_are_never_listed(tmp_path: Path) -> None:
    """F1: without this, a Rust target/ fills the 300-entry budget and
    truncates away the source the planner needs to see."""
    _write(tmp_path, "Cargo.toml", "[package]")
    _write(tmp_path, "src/main.rs", "fn main() {}")
    _write(tmp_path, "target/debug/app", "binary")
    _write(tmp_path, "target/debug/deps/libfoo.rlib", "x")

    tree = project_tree(tmp_path)

    assert "src/main.rs" in tree
    assert "Cargo.toml" in tree
    assert "target" not in tree


def test_frontend_build_dirs_are_never_listed(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", "{}")
    _write(tmp_path, "src/app/app.component.ts", "export class App {}")
    _write(tmp_path, ".next/cache/x", "x")
    _write(tmp_path, "dist/main.js", "x")
    _write(tmp_path, ".angular/cache/y", "y")

    tree = project_tree(tmp_path)

    assert "src/app/app.component.ts" in tree
    assert ".next" not in tree
    assert "dist/" not in tree
    assert ".angular" not in tree


def test_skip_dirs_are_sourced_from_the_stack_registry() -> None:
    """Single source of truth: adding a stack must not require editing tree.py."""
    from rudra.filesystem.tree import _ALWAYS_SKIP_DIRS
    from rudra.stacks import ALL_SKIP_DIRS

    assert ALL_SKIP_DIRS <= _ALWAYS_SKIP_DIRS
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/pytest tests/test_project_tree.py -v -k "build_output or frontend_build or registry"`
Expected: all three FAIL — `target`, `.next`, `dist`, `.angular` currently appear in the tree.

- [ ] **Step 3: Source the skip set from the registry**

In `src/rudra/filesystem/tree.py`, add to the imports:

```python
from rudra.stacks import ALL_SKIP_DIRS
```

Then replace `:31-34`:

```python
# Applied on BOTH listing paths, so they hold even in a repo that tracks
# them. `.rudra` is Rudra's own state directory and must never appear in
# the model's view of the project. Build-output directories come from the
# stack registry so adding a language never requires editing this file
# (TODO.md D18, C11.1).
_RUDRA_SKIP_DIRS = frozenset({".git", ".rudra"})
_ALWAYS_SKIP_DIRS = _RUDRA_SKIP_DIRS | ALL_SKIP_DIRS
```

`node_modules`, `__pycache__`, `.venv` and `venv` are all already in `ALL_SKIP_DIRS` via the Python and Node profiles, so nothing is lost by dropping them from the literal — but verify that with Step 4 rather than assuming it.

- [ ] **Step 4: Confirm nothing was lost**

```bash
.venv/bin/python -c "
from rudra.filesystem.tree import _ALWAYS_SKIP_DIRS as s
for d in ('.git','.rudra','__pycache__','node_modules','.venv','venv',
          'target','.next','out','dist','build','.angular',
          '.pytest_cache','.ruff_cache','.mypy_cache'):
    assert d in s, d
print(sorted(s))"
```
Expected: no assertion error, and the sorted set printed.

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/pytest tests/test_project_tree.py -v
.venv/bin/pytest -q
```
Expected: `test_project_tree.py` all pass (9 existing + 3 new = 12); full suite `88 passed` (62 + 23 stacks + 3 tree).

- [ ] **Step 6: Check for an import cycle**

```bash
.venv/bin/python -c "import rudra.filesystem.tree; import rudra.stacks; print('ok')"
```
Expected: `ok`. `rudra.stacks` imports nothing from `rudra.filesystem`, so this is acyclic — confirm rather than assume.

- [ ] **Step 7: Mark `<F1>` DONE and commit**

```bash
git add src/rudra/filesystem/tree.py tests/test_project_tree.py TODO.md
git commit -m "fix(tree): exclude every target stack's build output

_ALWAYS_SKIP_DIRS listed only Python and Node basics, so a Rust target/ or an
Angular .angular/ was listed in full. The harm is crowding rather than noise:
project_tree caps at 300 entries, so build artefacts truncated away the source
the planner needs.

The build-output half now comes from the stack registry, so adding a language
is a data change in one place rather than an edit here too.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Cover `compat/deepagents_path.py`

Closes finding `<F3>`. This module is **0% covered** — never imported by any test — and `CLAUDE.md` §7 calls its monkeypatch the most fragile thing in the repo.

**Files:**
- Create: `tests/test_deepagents_path.py`
- Modify: `src/rudra/compat/deepagents_path.py:1-25` (docstring only)

**Interfaces:**
- Consumes: `install_path_normalizer(project_root: Path, plan_path: Path | None = None) -> None`
- Produces: nothing new

- [ ] **Step 1: Understand the global side effect before writing anything**

`install_path_normalizer` rewrites `deepagents.backends.utils.validate_path` **and** `deepagents.middleware.filesystem.validate_path` — process-global. It stashes the original under the attribute name `_rudra_original_validate_path` on `deepagents.backends.utils` (`deepagents_path.py:34,86-89`). Tests **must** restore both, or every later test in the session runs against a patched deepagents.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_deepagents_path.py`. Every expected value below was **measured against the pinned deepagents 0.7.4**, not predicted.

```python
"""Tests for the deepagents path-normalizer monkeypatch (TODO.md U.4, F3).

This module was at 0% coverage -- never imported by any test -- while being
the most fragile thing in the repo: it rewrites a deepagents internal in two
places and will break on any version bump.

All expected values here were measured against the pinned deepagents 0.7.4.
Note what that measurement showed: the upstream validate_path does NOT reject
absolute paths (it returns them unchanged and prepends "/" to relative ones,
virtual-root semantics). The module's real job is stripping real-machine and
sandbox prefixes so the resulting virtual path is correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.compat.deepagents_path import install_path_normalizer

_ORIGINAL_KEY = "_rudra_original_validate_path"


@pytest.fixture
def normalizer(tmp_path: Path):
    """Install the patch, then fully restore deepagents afterwards.

    Without the restore, the patch leaks into every later test in the session
    -- it rewrites module-level names in two deepagents modules.
    """
    import deepagents.backends.utils as utils
    import deepagents.middleware.filesystem as fs_mw

    saved_utils = utils.validate_path
    saved_fs_mw = fs_mw.validate_path

    def _install(plan_lines: str | None = None):
        plan_path = None
        if plan_lines is not None:
            plan_path = tmp_path / ".rudra" / "PLAN.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_lines, encoding="utf-8")
        install_path_normalizer(tmp_path.resolve(), plan_path=plan_path)
        return utils.validate_path

    yield _install

    utils.validate_path = saved_utils
    fs_mw.validate_path = saved_fs_mw
    if hasattr(utils, _ORIGINAL_KEY):
        delattr(utils, _ORIGINAL_KEY)


def test_relative_paths_pass_through(normalizer):
    validate = normalizer()
    assert validate("main.py") == "/main.py"


def test_both_modules_are_patched(normalizer):
    """FilesystemMiddleware does `from ... import validate_path`, so patching
    only backends.utils leaves the middleware's copy untouched (CLAUDE.md §7)."""
    import deepagents.backends.utils as utils
    import deepagents.middleware.filesystem as fs_mw

    normalizer()
    assert utils.validate_path is fs_mw.validate_path


def test_project_root_prefix_is_stripped(normalizer, tmp_path: Path):
    validate = normalizer()
    absolute = str(tmp_path.resolve() / "src" / "models.py")
    assert validate(absolute) == "/src/models.py"


def test_windows_absolute_path_is_stripped(normalizer):
    validate = normalizer()
    assert validate("C:\\project\\models.py") == "/project/models.py"


@pytest.mark.parametrize(
    ("hallucinated", "expected"),
    [
        ("/testbed/app/main.py", "/app/main.py"),
        ("/workspace/src/main.rs", "/src/main.rs"),
    ],
)
def test_sandbox_prefixes_are_stripped(normalizer, hallucinated, expected):
    """LLMs trained on SWE-bench and Codespaces emit these constantly."""
    validate = normalizer()
    assert validate(hallucinated) == expected


def test_plan_aware_suffix_match_resolves_an_unknown_prefix(normalizer):
    validate = normalizer("- [ ] app/database.py\n")
    assert validate("/unknown/prefix/app/database.py") == "/app/database.py"


def test_plan_matching_is_language_agnostic(normalizer):
    """D18: the planner's checklist is filenames, whatever the stack."""
    validate = normalizer("- [ ] src/main.rs\n- [x] Cargo.toml\n")
    assert validate("/home/user/repos/myproj/Cargo.toml") == "/Cargo.toml"


def test_unknown_deep_path_keeps_only_the_last_two_segments(normalizer):
    """Last resort: avoid materialising deep hallucinated directory trees."""
    validate = normalizer()
    assert validate("/a/b/c/d/e.py") == "/d/e.py"


def test_install_is_idempotent(normalizer, tmp_path: Path):
    """create_main_agent may be called more than once per process; re-wrapping
    would stack normalizers."""
    import deepagents.backends.utils as utils

    normalizer()
    first_original = getattr(utils, _ORIGINAL_KEY)
    normalizer()
    assert getattr(utils, _ORIGINAL_KEY) is first_original
    assert utils.validate_path("main.py") == "/main.py"
```

- [ ] **Step 3: Run and confirm they fail**

Run: `.venv/bin/pytest tests/test_deepagents_path.py -v`

Expected: they **fail on import** only if you mistyped a name — otherwise most will *pass immediately*, because the implementation already exists. That is expected and correct here: this task is adding a characterization net over untested existing code, not driving new behaviour. **Record which tests pass on first run in your report.** If any test fails, that is a genuine discovery about the module — investigate and report it before changing anything.

- [ ] **Step 4: Correct the false docstring**

Replace `src/rudra/compat/deepagents_path.py:3-15` (the `WHY THIS EXISTS` block) with:

```python
WHY THIS EXISTS
--------------
Some LLMs ignore system-prompt instructions and emit absolute paths regardless
of the OS — Windows-style (``C:\\project\\models.py``), POSIX-style
(``/home/user/project/models.py``), or hallucinated sandbox prefixes from
training data (``/testbed/app/main.py``).  Left alone these resolve to the
wrong place in the backend's virtual filesystem.

Measured against the pinned deepagents 0.7.4, upstream ``validate_path`` does
NOT reject absolute paths: ``validate_path("/abs/x.py")`` returns
``"/abs/x.py"`` unchanged and ``validate_path("main.py")`` returns
``"/main.py"`` — it prepends ``/``, virtual-root semantics.  An earlier
revision of this docstring claimed it "rejects absolute paths with a hard
ValueError"; that was false for 0.7.4 (TODO.md F3).  This shim's real job is
to strip real-machine and sandbox prefixes so the resulting virtual path
points at the intended file.

``validate_path`` is only called inside ``deepagents.middleware.filesystem``
(confirmed by grep across the full deepagents package).  We patch the
module-level name there because ``FilesystemMiddleware`` uses
``from deepagents.backends.utils import validate_path``, so tool closures
look up the name in that module's globals at call time.
```

Use the real finding ID Task 1 allocated in place of `F3`.

- [ ] **Step 5: Run the suite and confirm no leakage**

```bash
.venv/bin/pytest tests/test_deepagents_path.py -v
.venv/bin/pytest -q
.venv/bin/pytest -q tests/test_deepagents_path.py tests/test_deepagents_contract.py
```
Expected: 10 passed in the new file; full suite `98 passed` (88 + 10). The third command specifically checks the patch does not leak into the contract tests, which exercise real deepagents behaviour and would break if left patched.

- [ ] **Step 6: Measure the coverage change**

```bash
uvx --with-editable . --with pytest coverage run -m pytest -q
uvx coverage report --include="src/rudra/compat/*"
```
Expected: `deepagents_path.py` appears with substantial coverage where it previously did not appear at all. Record the number.

- [ ] **Step 7: Mark `<F3>` DONE and commit**

```bash
git add tests/test_deepagents_path.py src/rudra/compat/deepagents_path.py TODO.md
git commit -m "test(compat): characterize the deepagents path normalizer

This module was at 0% coverage while rewriting a deepagents internal in two
places -- the thing CLAUDE.md §7 flags as most likely to break on a version
bump. Ten characterization tests now pin its real behaviour, each expectation
measured against the pinned 0.7.4 rather than predicted.

The fixture restores both patched module attributes; without that the patch
leaks into every later test in the session.

Also corrects the docstring's false premise (F3): upstream validate_path does
not reject absolute paths, it returns them unchanged and prepends / to
relative ones. The module's real job is stripping real-machine and sandbox
prefixes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Cover `main_agent`'s stable helpers and the CLI

Contributes to `C0.5`.

**Files:**
- Create: `tests/test_main_agent_helpers.py`, `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: `_check_off_file(plan_path: Path, filename: str) -> None`, `_ensure_agents_md(rudra_dir: Path, project_context: Optional[ProjectContext]) -> None`, `_write_tech_stack_file(rudra_dir: Path, project_context: Optional[ProjectContext]) -> str`
- Produces: nothing new

**Scope discipline — read before writing.** Cover only the helpers listed above. Do **not** test `RudraAgent.run()`, the per-file dispatch, or the 3-attempt retry: Step 9 replaces all of it, and those tests would be written to be deleted. `_parse_pending_files` is already covered by `tests/test_plan_items.py` — do not duplicate it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_agent_helpers.py`:

```python
"""Tests for main_agent's stable helper functions (C0.5).

Deliberately NOT covered: RudraAgent.run(), per-file dispatch, the retry loop.
Step 9 (C6.1-C6.6) replaces the orchestration loop wholesale, so tests against
it would be written to be deleted. These helpers are the seams that survive.

Fixtures span the D18 target stacks so no Python assumption calcifies.
"""

from __future__ import annotations

from pathlib import Path

from rudra.agent.main_agent import (
    _check_off_file,
    _ensure_agents_md,
    _write_tech_stack_file,
)
from rudra.state import ProjectContext


def test_check_off_file_ticks_the_matching_item(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] main.py\n- [ ] models.py\n", encoding="utf-8")

    _check_off_file(plan, "main.py")

    assert plan.read_text(encoding="utf-8") == "- [x] main.py\n- [ ] models.py\n"


def test_check_off_file_works_for_every_target_stack(tmp_path: Path):
    """D18: the checklist is filenames, whatever the language."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        "- [ ] src/main.rs\n- [ ] Cargo.toml\n- [ ] package.json\n"
        "- [ ] src/app/app.component.ts\n",
        encoding="utf-8",
    )

    for name in ("src/main.rs", "Cargo.toml", "package.json", "src/app/app.component.ts"):
        _check_off_file(plan, name)

    assert "- [ ]" not in plan.read_text(encoding="utf-8")


def test_check_off_file_ticks_only_the_first_occurrence(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] a.py\n- [ ] a.py\n", encoding="utf-8")

    _check_off_file(plan, "a.py")

    assert plan.read_text(encoding="utf-8") == "- [x] a.py\n- [ ] a.py\n"


def test_check_off_file_leaves_the_plan_untouched_when_absent(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    original = "- [ ] main.py\n"
    plan.write_text(original, encoding="utf-8")

    _check_off_file(plan, "nonexistent.rs")

    assert plan.read_text(encoding="utf-8") == original


def test_ensure_agents_md_creates_the_file_once(tmp_path: Path):
    _ensure_agents_md(tmp_path, ProjectContext(primary_language="Rust"))
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()

    agents.write_text("hand-edited by the user\n", encoding="utf-8")
    _ensure_agents_md(tmp_path, ProjectContext(primary_language="Rust"))

    assert agents.read_text(encoding="utf-8") == "hand-edited by the user\n"


def test_ensure_agents_md_handles_no_project_context(tmp_path: Path):
    _ensure_agents_md(tmp_path, None)
    assert (tmp_path / "AGENTS.md").is_file()


def test_write_tech_stack_file_writes_and_returns_the_same_content(tmp_path: Path):
    returned = _write_tech_stack_file(tmp_path, ProjectContext(primary_language="Rust"))
    on_disk = (tmp_path / "tech_stack.md").read_text(encoding="utf-8")
    assert returned == on_disk


def test_write_tech_stack_file_records_a_non_python_stack(tmp_path: Path):
    """D18: a Rust project must not be rendered as a Python one."""
    content = _write_tech_stack_file(
        tmp_path, ProjectContext(primary_language="Rust", framework="clap")
    )
    assert "Rust" in content
    assert "clap" in content


def test_write_tech_stack_file_handles_no_project_context(tmp_path: Path):
    content = _write_tech_stack_file(tmp_path, None)
    assert isinstance(content, str)
    assert (tmp_path / "tech_stack.md").is_file()
```

Create `tests/test_cli_smoke.py`:

```python
"""Import and --help smoke tests for the CLI (C0.5).

cli.py was at 0% coverage -- no test imported it. These do not test behaviour;
they catch the import-time breakage that unit tests miss, which is exactly the
failure mode Step 3's config refactor risked (a decorator argument evaluated
at import).
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_module_imports():
    """A broken module-level statement fails here rather than at a user's shell."""
    import rudra.cli

    assert rudra.cli.app is not None


def test_help_exits_zero_and_lists_the_flags():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--verbose" in result.stdout
    assert "--project-dir" in result.stdout


def test_version_reports_the_installed_version():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "0.0.0+unknown" not in result.stdout
```

- [ ] **Step 2: Run the CLI smoke tests**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -v`
Expected: 3 passed.

The `python -m rudra.cli` invocation form was verified while writing this plan — `.venv/bin/python -m rudra.cli --help` exits 0 and its output contains both `--verbose` and `--project-dir`. Use that form; no fallback is needed.

- [ ] **Step 3: Run both new files**

```bash
.venv/bin/pytest tests/test_main_agent_helpers.py tests/test_cli_smoke.py -v
```
Expected: 12 passed (9 helpers + 3 CLI). Any failure is a real finding about the helpers — investigate and report before changing source.

- [ ] **Step 4: Confirm the loop was not tested**

```bash
grep -n "RudraAgent\|\.run()\|MAX_CODER_RETRIES" tests/test_main_agent_helpers.py
```
Expected: **no output.** A hit means the task drifted into loop testing, which the global constraints forbid.

- [ ] **Step 5: Full suite and commit**

```bash
.venv/bin/pytest -q
```
Expected: `110 passed` (98 + 12).

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format tests/
git add tests/test_main_agent_helpers.py tests/test_cli_smoke.py TODO.md
git commit -m "test: cover main_agent's stable helpers and the CLI entry point

cli.py was at 0% -- no test imported it -- so import-time breakage reached
users before it reached the suite. Helper coverage is deliberately limited to
the seams that survive Step 9's loop rewrite; RudraAgent.run() and the retry
loop are left untested on purpose rather than by omission.

Fixtures span Rust, Node and Angular filenames alongside Python so no
single-language assumption calcifies ahead of D18.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: De-Python-ise the prompt examples

Closes finding `<F2>`.

**Files:**
- Modify: `src/rudra/tools/planning_tools.py` (7 locations), `src/rudra/agent/planner_agent.py` (3), `src/rudra/agent/coder_agent.py` (1)

**Interfaces:** Consumes nothing; produces nothing. Prompt text only — no logic changes.

**This task changes only strings the model reads.** If you find yourself editing a condition, a return value, or a function signature, stop: that is out of scope.

- [ ] **Step 1: Confirm the logic needs no change**

```bash
.venv/bin/python -c "
from rudra.tools.planning_tools import looks_like_path
for n in ['Cargo.toml','package.json','angular.json','tsconfig.json','src/main.rs',
          'next.config.js','src/app/app.component.ts','main.py','vite.config.ts']:
    assert looks_like_path(n), n
print('all accepted')"
```
Expected: `all accepted`. This is why the task is prompt-only.

- [ ] **Step 2: Rotate the examples**

At each of the 11 locations, replace the uniformly-Python example set with a mixed one. The goal is that no single language reads as the default. Concretely:

| Location | Change |
|---|---|
| `planning_tools.py:12-14` (module docstring usage block) | `update_plan("- [ ] src/main.rs\n- [ ] Cargo.toml")`, then `write_file(file_path="src/main.rs", ...)`, then the matching `edit_file` tick |
| `planning_tools.py:60-62` (`CORRECT:` examples) | `- [ ] src/main.rs`, `- [ ] package.json`, `- [ ] main.py` |
| `planning_tools.py:68-69` (format block) | `- [ ] <filename>` / `- [x] <filename>` — drop the extension entirely; it is a format illustration, not a language example |
| `planning_tools.py:72-73` (edit_file example) | `old_string = '- [ ] app/routes.ts'`, `new_string = '- [x] app/routes.ts'` |
| `planning_tools.py:113` (rejection message) | `e.g. '- [ ] auth.rs' instead of '- [ ] Set up auth'` |
| `planning_tools.py:160` (no-plan guidance) | `update_plan(plan_markdown='- [ ] src/main.rs\n- [ ] Cargo.toml')` |
| `planning_tools.py:178` (`write_task_assignment` arg doc) | `e.g. "src/main.rs"` |
| `planner_agent.py:46` | `CORRECT: '- [ ] main.rs'    WRONG: '- [ ] Create main.rs'` |
| `planner_agent.py:53` | `e.g. "src/models.ts"` |
| `planner_agent.py:61` | `e.g. "src/main.rs", not absolute paths` |
| `coder_agent.py:28` | `- Use RELATIVE paths only: "src/main.rs", "package.json"` |

Keep at least one Python example somewhere in the set — Python remains a supported target (D18), and removing it entirely would swap one bias for another.

- [ ] **Step 3: Confirm nothing but strings changed**

```bash
git diff --stat
git diff -U0 | grep "^[+-]" | grep -v "^[+-][+-]" | grep -vE '^[+-]\s*[#"]|^[+-]\s*$' | head -40
```
Every changed line must be inside a string literal, a docstring, or a comment. A changed `if`, `return`, or `def` means the task went out of scope.

- [ ] **Step 4: Confirm the behaviour is unchanged**

```bash
.venv/bin/pytest -q
```
Expected: `110 passed` — identical to Task 6. These are prompts; no test outcome may move. If a count changes, a logic line was edited.

- [ ] **Step 5: Mark `<F2>` DONE and commit**

```bash
.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/
git add src/rudra/tools/planning_tools.py src/rudra/agent/planner_agent.py src/rudra/agent/coder_agent.py TODO.md
git commit -m "fix(prompts): stop steering the model toward Python

Every filename example shown to the model across 11 locations was a .py file,
which nudges toward Python on every task including 'build a Rust CLI'. Rotated
to a mixed set spanning Rust, Node, TypeScript and Python.

Prompt text only -- looks_like_path already accepts every target stack's
filenames, verified by execution. The test count is unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: GitHub Actions CI

Closes `C0.6` and `A3.3`.

**Files:** Create `.github/workflows/ci.yml`

**Interfaces:** Consumes nothing; produces the CI contract Stage I's gate depends on.

- [ ] **Step 1: Confirm the matrix passes locally before writing the workflow**

```bash
uv run --python 3.12 --with-editable . --with pytest --isolated pytest -q
uv run --python 3.13 --with-editable . --with pytest --isolated pytest -q
```
Expected: `110 passed` on both. A workflow written against an untested matrix leg is a guess.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync
      # Absolute gate. Step 3 (C0.4) got this to zero; it must stay there.
      - run: uv run ruff check src/ tests/
      - run: uv run ruff format --check src/ tests/

  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      # `uv sync` installs the project itself, which test_package_version.py
      # requires: it compares __version__ against installed distribution
      # metadata, and an uninstalled source tree yields 0.0.0+unknown.
      - run: uv sync --python ${{ matrix.python-version }}
      - run: uv run --python ${{ matrix.python-version }} pytest -q

  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync
      # Reported, never gated. At 47% a gate at today's number is meaningless
      # and one set higher pressures low-value tests against code Steps 5, 6
      # and 9 replace. Revisit after Step 9. No --fail-under.
      - run: uv run --with coverage coverage run -m pytest -q
      - run: uv run --with coverage coverage report --include="src/*"
```

`fail-fast: false` is deliberate: when 3.13 breaks and 3.12 does not, you want to see both results, not have the matrix cancelled.

- [ ] **Step 3: Validate the YAML parses**

```bash
.venv/bin/python -c "
import tomllib, pathlib
try:
    import yaml
except ImportError:
    raise SystemExit('install pyyaml via uvx for this check')
"
uvx --with pyyaml python -c "
import yaml, pathlib
doc = yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())
print(sorted(doc['jobs']))
assert doc['jobs']['test']['strategy']['matrix']['python-version'] == ['3.12','3.13']
assert 'coverage' in doc['jobs']
print('workflow parses')"
```
Expected: `['coverage', 'lint', 'test']` then `workflow parses`.

- [ ] **Step 4: Confirm no coverage gate slipped in**

```bash
grep -n "fail-under" .github/workflows/ci.yml
```
Expected: **no output.** A `--fail-under` contradicts a global constraint.

- [ ] **Step 5: Mark the ledger and commit**

Mark `C0.6` and `A3.3` DONE. Also update the now-stale rows with today's measured reality:

- `A3.1` said `ruff check` → **213 errors**. It is now `All checks passed!` — closed by Step 3's `C0.4`. Mark DONE with that note.
- `A3.2` said `tests/` is empty, zero tests. It is now 110 tests across 12 files. Mark DONE.

```bash
git add .github/workflows/ci.yml TODO.md
git commit -m "ci: add GitHub Actions for ruff and pytest on 3.12/3.13

Three jobs: lint (ruff check + format --check, both absolute gates now that
C0.4 reached zero), test (3.12 and 3.13, fail-fast disabled so a 3.13-only
break is visible alongside a passing 3.12), and coverage (reported, never
gated).

uv sync installs the project itself, which test_package_version.py requires --
it compares against installed distribution metadata, so an uninstalled tree
yields 0.0.0+unknown and fails.

Closes C0.6 and A3.3; A3.1 and A3.2 closed as satisfied by earlier work.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Sign-off

No source changes.

**Files:** Modify `TODO.md`

- [ ] **Step 1: Full local verification**

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
uvx --with-editable . --with pytest coverage run -m pytest -q
uvx coverage report --include="src/*"
```
Expected: `All checks passed!`; all formatted; `110 passed`; `Rudra v0.2.0`; coverage materially above the 47% baseline. Record the new figure.

- [ ] **Step 2: Measure the diff**

```bash
git diff --stat 71e54e0..HEAD -- src/ tests/ .github/
```

- [ ] **Step 3: Write the Section E step 4 completion row**

Follow the format of the step 1-3 rows. It must state:

- Rows closed: `C0.5`, `C0.6`, `A3.1`, `A3.2`, `A3.3`, `C11.1`, plus the three findings from Task 1
- That `C11.2` (acceptance matrix) and `C11.3` (Angular browser check) remain **PENDING** — both need a live model backend and/or shell execution, which arrive in Steps 8-9
- That coverage is reported, not gated, and why — with the revisit trigger (after Step 9)
- That `main_agent.py`'s orchestration loop is deliberately untested, and why
- The measured diff stat, final ruff/pytest output, and the new coverage figure
- **That Step 5 (`C1.1–C1.8, U.9` — provider-agnostic model factory) is unblocked**

Also carry forward, so Step 5 inherits them: `A1.25` (silent plan-item drop reported as success), `A5.2` (`.env` keyed to cwd vs `--project-dir` — should land before the model factory), `A1.24` (accepted risk), and Step 3's un-run live smoke test.

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): close out Step 4 — stacks, regression net, CI

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** — every section maps to a task:

| Spec § | Task |
|---|---|
| §1 two toolchains; supported stacks | Task 2 registry; Task 8 CI is Python-only |
| §2 detection observes, never infers | Task 3 `detect()`; global constraints |
| §3 `StackProfile`, list return, `scripts.test`, no execution | Tasks 2, 3 (Step 6 asserts no subprocess) |
| §4.1 `tree.py` skip dirs | Task 4 |
| §4.2 prompt examples | Task 7 |
| §4.3 downstream consumers (`C3.6`, `C6.6`, `C6.8a`) | Not implemented here by design — Steps 8-10. Interfaces exist for them to consume |
| §5 Angular browser constraint | Logged as `C11.3` in Task 1; implementation needs shell (Step 8) |
| §6 amended Step 4 | Tasks 2-8 |
| §6 new ledger rows | Task 1 |
| §7 out of scope | Honoured — no Go/Java/Ruby profiles, no e2e, no build step in any gate |
| §8 monorepo open question | Left open, recorded in Task 1's `C11.2` |

**Type consistency:** `StackProfile`'s seven fields are defined in Task 2 Step 3 and used with those exact names in Task 2's registry, Task 3's `_matches`/`resolve_test_command`, and `tests/test_stacks.py`. `detect(project_path: Path) -> list[StackProfile]` and `resolve_test_command(project_path: Path, profile: StackProfile) -> list[str] | None` are defined in Task 3 Step 3 and used with those signatures in Task 3's tests and Task 4's `tree.py` import. `ALL_SKIP_DIRS` is defined in Task 2's registry and consumed in Task 4.

**Cumulative test counts** chain: 62 baseline → 85 (Task 3, +23) → 88 (Task 4, +3) → 98 (Task 5, +10) → 110 (Task 6, +12) → unchanged through Tasks 7-9.

**Assertions verified against the running code while writing this plan**, rather than predicted — so no task ships a guessed expectation:

| Claim | Verified |
|---|---|
| `validate_path('/abs/x.py')` → `'/abs/x.py'`; `validate_path('main.py')` → `'/main.py'` | probe against pinned 0.7.4 |
| All eight normalizer expectations in Task 5 | probe with a real `install_path_normalizer` + temp PLAN.md |
| `_ensure_agents_md` creates `AGENTS.md` and **preserves a hand-edit** on re-call | executed |
| `_write_tech_stack_file` writes `tech_stack.md`, returns identical content, renders `Rust` and `clap` | executed |
| Both helpers accept `project_context=None` | executed |
| `python -m rudra.cli --help` exits 0 and lists `--verbose` and `--project-dir` | executed |
| `looks_like_path` accepts all ten Rust/Node/Angular/React fixtures | executed |
| Suite passes on Python 3.13 | `uv run --python 3.13 --isolated` → 62 passed |

**One place the plan deliberately admits uncertainty:** Task 5 Step 3 expects most tests to *pass* on first run, because they characterize existing untested code rather than drive new behaviour. The plan says so explicitly and asks for the first-run result to be reported, so a passing test is not mistaken for a skipped TDD step.
