# Step 9a — Deterministic Verification Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace "the file exists on disk" as Rudra's definition of a completed task with a deterministic five-stage verification gate that can be run from Python, from the CLI, and (in Step 9c) from a fix loop.

**Architecture:** A new `src/rudra/verify/` package owns the pipeline — stage order, short-circuit policy, verdict. Stack-specific commands live in `src/rudra/stacks/` beside the existing `resolve_test_command`, because `registry.py:3-4` states that adding a language must be a data change. `verify/` never starts a subprocess: it calls `run_gated` (`src/rudra/shell/runner.py:91`), which stays Rudra's only subprocess call site. No model is involved anywhere in this step.

**Tech Stack:** Python 3.12+, `ast` and `tokenize` from the standard library, `ruff` and `mypy` as new runtime dependencies, Typer + Rich for the CLI, pytest for tests.

**Spec:** `docs/superpowers/specs/2026-08-12-step9a-verification-gate-design.md`

## Global Constraints

- **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE`. This ordering is non-negotiable (CLAUDE.md §2.2).
- **Evidence-based claims only.** Every claim about the codebase cites `file.py:line`.
- **Three gates must pass before every commit:** `uv run ruff check src/ tests/` prints `All checks passed!`; `uv run ruff format --check src/ tests/` is clean; `uv run pytest -q` never goes below the current count (656 passed, 2 skipped at Step 8).
- **Use `uv run`, never a bare `.venv/bin/…`.** A local `.venv` drifts from `uv.lock` and has already let two defects reach `main` (CLAUDE.md §9).
- **Line length 100**, ruff `select = ["E", "F", "I", "W"]`, `target-version = "py312"` (`pyproject.toml:76-82`).
- **`from __future__ import annotations`** at the top of every new module — every existing module in `src/rudra/` does this.
- **No new TOML config keys.** The test stage reuses `[tools] test_timeout`; every other stage uses the same value. An inert config key is worse than no key (CLAUDE.md §6).
- **Never build a `.rudra/…` path by hand.** `src/rudra/state/paths.py::rudra_paths` is the single source of truth (CLAUDE.md §3).
- **`verify/` must not import `rudra.agent` or `rudra.middleware`.** Step 9b replaces both; a dependency either way would couple this step to work that is about to change.
- **Lint is advisory. Syntax, typecheck, test, and stub scan block.** Fixed in code, never configurable (spec S9a.2).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/verify/result.py` | **Create.** `Finding`, `StageResult`, `VerifyReport`, outcome constants. Pure data plus one derivation (`VerifyReport.from_stages`) |
| `src/rudra/verify/stubs.py` | **Create.** Placeholder detection. Python via `ast`/`tokenize`; Rust and JS/TS via line regex. Also the source-file walker used by `--all` |
| `src/rudra/verify/pipeline.py` | **Create.** Stage execution: syntax, lint, typecheck, test, stubs. Order, short-circuit, per-stage internal-error containment |
| `src/rudra/verify/report.py` | **Create.** Rendering only: Rich table, `--json` dict, verdict line, exit-code mapping, docs-anchor messages |
| `src/rudra/verify/__init__.py` | **Create.** `verify_project()` — the one public entry point — plus `changed_files_from_git()` |
| `src/rudra/stacks/profile.py` | **Modify.** `StackProfile` gains `lint_command` / `typecheck_command`; new `CommandResolution` record |
| `src/rudra/stacks/registry.py` | **Modify.** Those two commands on all five profiles. Data change only |
| `src/rudra/stacks/detect.py` | **Modify.** `resolve_lint_command()`, `resolve_typecheck_command()` beside the existing `resolve_test_command` (`:152`) |
| `src/rudra/cli.py` | **Modify.** `@app.command("verify")` beside `doctor` (`:389`) |
| `pyproject.toml` | **Modify.** `ruff` moves dev → runtime; `mypy` added |
| `Documentation/10-verification.md` | **Create.** Stages, outcomes, per-stack install matrix |
| `Documentation/04-cli-reference.md`, `README.md` | **Modify.** The `verify` entry |
| `TODO.md` | **Modify.** Close `C6.6`, re-point `A1.24`, split §E's Step 9 row into 9a/9b/9c |

Syntax handling lives inside `pipeline.py` rather than in a resolver, because it is the only stage that is not uniformly "resolve a command and run it" — Python's is a native `ast.parse` with no subprocess at all.

---

## Task 1: Result types

**Files:**
- Create: `src/rudra/verify/__init__.py` (empty placeholder for now), `src/rudra/verify/result.py`
- Test: `tests/test_verify_result.py`

**Interfaces:**
- Consumes: nothing.
- Produces: constants `PASSED`, `FAILED`, `NOT_APPLICABLE`, `COVERED_BY`, `MISSING_TOOL`, `DENIED`, `STAGE_ORDER`; `Finding(file: str, line: int | None, message: str)`; `StageResult(name, outcome, blocking, escalate=False, command=None, stack=None, findings=(), output_tail="", detail="", docs_anchor=None)` with property `halts: bool`; `VerifyReport(passed, stages, blocker, escalate)` with classmethod `from_stages(stages) -> VerifyReport`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_result.py`:

```python
"""VerifyReport's verdict derivation — the one piece of logic in the data layer."""

from __future__ import annotations

from rudra.verify.result import (
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    StageResult,
    VerifyReport,
)


def stage(name, outcome, *, blocking=True, escalate=False):
    return StageResult(name=name, outcome=outcome, blocking=blocking, escalate=escalate)


def test_all_passed_is_a_pass():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("test", PASSED)])
    assert report.passed is True
    assert report.blocker is None
    assert report.escalate is False


def test_a_blocking_failure_blocks():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    assert report.passed is False
    assert report.blocker.name == "typecheck"
    assert report.escalate is False


def test_a_failing_advisory_stage_never_blocks():
    report = VerifyReport.from_stages(
        [stage("lint", FAILED, blocking=False), stage("test", PASSED)]
    )
    assert report.passed is True
    assert report.blocker is None


def test_not_applicable_never_blocks():
    report = VerifyReport.from_stages(
        [stage("typecheck", NOT_APPLICABLE), stage("test", PASSED)]
    )
    assert report.passed is True


def test_missing_tool_blocks_and_escalates():
    report = VerifyReport.from_stages(
        [stage("typecheck", MISSING_TOOL, escalate=True)]
    )
    assert report.passed is False
    assert report.escalate is True


def test_denied_blocks_and_escalates():
    report = VerifyReport.from_stages([stage("test", DENIED, escalate=True)])
    assert report.passed is False
    assert report.escalate is True


def test_internal_error_escalates_while_reporting_failed():
    # An internal error is a bug in Rudra, not in the user's code. It must not
    # reach the fix loop, so it escalates despite reporting `failed`.
    report = VerifyReport.from_stages([stage("lint", FAILED, escalate=True)])
    assert report.escalate is False, "advisory stages never become the blocker"

    report = VerifyReport.from_stages([stage("typecheck", FAILED, escalate=True)])
    assert report.passed is False
    assert report.escalate is True


def test_the_first_blocking_failure_is_the_blocker():
    report = VerifyReport.from_stages(
        [stage("syntax", FAILED), stage("typecheck", FAILED)]
    )
    assert report.blocker.name == "syntax"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_result.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.verify'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/verify/__init__.py` with a single line for now (Task 8 fills it in):

```python
"""Deterministic verification — Rudra's definition of a completed task (C6.6)."""
```

Create `src/rudra/verify/result.py`:

```python
"""What one verification run found. Pure data, plus one derivation.

Six outcomes rather than pass/fail, because collapsing them is the A1.57
failure mode: pytest exits 5 when it collects nothing, and reading that as
"tests failed" sends a fix loop to repair working code. TestResult already
refuses that collapse (testing/runner.py:39-43); this record inherits it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PASSED = "passed"
FAILED = "failed"
NOT_APPLICABLE = "not_applicable"
COVERED_BY = "covered_by"
MISSING_TOOL = "missing_tool"
DENIED = "denied"

# The order stages run in, and the order the report renders them in.
STAGE_ORDER: tuple[str, ...] = ("syntax", "lint", "typecheck", "test", "stubs")

# Outcomes that stop a blocking stage's pipeline.
_HALTING = frozenset({FAILED, MISSING_TOOL, DENIED})


@dataclass(frozen=True)
class Finding:
    """One problem, located. `line` is None when the tool did not report one."""

    file: str
    line: int | None
    message: str


@dataclass(frozen=True)
class StageResult:
    """What one stage did.

    `escalate` is deliberately a field rather than a function of `outcome`.
    A missing tool and a denied command escalate because no model can fix
    them, but so does an internal error in Rudra itself -- which reports
    `failed`, because from the caller's side the stage did not complete.
    Deriving it from the outcome could not express that third case.
    """

    name: str
    outcome: str
    blocking: bool
    escalate: bool = False
    command: tuple[str, ...] | None = None
    stack: str | None = None
    findings: tuple[Finding, ...] = ()
    output_tail: str = ""
    detail: str = ""
    docs_anchor: str | None = None

    @property
    def halts(self) -> bool:
        """Does this result stop the pipeline?

        Advisory stages never halt, however badly they went. That is what
        "advisory" means -- it governs the verdict, not the visibility.
        """
        return self.blocking and self.outcome in _HALTING


@dataclass(frozen=True)
class VerifyReport:
    """The verdict, and every stage that ran to reach it.

    `escalate` is the field Step 9c branches on: False means the fix loop
    receives `blocker.output_tail` and iterates, True means stop and hand
    back to the user. Without the split, the loop would spend its whole
    attempt budget (C6.5a) asking a model to install a toolchain.
    """

    passed: bool
    stages: tuple[StageResult, ...]
    blocker: StageResult | None
    escalate: bool

    @classmethod
    def from_stages(cls, stages: Iterable[StageResult]) -> VerifyReport:
        ordered = tuple(stages)
        blocker = next((stage for stage in ordered if stage.halts), None)
        return cls(
            passed=blocker is None,
            stages=ordered,
            blocker=blocker,
            escalate=blocker is not None and blocker.escalate,
        )


__all__ = [
    "COVERED_BY",
    "DENIED",
    "FAILED",
    "MISSING_TOOL",
    "NOT_APPLICABLE",
    "PASSED",
    "STAGE_ORDER",
    "Finding",
    "StageResult",
    "VerifyReport",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_result.py -q`
Expected: PASS, 8 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
```
Expected: `All checks passed!`, format clean, and the suite up by 8.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/__init__.py src/rudra/verify/result.py tests/test_verify_result.py
git commit -m "feat(verify): result types for the verification gate

Six outcomes rather than pass/fail. escalate is a field rather than a
function of outcome so an internal Rudra error can report failed while
still bypassing the fix loop.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Stub scanner

**Files:**
- Create: `src/rudra/verify/stubs.py`
- Test: `tests/test_verify_stubs.py`

**Interfaces:**
- Consumes: `Finding` from `rudra.verify.result`.
- Produces: `scan_stubs(project_path: Path, files: Sequence[str]) -> tuple[Finding, ...]`; `source_files(project_path: Path) -> tuple[str, ...]` (project-relative paths, skip-dirs pruned, used by `--all`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_stubs.py`:

```python
"""The stub scanner — the stage a test-only gate cannot have."""

from __future__ import annotations

from rudra.verify.stubs import scan_stubs, source_files


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def messages(findings):
    return [f.message for f in findings]


def test_bare_pass_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    pass\n")
    findings = scan_stubs(tmp_path, [name])
    assert len(findings) == 1
    assert findings[0].line == 1
    assert "pass" in findings[0].message


def test_pass_in_an_except_block_is_not_a_stub(tmp_path):
    name = write(
        tmp_path,
        "a.py",
        "def handler():\n    try:\n        go()\n    except OSError:\n        pass\n",
    )
    assert scan_stubs(tmp_path, [name]) == ()


def test_empty_class_body_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "class Marker:\n    pass\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_while_true_pass_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def spin():\n    while True:\n        pass\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_ellipsis_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    ...\n")
    assert "..." in messages(scan_stubs(tmp_path, [name]))[0]


def test_docstring_only_body_is_a_stub(tmp_path):
    name = write(tmp_path, "a.py", 'def handler():\n    """Does the thing."""\n')
    assert "docstring" in messages(scan_stubs(tmp_path, [name]))[0]


def test_not_implemented_error_is_a_stub_bare_and_called(tmp_path):
    bare = write(tmp_path, "a.py", "def a():\n    raise NotImplementedError\n")
    called = write(tmp_path, "b.py", 'def b():\n    raise NotImplementedError("later")\n')
    assert len(scan_stubs(tmp_path, [bare, called])) == 2


def test_raising_another_error_is_not_a_stub(tmp_path):
    name = write(tmp_path, "a.py", 'def a():\n    raise ValueError("bad input")\n')
    assert scan_stubs(tmp_path, [name]) == ()


def test_todo_comment_is_reported(tmp_path):
    name = write(tmp_path, "a.py", "def a():\n    return 1  # TODO: handle None\n")
    findings = scan_stubs(tmp_path, [name])
    assert len(findings) == 1
    assert findings[0].line == 2


def test_the_word_todo_in_a_string_is_not_a_comment(tmp_path):
    # tokenize, not regex: a regex over lines cannot tell these apart.
    name = write(tmp_path, "a.py", 'def a():\n    return "TODO list"\n')
    assert scan_stubs(tmp_path, [name]) == ()


def test_unparseable_python_yields_nothing(tmp_path):
    # The syntax stage blocks first, so stubs never sees this in practice.
    name = write(tmp_path, "a.py", "def broken(\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_rust_todo_macro_is_a_stub(tmp_path):
    name = write(tmp_path, "a.rs", "fn go() {\n    todo!()\n}\n")
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_rust_unimplemented_macro_is_a_stub(tmp_path):
    name = write(tmp_path, "a.rs", "fn go() {\n    unimplemented!()\n}\n")
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_js_not_implemented_throw_is_a_stub(tmp_path):
    name = write(tmp_path, "a.ts", 'function go() {\n  throw new Error("Not implemented");\n}\n')
    assert len(scan_stubs(tmp_path, [name])) == 1


def test_unknown_extension_is_skipped(tmp_path):
    name = write(tmp_path, "notes.md", "TODO: write this up\n")
    assert scan_stubs(tmp_path, [name]) == ()


def test_a_missing_file_is_skipped_not_raised(tmp_path):
    assert scan_stubs(tmp_path, ["gone.py"]) == ()


def test_source_files_prunes_skip_dirs(tmp_path):
    write(tmp_path, "app.py", "x = 1\n")
    write(tmp_path, "node_modules/dep/index.js", "x\n")
    write(tmp_path, ".venv/lib/thing.py", "x = 1\n")
    write(tmp_path, "target/debug/build.rs", "fn a() {}\n")
    assert source_files(tmp_path) == ("app.py",)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_stubs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.verify.stubs'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/verify/stubs.py`:

```python
"""Placeholder detection -- the stage a test-only gate cannot have.

C6.6 exists as a separate item from "run the tests" because an agent can
pass a suite and still leave placeholders behind. Nothing here executes
anything or starts a subprocess.

Python is scanned with `ast` and `tokenize`, not regex, because a bare
`pass` is legitimate in an `except:` block, in `class Foo: pass`, and in
`while True: pass` -- a line matcher cannot tell those from a stubbed
function body. Rust and JS/TS get line regex, which is shallower, and the
report says so rather than implying otherwise.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Sequence
from pathlib import Path

from rudra.stacks.registry import ALL_SKIP_DIRS
from rudra.verify.result import Finding

_MARKER = re.compile(r"\b(TODO|FIXME)\b")
_RUST_STUB = re.compile(r"\b(todo!|unimplemented!)\s*[(\[{]")
_JS_STUB = re.compile(r"""throw\s+new\s+Error\s*\(\s*['"`][^'"`]*not\s+implemented""", re.I)

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_RUST_SUFFIXES = frozenset({".rs"})
_JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_SCANNED_SUFFIXES = _PYTHON_SUFFIXES | _RUST_SUFFIXES | _JS_SUFFIXES

# Directories nothing is ever scanned in, on top of every stack's own
# build-output dirs. ALL_SKIP_DIRS is the registry's union (registry.py:71).
_EXTRA_SKIP_DIRS = frozenset({".git", ".rudra", ".idea", ".vscode"})
_SKIP_DIRS = ALL_SKIP_DIRS | _EXTRA_SKIP_DIRS


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _stub_reason(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Why this function is a placeholder, or None if it has a real body."""
    body = list(node.body)
    docstrings = [
        statement
        for statement in body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    ]
    remaining = [statement for statement in body if statement not in docstrings]

    if not remaining:
        return "function body is only a docstring"
    if len(remaining) != 1:
        return None

    only = remaining[0]
    if isinstance(only, ast.Pass):
        return "function body is a bare `pass`"
    if (
        isinstance(only, ast.Expr)
        and isinstance(only.value, ast.Constant)
        and only.value.value is Ellipsis
    ):
        return "function body is `...`"
    if isinstance(only, ast.Raise):
        raised = only.exc
        if isinstance(raised, ast.Call):
            raised = raised.func
        if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
            return "function raises NotImplementedError"
    return None


def _scan_python(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # The syntax stage blocks before this one ever runs, so an
        # unparseable file here means the caller skipped ahead. Report
        # nothing rather than guessing.
        return findings

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            reason = _stub_reason(node)
            if reason is not None:
                findings.append(Finding(relative, node.lineno, f"{node.name}: {reason}"))

    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and _MARKER.search(token.string):
                findings.append(
                    Finding(relative, token.start[0], f"marker comment: {token.string.strip()}")
                )
    except (tokenize.TokenError, IndentationError):
        pass

    return sorted(findings, key=lambda finding: (finding.line or 0, finding.message))


def _scan_lines(relative: str, text: str, pattern: re.Pattern[str]) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            findings.append(Finding(relative, number, f"placeholder: {line.strip()}"))
        elif _MARKER.search(line):
            findings.append(Finding(relative, number, f"marker: {line.strip()}"))
    return findings


def scan_stubs(project_path: Path, files: Sequence[str]) -> tuple[Finding, ...]:
    """Placeholders in `files`, which are project-relative paths.

    Changed files only, per spec S9a.5: a pre-existing TODO in code Rudra
    never touched is not Rudra's defect, and failing on it would make the
    gate unpassable in any real repository.
    """
    project_path = Path(project_path)
    findings: list[Finding] = []

    for relative in files:
        path = project_path / relative
        suffix = path.suffix.lower()
        if suffix not in _SCANNED_SUFFIXES:
            continue
        text = _read(path)
        if text is None:
            continue

        if suffix in _PYTHON_SUFFIXES:
            findings.extend(_scan_python(relative, text))
        elif suffix in _RUST_SUFFIXES:
            findings.extend(_scan_lines(relative, text, _RUST_STUB))
        else:
            findings.extend(_scan_lines(relative, text, _JS_STUB))

    return tuple(findings)


def source_files(project_path: Path) -> tuple[str, ...]:
    """Every scannable source file in the project, build output pruned.

    Backs `rudra verify --all`. Sorted so the report is stable between runs.
    """
    project_path = Path(project_path)
    found: list[str] = []

    for path in project_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(project_path)
        if any(part in _SKIP_DIRS for part in relative.parts[:-1]):
            continue
        found.append(str(relative))

    return tuple(sorted(found))


__all__ = ["scan_stubs", "source_files"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_stubs.py -q`
Expected: PASS, 17 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/stubs.py tests/test_verify_stubs.py
git commit -m "feat(verify): stub scanner

ast plus tokenize for Python so a legitimate pass in except:, class
bodies, and while True: is not mistaken for a stubbed function. Rust and
JS/TS use line regex and the report says so.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Stack command resolution and the bundled toolchain

**Files:**
- Modify: `src/rudra/stacks/profile.py`, `src/rudra/stacks/registry.py`, `src/rudra/stacks/detect.py`, `pyproject.toml:24-58` and `:60-64`
- Test: `tests/test_stacks_commands.py`

**Interfaces:**
- Consumes: `StackProfile`, `_venv_executable` (`detect.py:76`), `_load_package_json` (`detect.py:18`), `_declares_dependency` (`detect.py:33`).
- Produces: `CommandResolution(argv: tuple[str, ...] | None, status: str, detail: str = "", tool: str = "")` with `status` in `{"ok", "not_applicable", "missing_tool"}`; `resolve_lint_command(project_path, profile) -> CommandResolution`; `resolve_typecheck_command(project_path, profile) -> CommandResolution`; `StackProfile.lint_command` / `.typecheck_command`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_stacks_commands.py`:

```python
"""Lint and typecheck command resolution, per stack."""

from __future__ import annotations

import json
import sys

from rudra.stacks.detect import resolve_lint_command, resolve_typecheck_command
from rudra.stacks.registry import ANGULAR, NODE, PYTHON, REACT, RUST


def make_venv_binary(tmp_path, name):
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    executable = binaries / name
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def write_package_json(tmp_path, payload):
    (tmp_path / "package.json").write_text(json.dumps(payload), encoding="utf-8")


def test_python_lint_prefers_the_project_venv(tmp_path):
    executable = make_venv_binary(tmp_path, "ruff")
    resolution = resolve_lint_command(tmp_path, PYTHON)
    assert resolution.status == "ok"
    assert resolution.argv[0] == str(executable)
    assert "check" in resolution.argv


def test_python_lint_falls_back_to_the_bundled_ruff(tmp_path):
    resolution = resolve_lint_command(tmp_path, PYTHON)
    assert resolution.status == "ok"
    assert resolution.argv[:3] == (sys.executable, "-m", "ruff")


def test_python_typecheck_prefers_the_project_venv(tmp_path):
    executable = make_venv_binary(tmp_path, "mypy")
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.argv[0] == str(executable)
    assert "--ignore-missing-imports" not in resolution.argv


def test_bundled_mypy_ignores_missing_imports(tmp_path):
    # Rudra's mypy cannot resolve the project's dependencies, so without
    # this flag every run drowns in "Cannot find implementation or library
    # stub for module". Spec S9a.4.
    resolution = resolve_typecheck_command(tmp_path, PYTHON)
    assert resolution.argv[:3] == (sys.executable, "-m", "mypy")
    assert "--ignore-missing-imports" in resolution.argv


def test_rust_uses_the_profile_commands(tmp_path):
    assert resolve_lint_command(tmp_path, RUST).argv == ("cargo", "clippy")
    assert resolve_typecheck_command(tmp_path, RUST).argv == ("cargo", "check")


def test_plain_javascript_has_no_typechecker(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    resolution = resolve_typecheck_command(tmp_path, NODE)
    assert resolution.status == "not_applicable"
    assert "JavaScript" in resolution.detail


def test_typescript_project_without_tsc_is_a_missing_tool(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"typescript": "^5"}})
    resolution = resolve_typecheck_command(tmp_path, REACT)
    assert resolution.status == "missing_tool"
    assert resolution.tool == "tsc"


def test_typescript_project_with_tsc_resolves_to_noemit(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"typescript": "^5"}})
    binary = tmp_path / "node_modules" / ".bin" / "tsc"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    resolution = resolve_typecheck_command(tmp_path, ANGULAR)
    assert resolution.status == "ok"
    assert resolution.argv == (str(binary), "--noEmit")


def test_a_tsconfig_alone_makes_typecheck_applicable(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert resolve_typecheck_command(tmp_path, NODE).status == "missing_tool"


def test_node_without_eslint_makes_lint_not_applicable(tmp_path):
    write_package_json(tmp_path, {"name": "app"})
    resolution = resolve_lint_command(tmp_path, NODE)
    assert resolution.status == "not_applicable"


def test_node_declaring_eslint_without_installing_it_is_a_missing_tool(tmp_path):
    write_package_json(tmp_path, {"devDependencies": {"eslint": "^9"}})
    resolution = resolve_lint_command(tmp_path, NODE)
    assert resolution.status == "missing_tool"
    assert resolution.tool == "eslint"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_stacks_commands.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_lint_command'`

- [ ] **Step 3: Add the fields and the record**

In `src/rudra/stacks/profile.py`, replace the module docstring and add the two fields plus the new record:

```python
"""Pure data records for the stack layer -- no behaviour, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
```

Add to `StackProfile`, immediately after `test_command` (`profile.py:34`):

```python
    lint_command: tuple[str, ...] | None = None
    typecheck_command: tuple[str, ...] | None = None
```

Extend the `StackProfile` docstring's attribute list with:

```
        lint_command: The lint command as an argv tuple, or None when it
            depends on the project's own layout -- a virtualenv, or a
            package.json dependency. resolve_lint_command works it out.
        typecheck_command: Same, for type checking. Rust is the only stack
            whose answer is fixed, because cargo ships with the toolchain.
```

Append the new record to `profile.py`:

```python
OK = "ok"
NOT_APPLICABLE = "not_applicable"
MISSING_TOOL = "missing_tool"


@dataclass(frozen=True)
class CommandResolution:
    """The answer to "what command runs this stage here?".

    Three statuses, because two would lose the distinction the gate is
    built on: a plain-JavaScript project has no typechecker and never
    will (`not_applicable`), while a TypeScript project without tsc
    installed has one that is absent (`missing_tool`, which blocks and
    escalates). One status cannot carry both.
    """

    argv: tuple[str, ...] | None
    status: str
    detail: str = ""
    tool: str = ""
```

- [ ] **Step 4: Populate the registry**

In `src/rudra/stacks/registry.py`, add to `RUST` (after its `test_command`):

```python
    lint_command=("cargo", "clippy"),
    typecheck_command=("cargo", "check"),
```

Add to `PYTHON`, `NODE`, `REACT`, and `ANGULAR` — explicit `None` with the reason, matching how `test_command=None` is already justified in place (`registry.py:14-18`):

```python
    # None for both: the answer depends on the project's own layout -- a
    # virtualenv for Python, a declared devDependency for the Node family.
    # resolve_lint_command / resolve_typecheck_command work them out.
    lint_command=None,
    typecheck_command=None,
```

- [ ] **Step 5: Write the resolvers**

Append to `src/rudra/stacks/detect.py` (after `resolve_test_command`, `:152-176`), and add `import sys` to the existing imports:

```python
def _bundled(tool: str, *arguments: str) -> tuple[str, ...]:
    """Rudra's own copy of a tool, invoked through its own interpreter.

    `sys.executable`, not `shutil.which`: PATH is unreliable under
    `uv tool install`, and permissions/env.py:4 records that scrubbed_env
    produces an environment in which `which ruff` finds nothing.

    This is the one place sys.executable is correct, and it is worth saying
    so out loud because `_system_interpreter` above forbids it. That
    prohibition is about the *target project's* interpreter -- D18 is
    explicit that the user's tests must never run under Rudra's Python.
    Here the call deliberately invokes Rudra's own bundled tool, which is
    exactly what sys.executable names.
    """
    return (sys.executable, "-m", tool, *arguments)


def _node_binary(project_path: Path, name: str) -> Path | None:
    candidate = Path(project_path) / "node_modules" / ".bin" / name
    return candidate if candidate.is_file() else None


def resolve_lint_command(project_path: Path, profile: StackProfile) -> CommandResolution:
    """The argv for this stack's linter.

    Lint is advisory (spec S9a.2), so nothing here can fail a task -- but
    it still distinguishes "this project has no linter" from "it declares
    one that is not installed", because the report shows the difference and
    a user acting on it needs to know which.
    """
    project_path = Path(project_path)

    if profile.lint_command is not None:
        return CommandResolution(tuple(profile.lint_command), OK)

    if profile.name == "python":
        ruff = _venv_executable(project_path, "ruff")
        if ruff is not None:
            return CommandResolution((str(ruff), "check", "."), OK)
        return CommandResolution(_bundled("ruff", "check", "."), OK)

    package_json = _load_package_json(project_path)
    if not _declares_dependency(package_json, "eslint"):
        return CommandResolution(
            None, NOT_APPLICABLE, detail="this project declares no eslint", tool="eslint"
        )
    eslint = _node_binary(project_path, "eslint")
    if eslint is None:
        return CommandResolution(
            None,
            MISSING_TOOL,
            detail="package.json declares eslint but node_modules/.bin/eslint is absent",
            tool="eslint",
        )
    return CommandResolution((str(eslint), "."), OK)


def resolve_typecheck_command(project_path: Path, profile: StackProfile) -> CommandResolution:
    """The argv for this stack's type checker.

    Typecheck blocks, so the not_applicable / missing_tool split carries
    real weight here: plain JavaScript has no type checker and must pass,
    while a TypeScript project without tsc must stop and tell the user.
    """
    project_path = Path(project_path)

    if profile.typecheck_command is not None:
        return CommandResolution(tuple(profile.typecheck_command), OK)

    if profile.name == "python":
        mypy = _venv_executable(project_path, "mypy")
        if mypy is not None:
            return CommandResolution((str(mypy), "."), OK)
        # --ignore-missing-imports because Rudra's mypy cannot see the
        # project's dependencies. It still catches type errors in the
        # project's own code, which is where generated code goes wrong.
        return CommandResolution(
            _bundled("mypy", "--ignore-missing-imports", "."),
            OK,
            detail="Rudra's bundled mypy; project dependencies are not resolved",
        )

    package_json = _load_package_json(project_path)
    typescript = _declares_dependency(package_json, "typescript") or (
        project_path / "tsconfig.json"
    ).is_file()
    if not typescript:
        return CommandResolution(
            None,
            NOT_APPLICABLE,
            detail="plain JavaScript project -- no type checker applies",
            tool="tsc",
        )
    tsc = _node_binary(project_path, "tsc")
    if tsc is None:
        return CommandResolution(
            None,
            MISSING_TOOL,
            detail="this project is TypeScript but node_modules/.bin/tsc is absent",
            tool="tsc",
        )
    return CommandResolution((str(tsc), "--noEmit"), OK)
```

Add to the imports at the top of `detect.py`:

```python
from rudra.stacks.profile import MISSING_TOOL, NOT_APPLICABLE, OK, CommandResolution, StackProfile
```

- [ ] **Step 6: Promote the bundled tools to runtime dependencies**

In `pyproject.toml`, add to the `dependencies` list (`:24-58`), after the `prompt-toolkit` entry:

```toml
    # Bundled verification toolchain (Step 9a, S9a.3). Runtime, not dev:
    # `rudra verify` runs these against the user's project when the project
    # brings none of its own. Invoked as `sys.executable -m <tool>`, never
    # via PATH -- see stacks/detect.py::_bundled.
    "ruff>=0.15.8",
    "mypy>=1.14",
```

Replace the dev group (`:60-64`) with:

```toml
[dependency-groups]
dev = [
    "pytest>=9.0.2",
]
```

Then run `uv sync` so the lock file and the venv agree.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv sync
uv run pytest tests/test_stacks_commands.py -q
```
Expected: PASS, 12 tests

- [ ] **Step 8: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```
Expected: existing stack tests still pass — the two new `StackProfile` fields have defaults, so no existing construction breaks.

- [ ] **Step 9: Commit**

```bash
git add src/rudra/stacks/ pyproject.toml uv.lock tests/test_stacks_commands.py
git commit -m "feat(stacks): lint and typecheck command resolution

Commands live in stacks/ because registry.py:3-4 says adding a language
must be a data change; a second stack table in verify/ would repeat the
A1.30 drift. ruff and mypy become runtime deps and are invoked as
sys.executable -m <tool> rather than through PATH.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Pipeline — native stages and ordering

**Files:**
- Create: `src/rudra/verify/pipeline.py`
- Test: `tests/test_verify_pipeline.py`

**Interfaces:**
- Consumes: `StageResult`, outcome constants, `STAGE_ORDER` (Task 1); `scan_stubs` (Task 2); `detect` (`stacks/detect.py:51`).
- Produces: `syntax_stage(project_path, profile, changed_files, *, gate, console, cfg) -> StageResult`; `stubs_stage(project_path, changed_files) -> StageResult`; `run_pipeline(project_path, *, profile, changed_files, gate, console, cfg, _command_override=None) -> tuple[StageResult, ...]`.

Command stages are stubbed in this task and filled in by Tasks 5 and 6, so the ordering logic is testable on its own.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_pipeline.py`:

```python
"""Stage ordering, short-circuit, and the two native stages."""

from __future__ import annotations

from rudra.stacks.registry import PYTHON, RUST
from rudra.verify.pipeline import stubs_stage, syntax_stage
from rudra.verify.result import COVERED_BY, FAILED, PASSED


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def test_python_syntax_stage_runs_no_command(tmp_path):
    name = write(tmp_path, "a.py", "x = 1\n")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == PASSED
    assert result.command is None, "ast.parse is native -- no subprocess, no gate"


def test_python_syntax_stage_catches_a_truncated_file(tmp_path):
    # The exact failure main_agent.py:439 currently records as success.
    name = write(tmp_path, "a.py", "def broken(\n")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == FAILED
    assert result.blocking is True
    assert result.findings[0].file == name


def test_python_syntax_stage_catches_an_empty_file(tmp_path):
    # An empty file parses fine; emptiness is the stub scan's job, not this
    # stage's. Documented so the boundary is not re-litigated later.
    name = write(tmp_path, "a.py", "")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == PASSED


def test_rust_syntax_is_covered_by_typecheck(tmp_path):
    result = syntax_stage(tmp_path, RUST, [], gate=None, console=None, cfg=None)
    assert result.outcome == COVERED_BY
    assert "typecheck" in result.detail


def test_stubs_stage_reports_findings_and_blocks(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    pass\n")
    result = stubs_stage(tmp_path, [name])
    assert result.outcome == FAILED
    assert result.blocking is True
    assert len(result.findings) == 1


def test_stubs_stage_passes_on_clean_code(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    return 1\n")
    assert stubs_stage(tmp_path, [name]).outcome == PASSED
```

Add the ordering tests to the same file:

```python
from rudra.verify.pipeline import run_pipeline


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def names(stages):
    return [stage.name for stage in stages]


def test_pipeline_stops_at_the_first_blocking_failure(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def broken(\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
    )
    assert names(stages) == ["syntax"], "lint, typecheck, test and stubs must not run"


def test_pipeline_runs_every_stage_when_nothing_blocks(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    return 1\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
        _command_override={"lint": ["true"], "typecheck": ["true"], "test": ["true"]},
    )
    assert names(stages) == ["syntax", "lint", "typecheck", "test", "stubs"]


def test_an_internal_error_in_a_stage_does_not_kill_the_run(tmp_path, monkeypatch):
    # A bug in Rudra must be reported as a bug in Rudra -- escalate, never
    # hand it to the fix loop as if it were the user's code (A1.39 class).
    import rudra.verify.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(pipeline, "scan_stubs", boom)
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "x = 1\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
        _command_override={"lint": ["true"], "typecheck": ["true"], "test": ["true"]},
    )
    last = stages[-1]
    assert last.name == "stubs"
    assert last.outcome == FAILED
    assert last.escalate is True
    assert "internal error" in last.detail
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.verify.pipeline'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/verify/pipeline.py`:

```python
"""Stage execution: what runs, in what order, and when to stop.

Nothing here starts a subprocess. Command stages call run_gated
(shell/runner.py:91), which stays Rudra's only subprocess call site, so
every command reaches the permission engine as `execute:<command>` with no
new rule vocabulary (Step 8 spec S8.2).

The pipeline stops at the first blocking failure. A file that does not
parse makes lint, typecheck and test output pure noise, and feeding all of
it to a 32B model consumes the context the fix loop needs to act on the
real failure (D6).
"""

from __future__ import annotations

import ast
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rudra.stacks.profile import StackProfile
from rudra.verify.result import (
    COVERED_BY,
    FAILED,
    PASSED,
    Finding,
    StageResult,
)
from rudra.verify.stubs import scan_stubs

# Stacks whose parse step is subsumed by their type checker. Rust and
# TypeScript have no cheap native parser, and `cargo check` / `tsc --noEmit`
# already answer "does it parse" -- a separate pass would be a second full
# compile for no new information.
_SYNTAX_COVERED_BY_TYPECHECK = frozenset({"rust"})

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def syntax_stage(
    project_path: Path,
    profile: StackProfile | None,
    changed_files: Sequence[str],
    *,
    gate: Any,
    console: Any,
    cfg: Any,
) -> StageResult:
    """Does it parse?

    Python is checked with ast.parse -- native, instant, and ungated. It
    catches exactly the truncated or half-written file that
    main_agent.py:438-439 currently records as a completed task.
    """
    stack = profile.name if profile is not None else None

    if stack in _SYNTAX_COVERED_BY_TYPECHECK or _is_typescript(project_path, profile):
        return StageResult(
            name="syntax",
            outcome=COVERED_BY,
            blocking=True,
            stack=stack,
            detail="covered by typecheck -- this stack has no cheaper parse step",
        )

    if stack != "python":
        return _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack)

    findings: list[Finding] = []
    for relative in changed_files:
        path = Path(project_path) / relative
        if path.suffix.lower() not in _PYTHON_SUFFIXES:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(Finding(relative, exc.lineno, exc.msg or "syntax error"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            findings.append(Finding(relative, None, str(exc)))

    if findings:
        return StageResult(
            name="syntax",
            outcome=FAILED,
            blocking=True,
            stack=stack,
            findings=tuple(findings),
            detail=f"{len(findings)} file(s) do not parse",
        )
    return StageResult(
        name="syntax",
        outcome=PASSED,
        blocking=True,
        stack=stack,
        detail=f"{len(changed_files)} file(s) parsed",
    )


def stubs_stage(project_path: Path, changed_files: Sequence[str]) -> StageResult:
    """Placeholders in the files this run touched.

    The stage that makes C6.6 more than "run the tests": an agent can pass
    a suite and still leave `pass` where an implementation belongs.
    """
    findings = scan_stubs(Path(project_path), changed_files)
    if findings:
        return StageResult(
            name="stubs",
            outcome=FAILED,
            blocking=True,
            findings=findings,
            detail=f"{len(findings)} placeholder(s) in changed files",
        )
    return StageResult(
        name="stubs",
        outcome=PASSED,
        blocking=True,
        detail=f"{len(changed_files)} changed file(s) scanned",
    )


def run_pipeline(
    project_path: Path,
    *,
    profile: StackProfile | None,
    changed_files: Sequence[str],
    gate: Any,
    console: Any,
    cfg: Any,
    _command_override: dict[str, list[str]] | None = None,
) -> tuple[StageResult, ...]:
    """Every stage that ran, in order, stopping at the first blocking failure.

    Stages that never ran are absent from the result rather than carrying a
    "skipped" outcome -- report.py knows STAGE_ORDER and renders the gap.
    That keeps the outcome vocabulary at the six the spec defines.
    """
    project_path = Path(project_path)
    stages: list[StageResult] = []

    runners = (
        ("syntax", lambda: syntax_stage(
            project_path, profile, changed_files, gate=gate, console=console, cfg=cfg
        )),
        ("lint", lambda: lint_stage(
            project_path, profile, gate=gate, console=console, cfg=cfg,
            _override=(_command_override or {}).get("lint"),
        )),
        ("typecheck", lambda: typecheck_stage(
            project_path, profile, gate=gate, console=console, cfg=cfg,
            _override=(_command_override or {}).get("typecheck"),
        )),
        ("test", lambda: test_stage(
            project_path, gate=gate, console=console, cfg=cfg,
            _override=(_command_override or {}).get("test"),
        )),
        ("stubs", lambda: stubs_stage(project_path, changed_files)),
    )

    for name, runner in runners:
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001 - containment is the point
            # A bug in Rudra must not be reported as a bug in the user's
            # code, and must not kill the run (A1.39 class). It escalates
            # rather than reaching the fix loop.
            result = StageResult(
                name=name,
                outcome=FAILED,
                blocking=True,
                escalate=True,
                detail=f"internal error in the {name} stage: {exc}",
                output_tail=traceback.format_exc(),
            )
        stages.append(result)
        if result.halts:
            break

    return tuple(stages)


__all__ = ["run_pipeline", "stubs_stage", "syntax_stage"]
```

The three helpers this module references but does not yet define — `_is_typescript`, `_node_syntax_stage`, `lint_stage`, `typecheck_stage`, `test_stage` — land in Tasks 5 and 6. To keep this task's tests green, add temporary definitions at the bottom of the module now, marked for replacement:

```python
def _is_typescript(project_path: Path, profile: StackProfile | None) -> bool:
    """Replaced in Task 5 by the tsconfig / devDependency check."""
    return False


def _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack) -> StageResult:
    """Replaced in Task 5 by `node --check` per changed file."""
    return StageResult(name="syntax", outcome=PASSED, blocking=True, stack=stack)


def lint_stage(project_path, profile, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 5."""
    return StageResult(name="lint", outcome=PASSED, blocking=False)


def typecheck_stage(project_path, profile, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 5."""
    return StageResult(name="typecheck", outcome=PASSED, blocking=True)


def test_stage(project_path, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 6."""
    return StageResult(name="test", outcome=PASSED, blocking=True)
```

Note: name the module-level function `test_stage`, and add `python_functions = ["test_*"]` is **not** set in `pyproject.toml` — pytest's default `python_functions` is `test*`, so a module-level `test_stage` inside `src/` is never collected (pytest only collects from `testpaths = ["tests"]`, `pyproject.toml:88`). No rename is needed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_pipeline.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/pipeline.py tests/test_verify_pipeline.py
git commit -m "feat(verify): pipeline ordering and the two native stages

Python syntax is ast.parse -- no subprocess, no gate -- and catches the
truncated file main_agent.py:439 currently calls success. Per-stage
exception containment reports a Rudra bug as escalating rather than
handing it to the fix loop.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Command stages — lint and typecheck

**Files:**
- Modify: `src/rudra/verify/pipeline.py` (replace the four placeholders from Task 4)
- Test: `tests/test_verify_outcomes.py`

**Interfaces:**
- Consumes: `run_gated` and `CommandResult` (`shell/runner.py:33,91`); `scrubbed_env` (`permissions/env.py`); `resolve_lint_command` / `resolve_typecheck_command` / `CommandResolution` (Task 3); `MAX_TAIL_CHARS` (`testing/runner.py:32`).
- Produces: real `lint_stage`, `typecheck_stage`, `_node_syntax_stage`, `_is_typescript`; plus `_run_command_stage(name, resolution, *, blocking, project_path, gate, console, cfg, stack) -> StageResult` and `_parse_findings(text) -> tuple[Finding, ...]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_outcomes.py`:

```python
"""Every outcome a command stage can produce -- §5 of the spec, row by row."""

from __future__ import annotations

import json

from rudra.shell.runner import CommandResult
from rudra.stacks.registry import NODE, PYTHON
from rudra.verify import pipeline
from rudra.verify.result import DENIED, FAILED, MISSING_TOOL, NOT_APPLICABLE, PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def fake_run(result):
    def runner(argv, **kwargs):
        return result

    return runner


def command_result(**kwargs):
    base = dict(argv=("x",), command="x", exit_code=0, stdout="", stderr="")
    base.update(kwargs)
    return CommandResult(**base)


def test_a_clean_command_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "run_gated", fake_run(command_result(exit_code=0)))
    result = pipeline.typecheck_stage(
        tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == PASSED


def test_a_denied_command_escalates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_gated",
        fake_run(command_result(exit_code=None, denied=True, denial_reason="<auto:shell-not-opted-in>")),
    )
    result = pipeline.typecheck_stage(
        tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == DENIED
    assert result.escalate is True
    assert "shell-not-opted-in" in result.detail


def test_a_timeout_fails_without_escalating(tmp_path, monkeypatch):
    # An infinite loop in generated code is a real defect the fix loop can
    # fix, so this must not escalate.
    monkeypatch.setattr(
        pipeline, "run_gated", fake_run(command_result(exit_code=None, timed_out=True))
    )
    result = pipeline.typecheck_stage(
        tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == FAILED
    assert result.escalate is False
    assert "test_timeout" in result.detail


def test_a_binary_that_will_not_start_is_a_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_gated",
        fake_run(command_result(exit_code=None, stderr="mypy: No such file or directory")),
    )
    result = pipeline.typecheck_stage(
        tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True
    assert result.docs_anchor is not None


def test_a_nonzero_exit_fails_and_parses_findings(tmp_path, monkeypatch):
    output = "src/models.py:14: error: Argument 1 has incompatible type\n"
    monkeypatch.setattr(
        pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout=output))
    )
    result = pipeline.typecheck_stage(
        tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == FAILED
    assert result.findings[0].file == "src/models.py"
    assert result.findings[0].line == 14


def test_tsc_style_findings_are_parsed(tmp_path):
    findings = pipeline._parse_findings("src/app.ts(12,3): error TS2345: Argument type\n")
    assert findings[0].file == "src/app.ts"
    assert findings[0].line == 12


def test_plain_javascript_typecheck_is_not_applicable(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")
    result = pipeline.typecheck_stage(
        tmp_path, NODE, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == NOT_APPLICABLE
    assert result.escalate is False


def test_a_declared_but_uninstalled_tool_escalates_with_a_docs_anchor(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"typescript": "^5"}}), encoding="utf-8"
    )
    result = pipeline.typecheck_stage(
        tmp_path, NODE, gate=object(), console=None, cfg=FakeCfg()
    )
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True
    assert result.docs_anchor == "Documentation/10-verification.md#typescript"


def test_lint_is_advisory_even_when_it_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout="a.py:1:1: F401\n"))
    )
    result = pipeline.lint_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.blocking is False
    assert result.halts is False
    assert result.findings, "advisory governs the verdict, not the visibility"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_outcomes.py -q`
Expected: FAIL — `AttributeError: module 'rudra.verify.pipeline' has no attribute 'run_gated'`

- [ ] **Step 3: Replace the placeholders**

In `src/rudra/verify/pipeline.py`, extend the imports:

```python
import re
import shutil

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated
from rudra.stacks.detect import (
    _load_package_json,
    resolve_lint_command,
    resolve_typecheck_command,
)
from rudra.stacks.profile import MISSING_TOOL as RESOLUTION_MISSING_TOOL
from rudra.stacks.profile import NOT_APPLICABLE as RESOLUTION_NOT_APPLICABLE
from rudra.stacks.profile import CommandResolution
from rudra.testing.runner import MAX_TAIL_CHARS
from rudra.verify.result import DENIED, MISSING_TOOL, NOT_APPLICABLE
```

Add the docs anchors and the shared command runner, and delete the four placeholder definitions:

```python
# Every missing_tool message ends here, so the error and its fix are one
# hop apart. Composed once rather than at each resolution site.
_DOCS = "Documentation/10-verification.md"
_ANCHORS = {
    "tsc": f"{_DOCS}#typescript",
    "eslint": f"{_DOCS}#eslint",
    "cargo": f"{_DOCS}#rust",
    "node": f"{_DOCS}#node",
}

# `file:line: message` -- mypy, ruff, eslint, cargo.
_FINDING = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)(?::\d+)?:\s*(?P<message>.+)$")
# `file(line,col): message` -- tsc.
_TSC_FINDING = re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+),\d+\):\s*(?P<message>.+)$")

_MAX_FINDINGS = 50


def _tail(text: str, limit: int = MAX_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n{text[-limit:]}"


def _parse_findings(text: str) -> tuple[Finding, ...]:
    """Located problems from tool output, capped.

    Deliberately shallow, for the same reason the test parser is
    (testing/runner.py's parse module): a per-tool structured format needs
    a plugin or a JSON mode Rudra cannot assume is installed. The full
    output is on disk either way.
    """
    findings: list[Finding] = []
    for line in text.splitlines():
        match = _FINDING.match(line) or _TSC_FINDING.match(line)
        if match is None:
            continue
        findings.append(
            Finding(match.group("file"), int(match.group("line")), match.group("message").strip())
        )
        if len(findings) >= _MAX_FINDINGS:
            break
    return tuple(findings)


def _run_command_stage(
    name: str,
    resolution: CommandResolution,
    *,
    blocking: bool,
    project_path: Path,
    gate: Any,
    console: Any,
    cfg: Any,
    stack: str | None,
    override: list[str] | None = None,
) -> StageResult:
    """One command stage, from resolution to StageResult.

    Every branch that cannot succeed on a retry says so, and every one that
    needs a human rather than a model sets escalate.
    """
    if override is None:
        if resolution.status == RESOLUTION_NOT_APPLICABLE:
            return StageResult(
                name=name,
                outcome=NOT_APPLICABLE,
                blocking=blocking,
                stack=stack,
                detail=resolution.detail,
            )
        if resolution.status == RESOLUTION_MISSING_TOOL:
            return StageResult(
                name=name,
                outcome=MISSING_TOOL,
                blocking=blocking,
                escalate=True,
                stack=stack,
                detail=resolution.detail,
                docs_anchor=_ANCHORS.get(resolution.tool, _DOCS),
                findings=(),
            )
        argv = list(resolution.argv or ())
    else:
        argv = list(override)

    result = run_gated(
        argv,
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=cfg.tools.test_timeout,
        env=scrubbed_env(cfg),
    )
    combined = f"{result.stdout}\n{result.stderr}".strip()

    if result.denied:
        return StageResult(
            name=name,
            outcome=DENIED,
            blocking=blocking,
            escalate=True,
            command=result.argv,
            stack=stack,
            detail=result.denial_reason or "denied by the permission gate",
        )
    if result.timed_out:
        return StageResult(
            name=name,
            outcome=FAILED,
            blocking=blocking,
            command=result.argv,
            stack=stack,
            detail=f"timed out after {cfg.tools.test_timeout}s -- raise [tools] test_timeout "
            "if this stage is genuinely slow",
            output_tail=_tail(combined),
        )
    if result.exit_code is None:
        return StageResult(
            name=name,
            outcome=MISSING_TOOL,
            blocking=blocking,
            escalate=True,
            command=result.argv,
            stack=stack,
            detail=result.stderr.strip() or "the command could not be started",
            docs_anchor=_ANCHORS.get(resolution.tool, _DOCS),
        )
    if result.exit_code == 0:
        return StageResult(
            name=name, outcome=PASSED, blocking=blocking, command=result.argv, stack=stack
        )
    return StageResult(
        name=name,
        outcome=FAILED,
        blocking=blocking,
        command=result.argv,
        stack=stack,
        findings=_parse_findings(combined),
        output_tail=_tail(combined),
        detail=resolution.detail,
    )


def _is_typescript(project_path: Path, profile: StackProfile | None) -> bool:
    """Does this project typecheck through tsc?

    Used by the syntax stage: where tsc runs, `tsc --noEmit` is already the
    parse step and a separate syntax pass buys nothing.
    """
    if profile is None or profile.name not in {"node", "react", "angular"}:
        return False
    package_json = _load_package_json(Path(project_path))
    declared = any(
        "typescript" in (package_json.get(section) or {})
        for section in ("dependencies", "devDependencies", "peerDependencies")
    )
    return declared or (Path(project_path) / "tsconfig.json").is_file()


def _node_syntax_stage(
    project_path: Path,
    changed_files: Sequence[str],
    gate: Any,
    console: Any,
    cfg: Any,
    stack: str | None,
) -> StageResult:
    """`node --check` per changed JavaScript file.

    Plain JavaScript has no type checker to subsume the parse step, so this
    is the only place the check can happen.
    """
    javascript = [
        relative
        for relative in changed_files
        if Path(relative).suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}
    ]
    if not javascript:
        return StageResult(
            name="syntax",
            outcome=NOT_APPLICABLE,
            blocking=True,
            stack=stack,
            detail="no JavaScript files changed",
        )
    if shutil.which("node") is None:
        return StageResult(
            name="syntax",
            outcome=MISSING_TOOL,
            blocking=True,
            escalate=True,
            stack=stack,
            detail="node is not on PATH, so JavaScript cannot be parse-checked",
            docs_anchor=_ANCHORS["node"],
        )

    findings: list[Finding] = []
    for relative in javascript:
        result = run_gated(
            ["node", "--check", relative],
            cwd=Path(project_path),
            gate=gate,
            console=console,
            timeout=cfg.tools.test_timeout,
            env=scrubbed_env(cfg),
        )
        if result.denied:
            return StageResult(
                name="syntax",
                outcome=DENIED,
                blocking=True,
                escalate=True,
                stack=stack,
                detail=result.denial_reason or "denied by the permission gate",
            )
        if result.exit_code not in (0, None):
            findings.append(Finding(relative, None, result.stderr.strip().splitlines()[0]))

    if findings:
        return StageResult(
            name="syntax",
            outcome=FAILED,
            blocking=True,
            stack=stack,
            findings=tuple(findings),
            detail=f"{len(findings)} file(s) do not parse",
        )
    return StageResult(
        name="syntax",
        outcome=PASSED,
        blocking=True,
        stack=stack,
        detail=f"{len(javascript)} file(s) parsed",
    )


def lint_stage(
    project_path: Path,
    profile: StackProfile | None,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """Advisory. It reports fully and never fails a task (spec S9a.2)."""
    if profile is None:
        return StageResult(
            name="lint", outcome=NOT_APPLICABLE, blocking=False, detail="no stack detected"
        )
    return _run_command_stage(
        "lint",
        resolve_lint_command(project_path, profile),
        blocking=False,
        project_path=Path(project_path),
        gate=gate,
        console=console,
        cfg=cfg,
        stack=profile.name,
        override=_override,
    )


def typecheck_stage(
    project_path: Path,
    profile: StackProfile | None,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """Blocking. A type error is a genuine defect (spec S9a.2)."""
    if profile is None:
        return StageResult(
            name="typecheck", outcome=NOT_APPLICABLE, blocking=True, detail="no stack detected"
        )
    return _run_command_stage(
        "typecheck",
        resolve_typecheck_command(project_path, profile),
        blocking=True,
        project_path=Path(project_path),
        gate=gate,
        console=console,
        cfg=cfg,
        stack=profile.name,
        override=_override,
    )
```

Note that `_run_command_stage` reads `resolution.tool` in its `exit_code is None` branch, so `_override` callers must still pass a real resolution — they do, because the override only replaces the argv.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_outcomes.py tests/test_verify_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/pipeline.py tests/test_verify_outcomes.py
git commit -m "feat(verify): lint and typecheck stages

Every command reaches run_gated as execute:<command>, so there is no new
permission vocabulary and --auto's shell opt-in covers these
automatically. Denied and missing-tool escalate; a timeout does not,
because an infinite loop in generated code is fixable.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Test stage

**Files:**
- Modify: `src/rudra/verify/pipeline.py` (replace the Task 4 placeholder `test_stage`)
- Test: `tests/test_verify_test_stage.py`

**Interfaces:**
- Consumes: `run_tests` and `TestResult` (`testing/runner.py:111,36`).
- Produces: real `test_stage(project_path, *, gate, console, cfg, _override=None) -> StageResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_test_stage.py`:

```python
"""TestResult -> StageResult. The mapping A1.57 exists to protect."""

from __future__ import annotations

from rudra.testing.runner import TestResult
from rudra.verify import pipeline
from rudra.verify.result import DENIED, FAILED, MISSING_TOOL, NOT_APPLICABLE, PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def stage_for(monkeypatch, result, tmp_path):
    monkeypatch.setattr(pipeline, "run_tests", lambda *a, **k: result)
    return pipeline.test_stage(tmp_path, gate=object(), console=None, cfg=FakeCfg())


def test_passing_tests_pass(monkeypatch, tmp_path):
    result = stage_for(monkeypatch, TestResult(available=True, exit_code=0, passed=True), tmp_path)
    assert result.outcome == PASSED


def test_failing_tests_fail_without_escalating(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=1, passed=False, total=3, failed=1, output_tail="E"),
        tmp_path,
    )
    assert result.outcome == FAILED
    assert result.escalate is False
    assert result.output_tail == "E"


def test_no_test_command_is_not_applicable(monkeypatch, tmp_path):
    result = stage_for(monkeypatch, TestResult(available=False), tmp_path)
    assert result.outcome == NOT_APPLICABLE


def test_no_tests_collected_is_not_a_failure(monkeypatch, tmp_path):
    # pytest exits 5 when it collects nothing. Reading that as "tests
    # failed" sends the fix loop to repair working code -- A1.57.
    result = stage_for(
        monkeypatch,
        TestResult(available=True, exit_code=5, passed=False, no_tests_collected=True),
        tmp_path,
    )
    assert result.outcome == NOT_APPLICABLE
    assert "collected" in result.detail


def test_denied_tests_escalate(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, denied=True, output_tail="<auto:shell-not-opted-in>"),
        tmp_path,
    )
    assert result.outcome == DENIED
    assert result.escalate is True


def test_a_launch_error_is_a_missing_tool(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch,
        TestResult(available=True, launch_error="pytest: not found"),
        tmp_path,
    )
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True


def test_a_timed_out_suite_fails_without_escalating(monkeypatch, tmp_path):
    result = stage_for(
        monkeypatch, TestResult(available=True, timed_out=True, output_tail="hung"), tmp_path
    )
    assert result.outcome == FAILED
    assert result.escalate is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_test_stage.py -q`
Expected: FAIL — the placeholder returns `PASSED` unconditionally, so six of seven tests fail.

- [ ] **Step 3: Replace the placeholder**

Add to the imports in `src/rudra/verify/pipeline.py`:

```python
from rudra.testing.runner import run_tests
```

Replace the placeholder `test_stage` with:

```python
def test_stage(
    project_path: Path,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """The project's own test suite, via run_tests (C3.6, Step 8).

    Reused whole rather than reimplemented: TestResult already keeps
    available / denied / timed_out / launch_error / no_tests_collected
    distinct precisely so a completion gate can act on them
    (testing/runner.py:39-43), and duplicating that logic here would give
    the two callers two different answers.
    """
    result = run_tests(
        Path(project_path), gate=gate, console=console, cfg=cfg, _command_override=_override
    )

    if not result.available:
        return StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            stack=result.stack,
            detail="this project declares no test command",
        )
    if result.denied:
        return StageResult(
            name="test",
            outcome=DENIED,
            blocking=True,
            escalate=True,
            command=result.command,
            stack=result.stack,
            detail=result.output_tail or "denied by the permission gate",
        )
    if result.launch_error is not None:
        return StageResult(
            name="test",
            outcome=MISSING_TOOL,
            blocking=True,
            escalate=True,
            command=result.command,
            stack=result.stack,
            detail=result.launch_error,
            docs_anchor=_DOCS,
        )
    if result.timed_out:
        return StageResult(
            name="test",
            outcome=FAILED,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail=f"the suite timed out after {cfg.tools.test_timeout}s -- raise "
            "[tools] test_timeout if it is genuinely slow",
            output_tail=result.output_tail,
        )
    if result.no_tests_collected:
        # Neither a pass nor a failure: the code was never exercised. Calling
        # it a failure sends the fix loop to repair working code (A1.57).
        return StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail="no tests were collected -- the suite ran but found nothing to execute",
        )
    if result.passed:
        counted = "" if result.total is None else f"{result.total} run"
        return StageResult(
            name="test",
            outcome=PASSED,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail=counted,
        )
    counted = (
        ""
        if result.total is None
        else f"{result.total} run, {result.failed} failed, {result.skipped} skipped"
    )
    return StageResult(
        name="test",
        outcome=FAILED,
        blocking=True,
        command=result.command,
        stack=result.stack,
        findings=_parse_findings(result.output_tail),
        output_tail=result.output_tail,
        detail=counted,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_test_stage.py -q`
Expected: PASS, 7 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/pipeline.py tests/test_verify_test_stage.py
git commit -m "feat(verify): test stage over run_tests

run_tests is reused whole rather than reimplemented, so the gate and the
model-facing tool cannot disagree. no_tests_collected maps to
not_applicable, which is what A1.57 was about.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Report rendering and exit codes

**Files:**
- Create: `src/rudra/verify/report.py`
- Test: `tests/test_verify_report.py`

**Interfaces:**
- Consumes: `VerifyReport`, `StageResult`, `STAGE_ORDER`, outcome constants (Task 1).
- Produces: `verdict_line(report) -> str`; `exit_code(report) -> int`; `to_dict(report) -> dict`; `render(report, console) -> None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_report.py`:

```python
"""Rendering, the verdict line, and the exit-code contract."""

from __future__ import annotations

from rich.console import Console

from rudra.verify.report import exit_code, render, to_dict, verdict_line
from rudra.verify.result import (
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


def stage(name, outcome, *, blocking=True, escalate=False, **kwargs):
    return StageResult(
        name=name, outcome=outcome, blocking=blocking, escalate=escalate, **kwargs
    )


def test_a_clean_run_exits_zero():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("test", PASSED)])
    assert exit_code(report) == 0


def test_a_blocking_failure_exits_one():
    report = VerifyReport.from_stages([stage("typecheck", FAILED)])
    assert exit_code(report) == 1


def test_an_escalation_exits_two():
    # 2 already means "the environment is wrong, not your code" in this CLI.
    report = VerifyReport.from_stages([stage("typecheck", MISSING_TOOL, escalate=True)])
    assert exit_code(report) == 2
    report = VerifyReport.from_stages([stage("test", DENIED, escalate=True)])
    assert exit_code(report) == 2


def test_the_verdict_never_says_a_bare_passed():
    report = VerifyReport.from_stages(
        [
            stage("syntax", PASSED),
            stage("lint", PASSED, blocking=False),
            stage("typecheck", NOT_APPLICABLE, detail="plain JavaScript project"),
            stage("test", NOT_APPLICABLE, detail="no tests were collected"),
            stage("stubs", PASSED),
        ]
    )
    line = verdict_line(report)
    assert line.startswith("passed")
    assert "2 of 5 stages did not run" in line
    assert "plain JavaScript project" in line
    assert "no tests were collected" in line


def test_a_fully_clean_verdict_says_so_plainly():
    report = VerifyReport.from_stages(
        [stage(name, PASSED, blocking=name != "lint") for name in
         ("syntax", "lint", "typecheck", "test", "stubs")]
    )
    assert verdict_line(report) == "passed — all 5 stages ran clean"


def test_a_failed_verdict_names_the_blocker():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    assert verdict_line(report).startswith("failed — typecheck")


def test_the_docs_anchor_reaches_the_verdict():
    report = VerifyReport.from_stages(
        [
            stage(
                "typecheck",
                MISSING_TOOL,
                escalate=True,
                detail="node_modules/.bin/tsc is absent",
                docs_anchor="Documentation/10-verification.md#typescript",
            )
        ]
    )
    line = verdict_line(report)
    assert "Documentation/10-verification.md#typescript" in line


def test_to_dict_is_json_serialisable_and_complete():
    import json

    report = VerifyReport.from_stages(
        [
            stage("syntax", PASSED, detail="1 file parsed"),
            stage(
                "stubs",
                FAILED,
                findings=(Finding("a.py", 3, "handler: function body is a bare `pass`"),),
            ),
        ]
    )
    payload = to_dict(report)
    json.dumps(payload)
    assert payload["passed"] is False
    assert payload["escalate"] is False
    assert payload["blocker"] == "stubs"
    assert payload["stages"][1]["findings"][0]["line"] == 3
    assert [entry["name"] for entry in payload["stages"]] == ["syntax", "stubs"]


def test_render_marks_stages_that_never_ran():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    console = Console(record=True, width=100)
    render(report, console)
    text = console.export_text()
    assert "not run" in text
    assert "stopped at typecheck" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_report.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.verify.report'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/verify/report.py`:

```python
"""Rendering only. Nothing here decides anything.

The verdict never says a bare "passed": a gate reporting success while
three stages were skipped is A1.57 at report level, so every stage that
did not produce a result is named.
"""

from __future__ import annotations

from dataclasses import asdict

from rich.console import Console
from rich.table import Table

from rudra.verify.result import (
    COVERED_BY,
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    STAGE_ORDER,
    VerifyReport,
)

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_ESCALATE = 2

# Outcomes that mean the stage produced no judgement about the code.
_INCONCLUSIVE = frozenset({NOT_APPLICABLE, COVERED_BY})

_STYLES = {
    PASSED: "green",
    FAILED: "red",
    DENIED: "yellow",
    MISSING_TOOL: "yellow",
    NOT_APPLICABLE: "dim",
    COVERED_BY: "dim",
}


def exit_code(report: VerifyReport) -> int:
    """0 passed, 1 a blocking stage failed, 2 escalation.

    2 already means "the environment is wrong, not your code" in this CLI --
    a non-TTY under mode = "ask" exits 2 before any model call -- so
    escalation maps onto it rather than inventing a code.
    """
    if report.escalate:
        return _EXIT_ESCALATE
    return _EXIT_OK if report.passed else _EXIT_FAILED


def verdict_line(report: VerifyReport) -> str:
    """One line, and it always accounts for all five stages."""
    if not report.passed and report.blocker is not None:
        blocker = report.blocker
        parts = [f"failed — {blocker.name} ({blocker.outcome})"]
        if blocker.detail:
            parts.append(blocker.detail)
        if blocker.docs_anchor:
            parts.append(f"see {blocker.docs_anchor}")
        return ": ".join(parts[:2]) + (f" — {parts[2]}" if len(parts) > 2 else "")

    ran = {stage.name for stage in report.stages if stage.outcome not in _INCONCLUSIVE}
    inconclusive = [stage for stage in report.stages if stage.outcome in _INCONCLUSIVE]
    absent = [name for name in STAGE_ORDER if name not in ran and name not in
              {stage.name for stage in inconclusive}]

    skipped = len(inconclusive) + len(absent)
    if skipped == 0:
        return f"passed — all {len(STAGE_ORDER)} stages ran clean"

    reasons = "; ".join(
        f"{stage.name}: {stage.detail or stage.outcome}" for stage in inconclusive
    )
    for name in absent:
        reasons = f"{reasons}; {name}: did not run" if reasons else f"{name}: did not run"
    return f"passed — {skipped} of {len(STAGE_ORDER)} stages did not run ({reasons})"


def to_dict(report: VerifyReport) -> dict:
    """The --json payload. Every field the human table shows, plus findings."""
    return {
        "passed": report.passed,
        "escalate": report.escalate,
        "blocker": report.blocker.name if report.blocker is not None else None,
        "verdict": verdict_line(report),
        "exit_code": exit_code(report),
        "stages": [
            {
                **asdict(stage),
                "command": list(stage.command) if stage.command else None,
                "findings": [asdict(finding) for finding in stage.findings],
            }
            for stage in report.stages
        ],
    }


def render(report: VerifyReport, console: Console) -> None:
    """The human view: one row per stage, then the verdict and the tail."""
    produced = {stage.name: stage for stage in report.stages}
    stopped_at = report.blocker.name if report.blocker is not None else None

    table = Table(title="rudra verify", header_style="bold")
    for column in ("Stage", "Outcome", "Detail"):
        table.add_column(column, overflow="fold")

    for name in STAGE_ORDER:
        stage = produced.get(name)
        if stage is None:
            reason = f"not run (stopped at {stopped_at})" if stopped_at else "not run"
            table.add_row(name, "—", f"[dim]{reason}[/dim]")
            continue
        outcome = stage.outcome + ("  (advisory)" if not stage.blocking else "")
        style = _STYLES.get(stage.outcome, "")
        detail = stage.detail
        if stage.docs_anchor:
            detail = f"{detail} — see {stage.docs_anchor}" if detail else f"see {stage.docs_anchor}"
        table.add_row(name, f"[{style}]{outcome}[/{style}]" if style else outcome, detail)

    console.print(table)

    marker = "✓" if report.passed else "✗"
    console.print(f"\n{marker} {verdict_line(report)}")

    for stage in report.stages:
        if stage.outcome != FAILED or not (stage.findings or stage.output_tail):
            continue
        console.print(f"\n[bold]{stage.name}[/bold]")
        for finding in stage.findings[:20]:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            console.print(f"  {location}: {finding.message}")
        if not stage.findings and stage.output_tail:
            console.print(stage.output_tail)


__all__ = ["exit_code", "render", "to_dict", "verdict_line"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_report.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/report.py tests/test_verify_report.py
git commit -m "feat(verify): report rendering, verdict line, exit codes

The verdict never says a bare passed -- it always names the stages that
produced no judgement. 0/1/2 exit codes, where 2 already means the
environment is wrong rather than the code.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: `verify_project()` entry point

**Files:**
- Modify: `src/rudra/verify/__init__.py`
- Test: `tests/test_verify_entry.py`

**Interfaces:**
- Consumes: `detect` (`stacks/detect.py:51`), `run_pipeline` (Task 4), `VerifyReport` (Task 1), `source_files` (Task 2), `status` / `is_repo` (`git/core.py:168,124`), `rudra_paths` (`state/paths.py:61`).
- Produces: `verify_project(project_path, *, changed_files=(), gate, console, cfg, _command_override=None) -> VerifyReport`; `changed_files_from_git(project_path, *, gate, console, cfg) -> tuple[str, ...] | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify_entry.py`:

```python
"""The public entry point: stack selection, logging, changed-file discovery."""

from __future__ import annotations

from rich.console import Console

from rudra.verify import changed_files_from_git, verify_project
from rudra.verify.result import PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


OVERRIDES = {"lint": ["true"], "typecheck": ["true"], "test": ["true"]}


def test_a_clean_python_project_passes(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    return 1\n")
    report = verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    assert report.passed is True
    assert [stage.outcome for stage in report.stages].count(PASSED) >= 3


def test_a_stub_blocks_even_when_everything_else_passes(tmp_path):
    # C6.6's entire justification: tests pass, placeholder remains.
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    pass\n")
    report = verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    assert report.passed is False
    assert report.blocker.name == "stubs"
    assert report.escalate is False


def test_the_most_specific_profile_wins(tmp_path):
    # A React project also matches plain node; profiles[0] is the answer,
    # the same choice run_tests makes (testing/runner.py:126-127).
    import json

    write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "^19"}}))
    report = verify_project(
        tmp_path,
        changed_files=[],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    stacks = {stage.stack for stage in report.stages if stage.stack}
    assert stacks == {"react"}


def test_a_greenfield_directory_does_not_crash(tmp_path):
    report = verify_project(
        tmp_path, changed_files=[], gate=None, console=Console(), cfg=FakeCfg()
    )
    assert isinstance(report.passed, bool)


def test_the_full_output_is_logged(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def broken(\n")
    verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    log = tmp_path / ".rudra" / "run" / "logs" / "verify.log"
    assert log.is_file()
    assert "syntax" in log.read_text(encoding="utf-8")


def test_changed_files_outside_a_repo_is_none(tmp_path, monkeypatch):
    import rudra.verify as verify

    monkeypatch.setattr(verify, "is_repo", lambda *a, **k: False)
    assert changed_files_from_git(tmp_path, gate=None, console=Console(), cfg=FakeCfg()) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_verify_entry.py -q`
Expected: FAIL — `ImportError: cannot import name 'verify_project'`

- [ ] **Step 3: Write the implementation**

Replace `src/rudra/verify/__init__.py` with:

```python
"""Deterministic verification -- Rudra's definition of a completed task (C6.6).

Replaces "the file exists on disk" (agent/main_agent.py:438-439), under
which a zero-byte file, a truncated file, and `def main(): pass` all count
as a completed task and get ticked off the plan. That is A1.8.

No model is involved anywhere in this package. The verdict is the test
runner's exit code, a parser, and a scanner -- nothing that can be talked
out of an answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.git.core import is_repo, status
from rudra.stacks.detect import detect
from rudra.state.paths import rudra_paths
from rudra.verify.pipeline import run_pipeline
from rudra.verify.report import exit_code, render, to_dict, verdict_line
from rudra.verify.result import Finding, StageResult, VerifyReport
from rudra.verify.stubs import source_files


def _write_log(project_path: Path, report: VerifyReport) -> None:
    """Full stage output to .rudra/run/logs/verify.log.

    Mirrors testing/runner.py:95-108, including its refusal to fail a run
    over a log that could not be written. D15's volatile subtree.
    """
    lines = [verdict_line(report), ""]
    for stage in report.stages:
        lines.append(f"## {stage.name}: {stage.outcome}")
        if stage.command:
            lines.append(f"command: {' '.join(stage.command)}")
        if stage.detail:
            lines.append(stage.detail)
        for finding in stage.findings:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            lines.append(f"  {location}: {finding.message}")
        if stage.output_tail:
            lines.append(stage.output_tail)
        lines.append("")

    logs = rudra_paths(project_path).logs
    try:
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "verify.log").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def verify_project(
    project_path: Path,
    *,
    changed_files: Sequence[str] = (),
    gate: Any,
    console: Console,
    cfg: Any,
    _command_override: dict[str, list[str]] | None = None,
) -> VerifyReport:
    """Run the gate and report what it found.

    `changed_files` are project-relative paths and scope the stub scan only
    (spec S9a.5) -- every other stage is whole-project, because a new file
    can break an old one's imports and a test suite is whole-project by
    nature. Step 9c passes the exact paths its coder wrote; `rudra verify`
    derives them from git.

    gate, console and cfg are threaded rather than read from module state,
    the same shape run_tests uses (testing/runner.py:111). A1.52 is an open
    defect about factories reading Path.cwd() instead of their argument;
    this signature does not repeat it.

    `_command_override` is test-only, following the precedent set at
    testing/runner.py:117-123: it exercises the missing-binary and timeout
    paths without requiring the machine to lack a toolchain.
    """
    project_path = Path(project_path)
    profiles = detect(project_path)
    # profiles[0] -- the most specific match, the same choice run_tests
    # makes (testing/runner.py:126-127). A Tauri app is genuinely both Rust
    # and Node; running every matched toolchain would triple the cost on a
    # monorepo and produce a verdict nobody asked for.
    profile = profiles[0] if profiles else None

    stages = run_pipeline(
        project_path,
        profile=profile,
        changed_files=list(changed_files),
        gate=gate,
        console=console,
        cfg=cfg,
        _command_override=_command_override,
    )
    report = VerifyReport.from_stages(stages)
    _write_log(project_path, report)
    return report


def changed_files_from_git(
    project_path: Path, *, gate: Any, console: Console, cfg: Any
) -> tuple[str, ...] | None:
    """Modified and untracked paths, or None when this is not a git repo.

    None is a real answer the caller acts on: `rudra verify` falls back to
    scanning everything and says so, because scanning nothing would report
    a clean stub stage over an unexamined tree.
    """
    project_path = Path(project_path)
    if not is_repo(project_path, gate=gate, console=console, cfg=cfg):
        return None
    entries = status(project_path, gate=gate, console=console, cfg=cfg)
    return tuple(sorted({entry.path for entry in entries if entry.path}))


__all__ = [
    "Finding",
    "StageResult",
    "VerifyReport",
    "changed_files_from_git",
    "exit_code",
    "render",
    "source_files",
    "to_dict",
    "verdict_line",
    "verify_project",
]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_verify_entry.py -q`
Expected: PASS, 6 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/verify/__init__.py tests/test_verify_entry.py
git commit -m "feat(verify): verify_project entry point

Stack selection uses profiles[0], matching run_tests. Full stage output
lands in .rudra/run/logs/verify.log via rudra_paths, never a hand-built
path.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: `rudra verify` CLI command

**Files:**
- Modify: `src/rudra/cli.py` (add after the `doctor` command, which ends before `@app.command("init")` at `:490`)
- Test: `tests/test_cli_verify.py`

**Interfaces:**
- Consumes: `verify_project`, `changed_files_from_git`, `source_files`, `render`, `to_dict`, `exit_code` (Task 8); `get_project_path` (`cli.py:205`), `_load_config_or_exit` (`cli.py:285`), `build_gate` (`permissions/__init__.py:80`), `ensure_layout` (`state/paths.py`).
- Produces: `rudra verify [--project-dir] [--all] [--changed PATH]... [--json]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_verify.py`:

```python
"""The verify command: flags, mutual exclusion, exit codes."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from rudra.cli import app

runner = CliRunner()


def project(tmp_path, source):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_all_and_changed_are_mutually_exclusive(tmp_path):
    project(tmp_path, "x = 1\n")
    result = runner.invoke(
        app, ["verify", "-d", str(tmp_path), "--all", "--changed", "a.py"]
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_a_syntax_error_exits_one(tmp_path):
    project(tmp_path, "def broken(\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py"])
    assert result.exit_code == 1
    assert "syntax" in result.output


def test_json_output_is_parseable(tmp_path):
    project(tmp_path, "def broken(\n")
    result = runner.invoke(
        app, ["verify", "-d", str(tmp_path), "--changed", "a.py", "--json"]
    )
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert payload["blocker"] == "syntax"


def test_a_stub_exits_one_and_names_the_file(tmp_path):
    project(tmp_path, "def handler():\n    pass\n")
    result = runner.invoke(app, ["verify", "-d", str(tmp_path), "--changed", "a.py"])
    assert result.exit_code == 1
    assert "a.py" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli_verify.py -q`
Expected: FAIL — `No such command 'verify'`

- [ ] **Step 3: Write the implementation**

Add to `src/rudra/cli.py`, immediately after the `doctor` command's body and before `@app.command("init")`:

```python
@app.command("verify")
def verify_command(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    scan_all: bool = typer.Option(
        False, "--all", help="Scan every source file, not just the ones git reports as changed"
    ),
    changed: Optional[list[Path]] = typer.Option(
        None, "--changed", help="Scope the stub scan to these files (repeatable)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Run the deterministic verification gate: syntax, lint, typecheck, tests, stubs."""
    import json as json_module

    from rudra.permissions import build_gate
    from rudra.state.paths import ensure_layout
    from rudra.verify import (
        changed_files_from_git,
        exit_code,
        render,
        source_files,
        to_dict,
        verify_project,
    )

    if scan_all and changed:
        # Silently letting one win would make the report's scope a guess.
        console.print("[red]--all and --changed are mutually exclusive.[/red]")
        raise typer.Exit(code=2)

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    ensure_layout(project_path)
    gate = build_gate(cfg, project_path)
    quiet = Console(quiet=True) if as_json else console

    if changed:
        files: tuple[str, ...] = tuple(str(path) for path in changed)
    elif scan_all:
        files = source_files(project_path)
    else:
        from_git = changed_files_from_git(
            project_path, gate=gate, console=quiet, cfg=cfg
        )
        if from_git is None:
            # Not a repo. Scanning nothing would report a clean stub stage
            # over an unexamined tree, so scan everything and say so.
            files = source_files(project_path)
            if not as_json:
                console.print("[dim]Not a git repository — scanning every source file.[/dim]")
        else:
            files = from_git

    report = verify_project(
        project_path, changed_files=files, gate=gate, console=quiet, cfg=cfg
    )

    if as_json:
        console.print_json(json_module.dumps(to_dict(report)))
    else:
        render(report, console)

    raise typer.Exit(code=exit_code(report))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli_verify.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Run it for real**

```bash
uv run rudra verify --help
uv run rudra verify --all --json | head -20
```
Expected: the help text lists all four flags; the JSON payload parses.

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/rudra/cli.py tests/test_cli_verify.py
git commit -m "feat(cli): rudra verify

Exit codes 0/1/2, where 2 means escalation -- consistent with the
non-TTY-under-ask exit that already means the environment is wrong. --all
and --changed are mutually exclusive rather than one silently winning.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Documentation

**Files:**
- Create: `Documentation/10-verification.md`
- Modify: `Documentation/04-cli-reference.md`, `README.md`
- Test: none — prose. Verified by the anchor check in Step 3.

**Interfaces:**
- Consumes: the anchor names emitted by `pipeline._ANCHORS` — `#typescript`, `#eslint`, `#rust`, `#node`.
- Produces: the file every `missing_tool` message points at.

- [ ] **Step 1: Write `Documentation/10-verification.md`**

```markdown
# Verification

Rudra decides a task is finished by running a **deterministic gate** — not by
asking a model whether it is done, and not by checking that a file exists.

Run it yourself:

```bash
rudra verify              # the files git reports as changed
rudra verify --all        # every source file in the project
rudra verify --json       # machine-readable
```

Exit codes: `0` passed · `1` a blocking stage failed · `2` something needs you
(a denied command, a missing tool, an internal error).

## The five stages

| Stage | Blocks? | What it asks |
|---|---|---|
| syntax | yes | Does every changed file parse? |
| lint | **no — advisory** | What does the linter say? Reported in full, never fails the task |
| typecheck | yes | Do the types hold? |
| test | yes | Does the project's own suite pass? |
| stubs | yes | Are there placeholders in the files this run touched? |

Stages run in that order and **stop at the first blocking failure**. A file that
does not parse makes everything downstream noise.

The stub scan is the reason this is more than "run the tests". An agent can pass
a suite and still leave `pass` where an implementation belongs.

## The six outcomes

| Outcome | Meaning |
|---|---|
| `passed` | ran, clean |
| `failed` | ran, found problems |
| `not_applicable` | this stack has no such stage — plain JavaScript has no type checker |
| `covered_by` | subsumed by a later stage — `cargo check` and `tsc --noEmit` are already the parse step |
| `missing_tool` | the stage applies here, but the tool is not installed. See the matrix below |
| `denied` | the permission gate refused the command |

A verdict never says a bare "passed". It always names the stages that produced
no judgement, so a clean-looking result cannot hide three stages that never ran.

## Installing the tools

Rudra **ships Python's toolchain**. Everything else belongs to your project's
own ecosystem, which a Python package cannot vendor.

| Stack | What you need |
|---|---|
| **Python** | Nothing. `ruff` and `mypy` ship with Rudra |
| **Rust** | `cargo` (comes with rustup) and, for lint, `rustup component add clippy` |
| **TypeScript** | `typescript` in your `devDependencies`, installed into `node_modules` |
| **Any JS/TS** | `eslint` in your `devDependencies`, for the advisory lint stage |

Rudra prefers **your** tool over its own whenever you have one. A `ruff` or
`mypy` in the project's `.venv/bin/` wins over the bundled copy, because it is
the version your project pins.

### Python {#python}

Nothing to install. If your project has no `mypy` of its own, Rudra runs its
bundled copy with `--ignore-missing-imports` — it cannot resolve your
dependencies, so it checks your code rather than your imports, and the report
says so.

### Rust {#rust}

`cargo check` covers both syntax and typecheck. Lint needs clippy:

```bash
rustup component add clippy
```

### TypeScript {#typescript}

Rudra treats a project as TypeScript when `package.json` declares `typescript`
or a `tsconfig.json` exists. It then needs the compiler installed:

```bash
npm install --save-dev typescript
```

Without it, typecheck reports `missing_tool` and stops — a project that declares
TypeScript and cannot run `tsc` is a broken setup, not a project without types.

### ESLint {#eslint}

```bash
npm install --save-dev eslint
```

Lint is advisory, so a missing eslint never fails a task. It is still reported.

### Node {#node}

Plain JavaScript is parse-checked with `node --check`, so `node` must be on
PATH. It has no type checker, and typecheck correctly reports `not_applicable`.

## Verification and `--auto`

Every stage except Python's syntax check and the stub scan runs a command, and
commands go through the permission gate as `execute`. Under `--auto` without
`--allow-shell`, they are denied — the same rule that already governs
`run_tests`.

An unattended run that should verify its own work needs:

```bash
rudra --auto --allow-shell "..."
```

In `ask` mode nothing changes: you see each command before it runs.
```

- [ ] **Step 2: Verify every anchor the code emits exists in the file**

```bash
grep -o '#[a-z]*' src/rudra/verify/pipeline.py | sort -u
grep -o '{#[a-z]*}' Documentation/10-verification.md | sort -u
```
Expected: every anchor in `_ANCHORS` (`#typescript`, `#eslint`, `#rust`, `#node`) appears in the docs file.

- [ ] **Step 3: Add the CLI reference entry**

Add to `Documentation/04-cli-reference.md`, after the `rudra doctor` section. Match the
surrounding heading level — open the file and use whatever level `rudra doctor` uses:

```markdown
## `rudra verify`

Run the deterministic verification gate over the project: syntax, lint, typecheck,
tests, and a stub scan. No model is involved.

| Flag | Meaning |
|---|---|
| `-d`, `--project-dir PATH` | Project to verify. Defaults to the current directory |
| `--all` | Scan every source file, not just what git reports as changed |
| `--changed PATH` | Scope the stub scan to these files. Repeatable |
| `--json` | Machine-readable output instead of the table |

`--all` and `--changed` are mutually exclusive.

| Exit code | Meaning |
|---|---|
| `0` | Passed. The verdict still names any stage that produced no judgement |
| `1` | A blocking stage failed — syntax, typecheck, test, or stubs |
| `2` | Something needs you: a denied command, a missing tool, or an internal error |

Lint never fails a task. It is reported in full and ignored by the verdict.

Stages that run commands go through the permission gate as `execute`, so under
`--auto` without `--allow-shell` they are denied and the run exits 2.

See [Verification](10-verification.md) for the stages, the outcomes, and the
per-stack install matrix.
```

- [ ] **Step 4: Add the README line**

In `README.md`'s command list, add:

```
rudra verify                 # deterministic gate: syntax, lint, typecheck, tests, stubs
```

- [ ] **Step 5: Commit**

```bash
git add Documentation/10-verification.md Documentation/04-cli-reference.md README.md
git commit -m "docs: verification gate and the per-stack install matrix

Every missing_tool message points here by anchor, so the error and its
fix are one hop apart.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: Ledger

**Files:**
- Modify: `TODO.md` (`C6.6` row at `:654`, `A1.24` row at `:405`, §E Step 9 row at `:759`), `CLAUDE.md` (§3 architecture tree, §8 commands)

**Interfaces:** none — documentation of state.

- [ ] **Step 1: Close C6.6**

Change the `C6.6` row's status from `PENDING` to `**DONE** 2026-08-12` and append the evidence: the module list, the test counts from `uv run pytest -q`, and the three acceptance runs from Task 12. Write this row **after** Task 12 has actually run, not before — session rule 4 requires the verifying output.

- [ ] **Step 2: Re-point A1.24**

`A1.24`'s row says it is deferred to "C6.6's deterministic completion gate". Replace that clause with:

```
Re-pointed 2026-08-12 (Step 9a): this gate never parses plans, so it cannot
close A1.24. `looks_like_path` (src/rudra/tools/planning_tools.py:28)
mis-classifies single-word prose in a *plan checklist*, which belongs to
C6.10's self-maintained ledger in Step 9c.
```

- [ ] **Step 3: Split §E's Step 9 row**

Replace the single Step 9 row (`TODO.md:759`) with three:

```markdown
| **9a** | **C6.6** — deterministic verification gate | 8 | The loop has nothing to loop on until the gate exists. Pure Python, no model, fully unit-testable. Spec: `docs/superpowers/specs/2026-08-12-step9a-verification-gate-design.md`. Plan: `docs/superpowers/plans/2026-08-12-step9a-verification-gate.md` |
| **9b** | **C6.2, C6.3, C6.4, U.11** — subagent architecture: coder / tester / reviewer | 9a | Replaces the two hand-wired agents. `planner_agent.py:111-112` already names this step |
| **9c** | **C6.1, C6.5, C6.5a, C6.10** — agentic loop, fix loop with bounds, self-maintained ledger | 9b | Closes A1.8 and A1.25 by deleting the loop that carries them. Consumes 9a's `VerifyReport.escalate` to decide iterate-vs-stop |
```

Add one line above the Stage III table recording the decomposition and its date, so the change is not mistaken for drift.

- [ ] **Step 4: Update CLAUDE.md**

In §3's architecture tree, add after the `testing/` entry:

```
├── verify/                 The deterministic gate (Step 9a). syntax · lint ·
│                           typecheck · test · stubs. Blocking except lint;
│                           VerifyReport.escalate splits fix-loop input from
│                           user action
```

In §8's command list, add:

```bash
.venv/bin/rudra verify               # deterministic gate over the changed files
```

Update the §8 test count to whatever `uv run pytest -q` actually reports.

- [ ] **Step 5: Commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: close C6.6, re-point A1.24, split step 9 into 9a/9b/9c

C6.6 is done. A1.24 was deferred to 'C6.6's gate' before the
decomposition; this gate never parses plans, so it moves to C6.10 in 9c.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: Acceptance

**Files:** none modified. This task produces evidence for Task 11's ledger entry.

**Interfaces:** none.

- [ ] **Step 1: Dogfood the gate on Rudra itself**

```bash
uv run rudra verify --all
```
Expected: the Python path end to end with real `ruff`, real `mypy`, and the real suite. Record the verdict line verbatim.

If `mypy` reports errors on Rudra's own source — likely, since the codebase has never been typechecked — **do not fix them in this step and do not weaken the gate.** Log them as a new `PENDING` row in `TODO.md` §A with `file:line` evidence, per session rule 2, and note in the row that they predate the gate.

- [ ] **Step 2: Prove syntax blocks**

```bash
WORK=$(mktemp -d)
printf '[project]\nname="acc"\nversion="0.1.0"\n' > "$WORK/pyproject.toml"
printf 'def broken(\n' > "$WORK/a.py"
uv run rudra verify -d "$WORK" --changed a.py; echo "exit=$?"
```
Expected: `exit=1`; the table shows `syntax  failed` and `lint`/`typecheck`/`test`/`stubs` as `not run (stopped at syntax)`.

- [ ] **Step 3: Prove the stub scan catches what tests cannot**

```bash
cat > "$WORK/a.py" <<'PY'
def add(a, b):
    return a + b


def subtract(a, b):
    pass
PY
mkdir -p "$WORK/tests"
cat > "$WORK/tests/test_a.py" <<'PY'
from a import add


def test_add():
    assert add(1, 2) == 3
PY
uv run rudra verify -d "$WORK" --changed a.py; echo "exit=$?"
```
Expected: `exit=1`; `test` reports `passed`, and `stubs` reports `failed` naming `a.py:5` and `subtract: function body is a bare pass`. **This is C6.6's entire justification in one run** — a suite that passes over a placeholder. Record the output verbatim.

- [ ] **Step 4: Prove the denial path**

```bash
cd "$WORK" && env -i PATH="$PATH" HOME="$HOME" \
  uv run --project /Users/archish/Documents/ai-ml/Rudra rudra verify --changed a.py
echo "exit=$?"
cat "$WORK/.rudra/run/logs/permissions.jsonl"
```

First set `mode = "auto"` with `shell_in_auto = false` in `$WORK/.rudra/config.toml` (via `rudra init -d "$WORK"`, then edit).

Expected: `exit=2`; `syntax` and `stubs` pass natively; `lint`, `typecheck`, and `test` report `denied`; the verdict names the escalation; and `permissions.jsonl` records each denial with `source: "auto-shell"`.

- [ ] **Step 5: Confirm the gates one final time**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
uv run rudra --version
```
Expected: `All checks passed!`, format clean, the suite well above its 656-passing Step 8 baseline, and `Rudra v0.2.0`.

- [ ] **Step 6: Clean up**

```bash
rm -rf "$WORK"
```

- [ ] **Step 7: Write the evidence into Task 11's ledger rows, then commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: C6.6 acceptance evidence

Three runs: dogfood on Rudra itself, syntax blocking a truncated file,
and a passing test suite over a bare-pass stub that the stub scan
catches. The third is why C6.6 is not just 'run the tests'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Appendix: What this plan deliberately does not touch

Stated so a reviewer does not read the omissions as gaps:

- **`src/rudra/agent/main_agent.py` is not modified.** The gate exists; nothing consumes it until Step 9c replaces the loop at `:362-465`. `A1.8` therefore stays `PENDING`.
- **No `run_verify` model-facing tool.** It is roughly ten lines on top of Task 8, but it would wire a tool into an agent architecture Step 9b replaces.
- **No prompt changes.** `planner_agent.py` and `coder_agent.py` are untouched.
- **No new TOML keys.** The test stage reuses `[tools] test_timeout` and every other stage borrows it.
- **`C11.2`** (the live per-stack acceptance matrix) stays `PENDING` — the fixture projects cover command *resolution*, not an end-to-end agent run, which needs a model backend.
