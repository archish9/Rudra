# Step 9c — The Agentic Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rudra's hardcoded file-by-file loop with a task ledger the agent maintains, a fix loop bounded by a deterministic gate, and a summary whose numbers cannot disagree with reality.

**Architecture:** A new `src/rudra/loop/` package. `ledger.py` and `bounds.py` are pure — no Rudra imports, no I/O beyond one file — and carry most of the test weight. `tools.py` gives the agent three typed tools with **no way to mark anything done**. `engine.py` is the orchestrator: it dispatches the 9b coder subagent, calls 9a's `verify_project`, and owns every stop decision.

**Tech Stack:** Python 3.12+ (`StrEnum`, `os.replace`), the existing `verify/` and `subagents/` packages, LangChain `@tool`, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-08-12-step9c-agentic-loop-design.md`

## Global Constraints

- **Never fix a bug on discovery.** Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE` (CLAUDE.md §2.2).
- **Evidence-based claims only.** Every claim about the codebase cites `file.py:line`.
- **Three gates before every commit:** `uv run ruff check src/ tests/` prints `All checks passed!`; `uv run ruff format --check src/ tests/` is clean; `uv run pytest -q` never drops below **827 passed, 2 skipped**.
- **Use `uv run`, never a bare `.venv/bin/…`** (CLAUDE.md §9).
- **Line length 100**, ruff `select = ["E", "F", "I", "W"]`, `target-version = "py312"`.
- **`from __future__ import annotations`** at the top of every new module.
- **`loop/` imports `verify` and `subagents`; neither imports `loop`.** `ledger.py` and `bounds.py` import nothing from Rudra at all.
- **No tool may produce `DONE` or `BLOCKED`.** Only `engine.py` writes those, and `DONE` only when `VerifyReport.passed` (spec S9c.1).
- **Never build a `.rudra/…` path by hand.** `state/paths.py::rudra_paths` is the single source of truth.
- **One new config key only:** `[agent] max_fix_attempts = 3`. An inert config key is worse than no key (CLAUDE.md §6).
- **`AgentResult` keeps its five fields** — `cli.py:719-725` and `:801-806` render them.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/loop/ledger.py` | **Create.** `Task`, `TaskStatus`, `Ledger`. Pure data + atomic save/load |
| `src/rudra/loop/bounds.py` | **Create.** `failure_signature`, `tests_produced_no_judgement`. Pure, no I/O |
| `src/rudra/loop/tools.py` | **Create.** `create_ledger_tools` → `add_tasks`, `drop_task`, `read_ledger` |
| `src/rudra/loop/engine.py` | **Create.** `run_loop`, `run_task`, `git_snapshot`, `summarise`, `Outcome` |
| `src/rudra/loop/__init__.py` | **Create.** Public surface |
| `src/rudra/state/paths.py` | **Modify.** `RudraPaths` gains `ledger_json` |
| `src/rudra/config/schema.py` | **Modify.** `[agent] max_fix_attempts` |
| `src/rudra/permissions/rules.py:29,35` | **Modify.** Control-plane and read-only tool names |
| `src/rudra/agent/planner_agent.py` | **Modify.** Ledger tools; prompt rewritten |
| `src/rudra/subagents/registry.py` | **Modify.** `_CODER_PROMPT` for task-shaped work |
| `src/rudra/agent/main_agent.py` | **Modify.** `RudraAgent.run` → `run_loop`; six functions deleted |
| `src/rudra/tools/planning_tools.py` | **DELETE.** Whole module |
| `src/rudra/agent/coder_agent.py` | **DELETE.** Superseded by the `coder` registry entry |
| `tests/test_plan_items.py` | **DELETE.** Every subject is deleted |
| `tests/test_main_agent_helpers.py:22-62` | **Modify.** Drop the `_check_off_file` tests; the rest stays |
| `tests/test_agent_wiring.py`, `test_rudra_dir_migration.py`, `test_permissions_*.py`, `test_no_direct_provider_imports.py` | **Modify.** Renamed tools and deleted modules |
| `Documentation/05-how-it-works.md`, `08-project-status.md`, `02-configuration.md`, `README.md` | **Modify.** The loop is no longer file-by-file |
| `TODO.md`, `CLAUDE.md` | **Modify.** Close C6.1/C6.5/C6.5a/C6.10/A1.8/A1.24/A1.25 |

**Task order matters.** Tasks 1–6 add and rewire; Task 7 deletes. Deleting first would leave the tree broken across several commits, and a broken intermediate commit is one nobody can bisect through.

**Two test files beyond the spec's §7 list**, so neither reads as scope creep:
`tests/test_loop_run.py` splits the outer loop's tests away from `run_task`'s —
`test_loop_engine.py` would otherwise carry two unrelated fixture sets — and
`tests/test_planner_ledger.py` covers the prompt and tool rewiring in Task 6,
which the spec describes in §6 but did not assign a file.

---

## Task 1: The ledger

**Files:**
- Create: `src/rudra/loop/__init__.py`, `src/rudra/loop/ledger.py`
- Modify: `src/rudra/state/paths.py`
- Test: `tests/test_loop_ledger.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TaskStatus` (StrEnum: `PENDING`, `IN_PROGRESS`, `DONE`, `BLOCKED`, `DROPPED`); `Task(id, description, status=PENDING, attempts=0, files_touched=(), last_signature=None, note="")`; `Ledger(tasks=[])` with `add(description) -> Task`, `get(task_id) -> Task | None`, `next_pending() -> Task | None`, `counts() -> dict[str, int]`, `save(path)`, `load(path)` classmethod; `RudraPaths.ledger_json`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_ledger.py`:

```python
"""The task ledger: what it records and how it survives a crash."""

from __future__ import annotations

import json

from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.state.paths import rudra_paths


def test_the_paths_module_owns_the_ledger_location(tmp_path):
    # Never build a .rudra/... path by hand (CLAUDE.md §3).
    assert rudra_paths(tmp_path).ledger_json == tmp_path / ".rudra" / "run" / "ledger.json"


def test_add_assigns_stable_sequential_ids():
    ledger = Ledger()
    first = ledger.add("write the parser")
    second = ledger.add("write its tests")
    assert (first.id, second.id) == ("t1", "t2")
    assert first.status is TaskStatus.PENDING


def test_get_finds_by_id_and_returns_none_otherwise():
    ledger = Ledger()
    ledger.add("a")
    assert ledger.get("t1").description == "a"
    assert ledger.get("t99") is None


def test_next_pending_returns_them_in_declaration_order():
    ledger = Ledger()
    ledger.add("a")
    ledger.add("b")
    assert ledger.next_pending().id == "t1"
    ledger.get("t1").status = TaskStatus.DONE
    assert ledger.next_pending().id == "t2"
    ledger.get("t2").status = TaskStatus.BLOCKED
    assert ledger.next_pending() is None


def test_a_dropped_task_is_not_pending():
    ledger = Ledger()
    ledger.add("a")
    ledger.get("t1").status = TaskStatus.DROPPED
    assert ledger.next_pending() is None


def test_counts_always_account_for_every_task():
    # The A1.25 invariant in its smallest form.
    ledger = Ledger()
    for description in ("a", "b", "c", "d"):
        ledger.add(description)
    ledger.get("t1").status = TaskStatus.DONE
    ledger.get("t2").status = TaskStatus.BLOCKED
    ledger.get("t3").status = TaskStatus.DROPPED
    counts = ledger.counts()
    assert counts["requested"] == 4
    assert counts["done"] + counts["blocked"] + counts["dropped"] + counts["pending"] == 4


def test_round_trip_preserves_every_field(tmp_path):
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.BLOCKED
    task.attempts = 3
    task.files_touched = ("a.py", "b.py")
    task.last_signature = "deadbeef"
    task.note = "no progress: the same failure twice"

    path = tmp_path / "ledger.json"
    ledger.save(path)
    loaded = Ledger.load(path)

    assert loaded.tasks == ledger.tasks
    assert loaded.get("t1").files_touched == ("a.py", "b.py")
    assert loaded.get("t1").status is TaskStatus.BLOCKED


def test_the_saved_file_is_readable_json(tmp_path):
    ledger = Ledger()
    ledger.add("a")
    path = tmp_path / "ledger.json"
    ledger.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tasks"][0]["id"] == "t1"
    assert payload["tasks"][0]["status"] == "pending"


def test_save_is_atomic(tmp_path, monkeypatch):
    # A crash mid-write must leave the previous ledger intact, not a
    # truncated one -- this file is the only record of how far a run got.
    path = tmp_path / "ledger.json"
    first = Ledger()
    first.add("survivor")
    first.save(path)

    import os

    def explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    second = Ledger()
    second.add("never lands")
    try:
        second.save(path)
    except OSError:
        pass

    assert Ledger.load(path).get("t1").description == "survivor"


def test_loading_a_missing_file_gives_an_empty_ledger(tmp_path):
    assert Ledger.load(tmp_path / "absent.json").tasks == []


def test_add_after_load_keeps_ids_unique(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("a")
    ledger.add("b")
    ledger.save(path)

    reloaded = Ledger.load(path)
    third = reloaded.add("c")
    assert third.id == "t3"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_loop_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.loop'`

- [ ] **Step 3: Add the ledger path**

In `src/rudra/state/paths.py`, add to the `RudraPaths` dataclass beside `plan_md`:

```python
    ledger_json: Path
```

and to the `rudra_paths()` return, beside `plan_md`:

```python
        # The task ledger (Step 9c, C6.10). Volatile: a run's tasks are
        # meaningless to the next run's request, so it is never resumed --
        # that is C7.2's --continue.
        ledger_json=run / "ledger.json",
```

Leave `plan_md` in place for now; Task 7 removes it with the module that writes it.

- [ ] **Step 4: Write the ledger**

Create `src/rudra/loop/__init__.py`:

```python
"""The agentic loop: a task ledger, a fix loop, and a deterministic gate (Step 9c)."""
```

Create `src/rudra/loop/ledger.py`:

```python
"""What the run is trying to do, and how far it got.

Supersedes the bare-filename checklist in .rudra/run/PLAN.md (C6.10). That
file was prose the orchestrator parsed, which is why a plan item could be
silently dropped between what was requested and what was reported (A1.25).
Here the agent writes through typed tools and Rudra reads a structured
record, so the two are no longer the same artefact.

Pure data plus one file. Imports nothing from Rudra, so its tests need no
model, no backend and no gate.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


class TaskStatus(StrEnum):
    """Where one task got to.

    DONE and BLOCKED are written by loop/engine.py and by nothing else --
    no agent-facing tool can produce them, which is how D9's "no LLM judge
    decides termination" is enforced structurally rather than by asking.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    DROPPED = "dropped"


# Statuses a task can no longer move out of.
_TERMINAL = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED})


@dataclass
class Task:
    """One unit of work the agent declared.

    A task is work, not a filename (spec S9c.2): "write tests for the
    parser" has no filename until it is done. `files_touched` records what
    it produced, which is exactly the changed-file list 9a's stub scan
    needs.
    """

    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    files_touched: tuple[str, ...] = ()
    last_signature: str | None = None
    note: str = ""


@dataclass
class Ledger:
    """Every task this run declared, in declaration order."""

    tasks: list[Task] = field(default_factory=list)

    def add(self, description: str) -> Task:
        """Append a task and return it. Ids are `t1`, `t2`, ... -- short
        enough to quote in a prompt and stable for the run's lifetime."""
        task = Task(id=f"t{len(self.tasks) + 1}", description=description)
        self.tasks.append(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return next((task for task in self.tasks if task.id == task_id), None)

    def next_pending(self) -> Task | None:
        return next((task for task in self.tasks if task.status is TaskStatus.PENDING), None)

    def counts(self) -> dict[str, int]:
        """Requested, and the terminal buckets. They always add up.

        `pending` is non-zero only when the run stopped early, which is
        precisely when the user most needs to see it (spec §6).
        """
        tally = {status.value: 0 for status in TaskStatus}
        for task in self.tasks:
            tally[task.status.value] += 1
        return {
            "requested": len(self.tasks),
            "done": tally[TaskStatus.DONE.value],
            "blocked": tally[TaskStatus.BLOCKED.value],
            "dropped": tally[TaskStatus.DROPPED.value],
            "pending": tally[TaskStatus.PENDING.value] + tally[TaskStatus.IN_PROGRESS.value],
        }

    def save(self, path: Path) -> None:
        """Write atomically: temp file, then os.replace.

        A crash mid-write must leave the previous ledger readable rather
        than a truncated one. This file is the only record of how far a run
        got, and it is written after every status change.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": [{**asdict(task), "status": task.status.value} for task in self.tasks]}
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        """Read a ledger, or an empty one when the file is absent."""
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        tasks = [
            Task(
                id=entry["id"],
                description=entry["description"],
                status=TaskStatus(entry["status"]),
                attempts=entry.get("attempts", 0),
                files_touched=tuple(entry.get("files_touched", ())),
                last_signature=entry.get("last_signature"),
                note=entry.get("note", ""),
            )
            for entry in payload.get("tasks", [])
        ]
        return cls(tasks=tasks)


__all__ = ["Ledger", "Task", "TaskStatus"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_ledger.py -q`
Expected: PASS, 11 tests

- [ ] **Step 6: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

If `test_rudra_dir_migration.py` fails on the new `RudraPaths` field, that is a
stale expectation rather than a defect — it asserts the layout. Update it and say
so in the commit.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/loop/ src/rudra/state/paths.py tests/test_loop_ledger.py
git commit -m "feat(loop): the task ledger

A task is work, not a filename. Saves atomically so a crash leaves a
readable record. counts() always accounts for every task, which is A1.25
in its smallest form.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Bounds

**Files:**
- Create: `src/rudra/loop/bounds.py`
- Test: `tests/test_loop_bounds.py`

**Interfaces:**
- Consumes: `VerifyReport`, `StageResult`, `Finding` from `rudra.verify.result` (types only — no Rudra behaviour).
- Produces: `failure_signature(report: VerifyReport) -> str`; `tests_produced_no_judgement(report: VerifyReport) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_bounds.py`:

```python
"""Failure signatures: what counts as 'the same failure twice'."""

from __future__ import annotations

from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.verify.result import (
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


def report_with(*stages):
    return VerifyReport.from_stages(stages)


def failing(name="typecheck", findings=(), detail="", tail=""):
    return StageResult(
        name=name,
        outcome=FAILED,
        blocking=True,
        findings=tuple(findings),
        detail=detail,
        output_tail=tail,
    )


def test_the_same_failure_gives_the_same_signature():
    findings = (Finding("a.py", 3, "bad type"),)
    first = report_with(failing(findings=findings))
    second = report_with(failing(findings=findings))
    assert failure_signature(first) == failure_signature(second)


def test_the_output_tail_is_excluded():
    # It carries durations, temp paths and pytest's ordering seed, so
    # including it would mean two identical failures rarely match and the
    # no-progress detector would never fire (spec S9c.3).
    findings = (Finding("a.py", 3, "bad type"),)
    quick = report_with(failing(findings=findings, tail="ran in 0.01s /tmp/aaa"))
    slow = report_with(failing(findings=findings, tail="ran in 9.40s /tmp/zzz"))
    assert failure_signature(quick) == failure_signature(slow)


def test_fixing_one_of_several_findings_changes_the_signature():
    four = [Finding("a.py", n, "bad type") for n in (1, 2, 3, 4)]
    before = report_with(failing(findings=four))
    after = report_with(failing(findings=four[:3]))
    assert failure_signature(before) != failure_signature(after)


def test_moving_a_defect_counts_as_progress():
    before = report_with(failing(findings=(Finding("a.py", 3, "bad type"),)))
    after = report_with(failing(findings=(Finding("a.py", 9, "bad type"),)))
    assert failure_signature(before) != failure_signature(after)


def test_finding_order_does_not_matter():
    one = Finding("a.py", 1, "x")
    two = Finding("b.py", 2, "y")
    assert failure_signature(report_with(failing(findings=(one, two)))) == failure_signature(
        report_with(failing(findings=(two, one)))
    )


def test_a_different_stage_is_a_different_signature():
    findings = (Finding("a.py", 3, "boom"),)
    assert failure_signature(
        report_with(failing(name="typecheck", findings=findings))
    ) != failure_signature(report_with(failing(name="stubs", findings=findings)))


def test_without_findings_the_detail_carries_the_signature():
    # e.g. syntax reports "3 file(s) do not parse" and no Finding list.
    three = report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    two = report_with(failing(name="syntax", detail="2 file(s) do not parse"))
    assert failure_signature(three) != failure_signature(two)
    assert failure_signature(three) == failure_signature(
        report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    )


def test_a_passing_report_has_no_signature():
    passed = report_with(StageResult(name="syntax", outcome=PASSED, blocking=True))
    assert failure_signature(passed) is None


def test_no_test_judgement_is_detected():
    report = report_with(
        StageResult(name="syntax", outcome=PASSED, blocking=True),
        StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            detail="no tests were collected",
        ),
    )
    assert tests_produced_no_judgement(report) is True


def test_a_passing_test_stage_is_a_judgement():
    report = report_with(StageResult(name="test", outcome=PASSED, blocking=True))
    assert tests_produced_no_judgement(report) is False


def test_an_absent_test_stage_is_not_a_missing_judgement():
    # The pipeline short-circuited before tests. That is a blocked run, not
    # a project without tests -- dispatching the tester would be wrong.
    report = report_with(failing(name="syntax"))
    assert tests_produced_no_judgement(report) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_loop_bounds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.loop.bounds'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/loop/bounds.py`:

```python
"""When the loop is stuck, and when it should ask for tests.

Pure functions over a VerifyReport. No I/O, no model, nothing from Rudra
except the report types -- so a test can drive every branch directly.
"""

from __future__ import annotations

from hashlib import sha256

from rudra.verify.result import NOT_APPLICABLE, VerifyReport

_SIGNATURE_LENGTH = 16


def failure_signature(report: VerifyReport) -> str | None:
    """A stable fingerprint of *what is wrong*, ignoring how it was said.

    Two identical signatures in a row mean the last attempt changed nothing
    the gate can see -- C6.5a's no-progress rule.

    `output_tail` is deliberately excluded: it carries durations, absolute
    temp paths, and pytest's ordering seed, so hashing it would almost
    never match and the detector would never fire. 9a already parses tool
    output into structured Findings, which is what makes this stable.

    A changed line number counts as progress, correctly: the model moved
    the defect rather than leaving it where it was.

    Returns None for a report with no blocker -- there is nothing to be
    stuck on.
    """
    blocker = report.blocker
    if blocker is None:
        return None

    if blocker.findings:
        parts = sorted(
            f"{finding.file}:{finding.line}:{finding.message}" for finding in blocker.findings
        )
    else:
        parts = [blocker.detail]

    digest = sha256("\n".join([blocker.name, *parts]).encode("utf-8"))
    return digest.hexdigest()[:_SIGNATURE_LENGTH]


def tests_produced_no_judgement(report: VerifyReport) -> bool:
    """Did the test stage run and decline to say anything?

    True only when the stage is present and `not_applicable` -- no test
    command declared, or nothing collected. That is the one case where
    "no tests" is a gap the tester subagent can fill.

    An *absent* test stage means the pipeline short-circuited before
    reaching it, which is a blocked run rather than a project without
    tests; dispatching a tester there would be answering the wrong
    question.
    """
    stage = next((stage for stage in report.stages if stage.name == "test"), None)
    return stage is not None and stage.outcome == NOT_APPLICABLE


__all__ = ["failure_signature", "tests_produced_no_judgement"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_bounds.py -q`
Expected: PASS, 11 tests

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/rudra/loop/bounds.py tests/test_loop_bounds.py
git commit -m "feat(loop): failure signatures and the no-tests predicate

The signature hashes the blocking stage plus its structured findings and
excludes output_tail, which never repeats. An absent test stage is not a
missing judgement -- that is a short-circuit, not a project without tests.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Ledger tools, and the permission gate

**Files:**
- Create: `src/rudra/loop/tools.py`
- Modify: `src/rudra/permissions/rules.py:26-35`
- Test: `tests/test_loop_tools.py`

**Interfaces:**
- Consumes: `Ledger`, `TaskStatus` (Task 1).
- Produces: `create_ledger_tools(ledger: Ledger, path: Path) -> list` returning three LangChain tools named `add_tasks`, `drop_task`, `read_ledger`.

**Why the permission change is in this task, not a later one:** `permissions/rules.py:29` lists `update_plan` and `write_task_assignment` as control-plane tools that are never gated, because gating Rudra's own bookkeeping would make `mode = "ask"` prompt for it. The new tools replace them and need the same treatment. Ship them together or the first `add_tasks` call prompts the user.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_tools.py`:

```python
"""The three ledger tools -- and the one they deliberately are not."""

from __future__ import annotations

import json

from rudra.loop.ledger import Ledger, TaskStatus
from rudra.loop.tools import create_ledger_tools


def tools_for(tmp_path, ledger=None):
    ledger = ledger if ledger is not None else Ledger()
    path = tmp_path / "ledger.json"
    return {tool.name: tool for tool in create_ledger_tools(ledger, path)}, ledger, path


def test_exactly_three_tools(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert set(tools) == {"add_tasks", "drop_task", "read_ledger"}


def test_no_tool_can_produce_a_terminal_engine_status(tmp_path):
    # The invariant that enforces D9: only engine.py may write DONE or
    # BLOCKED. Enumerating the schemas means a fourth tool added later
    # cannot quietly grant it.
    tools, _, _ = tools_for(tmp_path)
    for tool in tools.values():
        schema = json.dumps(tool.args_schema.model_json_schema()).lower()
        assert "done" not in schema
        assert "blocked" not in schema


def test_add_tasks_appends_and_persists(tmp_path):
    tools, ledger, path = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["write the parser", "write its tests"]})
    assert [task.description for task in ledger.tasks] == [
        "write the parser",
        "write its tests",
    ]
    assert Ledger.load(path).tasks == ledger.tasks


def test_add_tasks_reports_the_ids_it_assigned(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    result = tools["add_tasks"].invoke({"descriptions": ["a", "b"]})
    assert "t1" in result
    assert "t2" in result


def test_add_tasks_rejects_an_empty_list(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    result = tools["add_tasks"].invoke({"descriptions": []})
    assert "REJECTED" in result
    assert ledger.tasks == []


def test_add_tasks_ignores_blank_descriptions(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["real work", "   ", ""]})
    assert [task.description for task in ledger.tasks] == ["real work"]


def test_drop_task_marks_and_records_the_reason(tmp_path):
    tools, ledger, path = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    tools["drop_task"].invoke({"task_id": "t1", "reason": "already handled by t0"})
    assert ledger.get("t1").status is TaskStatus.DROPPED
    assert "already handled" in ledger.get("t1").note
    assert Ledger.load(path).get("t1").status is TaskStatus.DROPPED


def test_drop_task_refuses_an_unknown_id(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t9", "reason": "x"})


def test_drop_task_refuses_a_finished_task(tmp_path):
    # Dropping something the gate already passed would rewrite history.
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    ledger.get("t1").status = TaskStatus.DONE
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t1", "reason": "x"})
    assert ledger.get("t1").status is TaskStatus.DONE


def test_drop_task_requires_a_reason(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t1", "reason": "  "})
    assert ledger.get("t1").status is TaskStatus.PENDING


def test_read_ledger_shows_every_task_and_its_status(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a", "b"]})
    ledger.get("t1").status = TaskStatus.DONE
    rendered = tools["read_ledger"].invoke({})
    assert "t1" in rendered and "done" in rendered
    assert "t2" in rendered and "pending" in rendered


def test_read_ledger_says_so_when_empty(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert "no tasks" in tools["read_ledger"].invoke({}).lower()
```

Add to `tests/test_permissions_rules.py`, replacing the body of the control-plane
test at `:100-101`:

```python
def test_control_plane_tools_are_never_gated(tmp_path: Path):
    """The ledger tools write only under .rudra/run/ (spec §4.6).

    Step 9c replaced update_plan / write_task_assignment with add_tasks /
    drop_task. Gating them would make mode="ask" prompt for Rudra's own
    bookkeeping on every planner turn.
    """
    for tool in ("add_tasks", "drop_task", "ask_user"):
        assert _engine(tmp_path).decide(tool, {}).effect == "allow"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop_tools.py tests/test_permissions_rules.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.loop.tools'`, and the
permissions test fails because `add_tasks` is not yet control plane.

- [ ] **Step 3: Write the tools**

Create `src/rudra/loop/tools.py`:

```python
"""The agent's three ledger tools.

Typed arguments, so the model never emits JSON syntax. That is what lets
the ledger be JSON without reintroducing the parsing problem PLAN.md had:
the format the model writes and the format Rudra reads stopped being the
same artefact (C6.10).

There is deliberately no tool that sets DONE or BLOCKED. Only
loop/engine.py writes those, and DONE only when VerifyReport.passed --
which is how D9's "no LLM judge decides termination" is made structural
rather than a request in a prompt.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from rudra.loop.ledger import Ledger, TaskStatus

# Statuses a task can no longer be dropped out of: the gate has already
# ruled, and letting the model retract that would rewrite history.
_SETTLED = frozenset({TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED})


def _render(ledger: Ledger) -> str:
    if not ledger.tasks:
        return "The ledger has no tasks yet."
    lines = [f"{task.id}  [{task.status.value}]  {task.description}" for task in ledger.tasks]
    return "\n".join(lines)


def create_ledger_tools(ledger: Ledger, path: Path) -> list:
    """Tools bound to one run's ledger. Every mutation persists immediately.

    Args:
        ledger: The live Ledger the engine also reads. Same object, not a
            copy -- the agent and the loop must not diverge.
        path: Where to persist after each change.
    """

    @tool
    def add_tasks(descriptions: list[str]) -> str:
        """Add tasks to the ledger.

        Each description is one unit of work, in plain language — not a
        filename. Good: "write a CSV parser that handles quoted commas".
        Also good: "write tests for the parser". Bad: "parser.py".

        Call this once with every task you can foresee. You can call it
        again later if you discover more.

        Args:
            descriptions: One entry per task.

        Returns:
            The ids assigned, so you can refer to them later.
        """
        wanted = [text.strip() for text in descriptions if text and text.strip()]
        if not wanted:
            return "REJECTED: give at least one non-empty task description."
        added = [ledger.add(text) for text in wanted]
        ledger.save(path)
        listed = ", ".join(f"{task.id} ({task.description})" for task in added)
        return f"Added {len(added)} task(s): {listed}"

    @tool
    def drop_task(task_id: str, reason: str) -> str:
        """Retract a task that should not be done after all.

        Use this when a task turned out to be unnecessary or wrong — not
        when it is merely hard. A dropped task is reported to the user
        along with your reason.

        You cannot drop a task that has already finished.

        Args:
            task_id: The id, e.g. "t2".
            reason: Why it should not be done. Required.

        Returns:
            Confirmation, or REJECTED and why.
        """
        if not reason.strip():
            return "REJECTED: dropping a task requires a reason."
        task = ledger.get(task_id)
        if task is None:
            known = ", ".join(item.id for item in ledger.tasks) or "none"
            return f"REJECTED: no task '{task_id}'. Known ids: {known}."
        if task.status in _SETTLED:
            return f"REJECTED: {task_id} is already {task.status.value}."
        task.status = TaskStatus.DROPPED
        task.note = f"dropped: {reason.strip()}"
        ledger.save(path)
        return f"Dropped {task_id}."

    @tool
    def read_ledger() -> str:
        """Show every task and its current status.

        Returns:
            One line per task: id, status, description.
        """
        return _render(ledger)

    return [add_tasks, drop_task, read_ledger]


__all__ = ["create_ledger_tools"]
```

- [ ] **Step 4: Make the tools control plane**

In `src/rudra/permissions/rules.py`, replace lines 26-35:

```python
# Rudra's own orchestration tools. They write only under .rudra/run/ and are
# how the loop functions; gating them would make mode="ask" prompt for
# Rudra's own bookkeeping (spec §4.6). Step 9c replaced update_plan and
# write_task_assignment with the ledger tools that superseded them.
CONTROL_PLANE_TOOLS = frozenset({"add_tasks", "drop_task", "ask_user"})

# `read_ledger` belongs here, not in the control plane: it only reads
# .rudra/run/ledger.json. Its predecessor `read_plan` was in neither set
# until Step 8, which made it the one instance of A1.53 reachable today --
# decided "ask", no interrupt registered, so it ran unprompted and
# unaudited.
READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep", "read_ledger"})
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_loop_tools.py tests/test_permissions_rules.py -q
uv run pytest -q
```

Other permission tests reference `update_plan` and `read_plan`
(`test_permissions_audit.py:92`, `test_permissions_middleware.py:143-151`,
`test_permissions_unknown_tool.py:80-88`). Update those names to `add_tasks` and
`read_ledger` — the tools are renamed, not the behaviour. If any fails for a
*different* reason, stop and log it in `TODO.md` before touching it.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
git add src/rudra/loop/tools.py src/rudra/permissions/rules.py tests/
git commit -m "feat(loop): the three ledger tools, and their permission status

Typed arguments, so the model never writes JSON. No tool can produce DONE
or BLOCKED -- asserted by enumerating the schemas, so a fourth tool
cannot quietly grant it.

The permission change ships in the same commit on purpose: rules.py named
update_plan and write_task_assignment as ungated control plane, and
without the rename every add_tasks call would prompt under ask.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: The fix loop (`run_task`)

**Files:**
- Create: `src/rudra/loop/engine.py`
- Modify: `src/rudra/config/schema.py`
- Test: `tests/test_loop_engine.py`

**Interfaces:**
- Consumes: `Ledger`, `Task`, `TaskStatus` (Task 1); `failure_signature`, `tests_produced_no_judgement` (Task 2); `run_subagent(name, prompt, *, context, thread_id=None) -> SubagentResult` and `SubagentContext` (`rudra.subagents`); `verify_project(project_path, *, changed_files, gate, console, cfg) -> VerifyReport` (`rudra.verify`); `status(project_path, *, gate, console, cfg) -> list[FileStatus]` (`rudra.git.core`).
- Produces: `Outcome` (StrEnum: `DONE`, `BLOCKED`, `STOP_RUN`); `async run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome`; `git_snapshot(context: LoopContext) -> frozenset[str] | None`; `changed_since(context: LoopContext, before: frozenset[str] | None) -> tuple[str, ...]`; `LoopContext(subagents, project_path, console, cfg, paths)` — one record bundling the `SubagentContext` with the paths and the console, so `run_task` takes exactly one keyword argument.

- [ ] **Step 1: Add the config key**

In `src/rudra/config/schema.py`, add to the `AgentConfig` dataclass (find it with
`grep -n "class AgentConfig" -A 8 src/rudra/config/schema.py`):

```python
    # How many times the fix loop may retry one task before giving up
    # (C6.5a). Three matches the convention the deleted orchestrator used.
    max_fix_attempts: int = 3
```

Add the matching entry to `DEFAULTS["agent"]` in the same file, and to the
commented template in `src/rudra/config/template.py` beside `verbose`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_loop_engine.py`:

```python
"""The fix loop: every terminal condition in the spec's §5 table."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_task
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.subagents import SubagentResult
from rudra.verify.result import (
    DENIED,
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


@dataclass
class FakeAgentCfg:
    max_fix_attempts: int = 3
    verbose: bool = False


@dataclass
class FakeToolsCfg:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    agent: FakeAgentCfg = field(default_factory=FakeAgentCfg)
    tools: FakeToolsCfg = field(default_factory=FakeToolsCfg)
    models: dict = field(default_factory=dict)


def passing_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="test", outcome=PASSED, blocking=True),
            StageResult(name="stubs", outcome=PASSED, blocking=True),
        ]
    )


def failing_report(findings=(Finding("a.py", 3, "bad type"),)):
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(name="typecheck", outcome=FAILED, blocking=True, findings=findings),
        ]
    )


def escalating_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name="typecheck",
                outcome=DENIED,
                blocking=True,
                escalate=True,
                detail="<auto:shell-not-opted-in>",
            ),
        ]
    )


def no_test_judgement_report():
    return VerifyReport.from_stages(
        [
            StageResult(name="syntax", outcome=PASSED, blocking=True),
            StageResult(
                name="test",
                outcome=NOT_APPLICABLE,
                blocking=True,
                detail="no tests were collected",
            ),
            StageResult(name="stubs", outcome=PASSED, blocking=True),
        ]
    )


@pytest.fixture
def context(tmp_path):
    from rudra.state.paths import rudra_paths

    return LoopContext(
        subagents=object(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(tmp_path),
    )


@pytest.fixture(autouse=True)
def default_fakes(monkeypatch):
    """A coder that always writes something, and a git snapshot that sees it."""
    calls: list[str] = []

    async def coder(name, prompt, *, context, thread_id=None):
        calls.append(name)
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder)
    monkeypatch.setattr(engine, "git_snapshot", lambda ctx: frozenset())
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ("a.py",))
    return calls


async def run_one(context, ledger=None, description="write the parser"):
    ledger = ledger if ledger is not None else Ledger()
    task = ledger.add(description)
    outcome = await run_task(task, ledger, context=context)
    return outcome, task, ledger


async def test_a_passing_gate_marks_the_task_done(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert task.status is TaskStatus.DONE
    assert task.attempts == 1
    assert task.files_touched == ("a.py",)


async def test_an_escalating_gate_stops_the_whole_run(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: escalating_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.STOP_RUN
    assert task.status is not TaskStatus.DONE


async def test_a_subagent_error_stops_the_whole_run(monkeypatch, context):
    async def broken(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, error="no provider package")

    monkeypatch.setattr(engine, "run_subagent", broken)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.STOP_RUN


async def test_a_halted_subagent_is_only_a_failed_attempt(monkeypatch, context):
    reports = [failing_report(), passing_report()]

    async def halting(name, prompt, *, context, thread_id=None):
        return SubagentResult(name=name, text="", ok=False, halted_reason="repeated 3x")

    monkeypatch.setattr(engine, "run_subagent", halting)
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert task.attempts == 2
    assert "repeated 3x" in task.note


async def test_the_same_failure_twice_blocks(monkeypatch, context):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 2, "stop on the second identical signature, not the budget"
    assert "no progress" in task.note


async def test_a_changing_failure_uses_the_whole_budget(monkeypatch, context):
    lines = iter([1, 2, 3, 4, 5])
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "bad type"),)),
    )
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 3
    assert "attempts exhausted" in task.note


async def test_a_coder_that_writes_nothing_is_a_failed_attempt(monkeypatch, context):
    # With no changed files the gate's syntax stage reports "0 files
    # parsed" and stubs "0 changed file(s) scanned", so a green report
    # would mark an untouched task DONE. Spec §5.
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ())
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: passing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.status is not TaskStatus.DONE
    assert "wrote nothing" in task.note


async def test_no_test_judgement_dispatches_the_tester_once(monkeypatch, context, default_fakes):
    reports = [no_test_judgement_report(), passing_report()]
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: reports.pop(0))
    outcome, _, _ = await run_one(context)
    assert outcome is Outcome.DONE
    assert default_fakes == ["coder", "tester"]


async def test_the_tester_is_not_dispatched_twice(monkeypatch, context, default_fakes):
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: no_test_judgement_report())
    outcome, _, _ = await run_one(context)
    assert default_fakes.count("tester") == 1
    assert outcome is Outcome.DONE, "a project with no tests still finishes"


async def test_the_retry_prompt_carries_the_blocker_verbatim(monkeypatch, context):
    prompts: list[str] = []

    async def recorder(name, prompt, *, context, thread_id=None):
        prompts.append(prompt)
        return SubagentResult(name=name, text="ok", ok=True)

    lines = iter([7, 8, 9])
    monkeypatch.setattr(engine, "run_subagent", recorder)
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("models.py", next(lines), "incompatible type"),)),
    )
    await run_one(context)
    assert "models.py:7" in prompts[1]
    assert "incompatible type" in prompts[1]


async def test_each_attempt_gets_its_own_thread(monkeypatch, context):
    threads: list[str] = []

    async def recorder(name, prompt, *, context, thread_id=None):
        threads.append(thread_id)
        return SubagentResult(name=name, text="ok", ok=True)

    lines = iter([1, 2, 3])
    monkeypatch.setattr(engine, "run_subagent", recorder)
    monkeypatch.setattr(
        engine,
        "verify_project",
        lambda *a, **k: failing_report((Finding("a.py", next(lines), "x"),)),
    )
    await run_one(context)
    assert len(set(threads)) == 3


async def test_a_budget_of_one_allows_no_retry(monkeypatch, context):
    context.cfg.agent.max_fix_attempts = 1
    monkeypatch.setattr(engine, "verify_project", lambda *a, **k: failing_report())
    outcome, task, _ = await run_one(context)
    assert outcome is Outcome.BLOCKED
    assert task.attempts == 1
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_loop_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.loop.engine'`

- [ ] **Step 4: Write the engine's task half**

Create `src/rudra/loop/engine.py`:

```python
"""The loop: dispatch work, verify it, fix it, and decide when to stop.

The split this module exists to enforce (spec S9c.1): the model decides
what work exists and what to do next; Python decides when a task is done
and when to stop. C6.1 asked for an agent that owns the todo list; D9
forbids an LLM deciding termination. Both hold here because the ledger
tools cannot write DONE and this module is the only thing that can.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.git.core import is_repo, status
from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.subagents import SubagentContext, run_subagent
from rudra.verify import verify_project
from rudra.verify.stubs import source_files


class Outcome(StrEnum):
    """What running one task told the caller to do next."""

    DONE = "done"
    BLOCKED = "blocked"
    STOP_RUN = "stop_run"


@dataclass
class LoopContext:
    """Everything one run needs, assembled once.

    Bundled for the reason Gate and SubagentContext are: these must be the
    *same* objects across calls, the gate and its session grants
    especially.
    """

    subagents: SubagentContext
    project_path: Path
    console: Console
    cfg: Any
    paths: Any


def git_snapshot(context: LoopContext) -> frozenset[str] | None:
    """Paths git currently reports as changed, or None outside a repo.

    None is a real answer the caller acts on: without git there is no way
    to tell what an attempt touched, so the gate falls back to scanning
    everything -- the same choice `rudra verify` makes.
    """
    subagents = context.subagents
    if not is_repo(
        context.project_path, gate=subagents.gate, console=context.console, cfg=context.cfg
    ):
        return None
    entries = status(
        context.project_path, gate=subagents.gate, console=context.console, cfg=context.cfg
    )
    return frozenset(entry.path for entry in entries if entry.path)


def changed_since(context: LoopContext, before: frozenset[str] | None) -> tuple[str, ...]:
    """What this attempt touched.

    Read from git rather than from the model. Asking the coder what it
    wrote invites a wrong answer at exactly the moment the answer matters,
    because it feeds 9a's stub scan.
    """
    if before is None:
        return source_files(context.project_path)
    after = git_snapshot(context)
    if after is None:  # pragma: no cover - a repo cannot stop being one mid-run
        return source_files(context.project_path)
    return tuple(sorted(after - before))


def _coder_prompt(task: Task, blocker_text: str = "") -> str:
    prompt = (
        f"Task {task.id}: {task.description}\n\n"
        "Write every file this task needs. Stop when the task is complete."
    )
    if blocker_text:
        prompt += (
            "\n\nYour previous attempt did not pass verification. "
            "Fix exactly this, then stop:\n\n" + blocker_text
        )
    return prompt


def _tester_prompt(task: Task) -> str:
    files = ", ".join(task.files_touched) or "the code this project contains"
    return (
        f"The project has no tests covering recent work on: {files}\n\n"
        f"That work was: {task.description}\n\n"
        "Write tests for it, run them, and report what happened."
    )


def _blocker_text(report) -> str:
    """The gate's complaint, verbatim -- a paraphrase is a worse input."""
    blocker = report.blocker
    if blocker is None:  # pragma: no cover - only called on a failure
        return ""
    lines = [f"{blocker.name} failed: {blocker.detail}".rstrip(": ")]
    lines.extend(
        f"  {finding.file}:{finding.line}: {finding.message}" for finding in blocker.findings
    )
    if not blocker.findings and blocker.output_tail:
        lines.append(blocker.output_tail)
    return "\n".join(lines)


async def run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
    """Write, verify, fix, reverify -- until the gate passes or we stop.

    Only this function writes DONE, and only on VerifyReport.passed.
    """
    tested = False
    blocker_text = ""

    while task.attempts < context.cfg.agent.max_fix_attempts:
        task.attempts += 1
        task.status = TaskStatus.IN_PROGRESS
        ledger.save(context.paths.ledger_json)

        before = git_snapshot(context)
        result = await run_subagent(
            "coder",
            _coder_prompt(task, blocker_text),
            context=context.subagents,
            thread_id=f"{context.subagents.session_id}-{task.id}-a{task.attempts}",
        )

        if result.error:
            # Never ran: a build failure or a mid-stream exception. Both are
            # environment-class and would recur on every remaining task.
            task.note = f"the coder could not run: {result.error}"
            ledger.save(context.paths.ledger_json)
            return Outcome.STOP_RUN

        if result.halted_reason:
            # A guard fired. Recorded for the summary; the gate below is what
            # says how badly the attempt actually went.
            task.note = result.halted_reason

        task.files_touched = changed_since(context, before)
        if before is not None and not task.files_touched:
            task.note = "the coder wrote nothing"
            ledger.save(context.paths.ledger_json)
            continue

        report = verify_project(
            context.project_path,
            changed_files=task.files_touched,
            gate=context.subagents.gate,
            console=context.console,
            cfg=context.cfg,
        )

        if report.escalate:
            task.note = _blocker_text(report)
            ledger.save(context.paths.ledger_json)
            return Outcome.STOP_RUN

        if report.passed:
            if not tested and tests_produced_no_judgement(report):
                tested = True
                await run_subagent(
                    "tester",
                    _tester_prompt(task),
                    context=context.subagents,
                    thread_id=f"{context.subagents.session_id}-{task.id}-tester",
                )
                continue  # re-verify with whatever the tester wrote
            task.status = TaskStatus.DONE
            task.note = ""
            ledger.save(context.paths.ledger_json)
            return Outcome.DONE

        signature = failure_signature(report)
        blocker_text = _blocker_text(report)
        if signature is not None and signature == task.last_signature:
            task.status = TaskStatus.BLOCKED
            task.note = "no progress: the same failure twice"
            ledger.save(context.paths.ledger_json)
            return Outcome.BLOCKED
        task.last_signature = signature

    task.status = TaskStatus.BLOCKED
    if "wrote nothing" not in task.note:
        task.note = f"{task.attempts} attempts exhausted"
    ledger.save(context.paths.ledger_json)
    return Outcome.BLOCKED


__all__ = ["LoopContext", "Outcome", "changed_since", "git_snapshot", "run_task"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_engine.py -q`
Expected: PASS, 12 tests

- [ ] **Step 6: Run the gates and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
git add src/rudra/loop/engine.py src/rudra/config/ tests/test_loop_engine.py
git commit -m "feat(loop): the fix loop

write -> verify -> diagnose -> fix -> reverify, with the gate as the only
thing that grants DONE. Stops on an escalating gate or a subagent that
never ran; retries a subagent that merely misbehaved.

An empty diff is a failed attempt, not a pass: with no changed files the
gate reports '0 files parsed' and would mark an untouched task done.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: The outer loop and the summary

**Files:**
- Modify: `src/rudra/loop/engine.py`, `src/rudra/loop/__init__.py`
- Test: `tests/test_loop_run.py`, `tests/test_loop_summary.py`

**Interfaces:**
- Consumes: everything from Task 4.
- Produces: `async run_loop(request: str, *, context: LoopContext, planner) -> AgentResult`; `summarise(ledger, console) -> AgentResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_summary.py`:

```python
"""The summary, where A1.25 dies."""

from __future__ import annotations

from rich.console import Console

from rudra.loop.engine import summarise
from rudra.loop.ledger import Ledger, TaskStatus


def ledger_with(*statuses):
    ledger = Ledger()
    for index, status in enumerate(statuses, start=1):
        task = ledger.add(f"task {index}")
        task.status = status
        if status is TaskStatus.BLOCKED:
            task.note = "no progress: the same failure twice"
    return ledger


def render(ledger):
    console = Console(record=True, width=100, quiet=False)
    result = summarise(ledger, console)
    return result, console.export_text()


def test_every_task_appears_in_the_output():
    # A1.25: a requested item must never be invisible. There is no filter
    # between what was declared and what is reported.
    ledger = ledger_with(
        TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED, TaskStatus.PENDING
    )
    _, text = render(ledger)
    for task in ledger.tasks:
        assert task.id in text


def test_the_counts_always_add_up():
    ledger = ledger_with(
        TaskStatus.DONE, TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED
    )
    counts = ledger.counts()
    assert (
        counts["done"] + counts["blocked"] + counts["dropped"] + counts["pending"]
        == counts["requested"]
    )


def test_unattempted_tasks_are_named_when_the_run_stopped_early():
    # The case the user most needs: they would otherwise assume the rest
    # simply passed.
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.PENDING)
    _, text = render(ledger)
    assert "never attempted" in text


def test_a_blocked_task_makes_the_run_unsuccessful():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DONE, TaskStatus.BLOCKED))
    assert result.success is False


def test_all_done_is_a_success():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DONE))
    assert result.success is True


def test_dropping_everything_is_not_a_success():
    result, _ = render(ledger_with(TaskStatus.DROPPED, TaskStatus.DROPPED))
    assert result.success is False


def test_done_alongside_dropped_is_a_success():
    result, _ = render(ledger_with(TaskStatus.DONE, TaskStatus.DROPPED))
    assert result.success is True


def test_an_empty_ledger_is_not_a_success():
    result, _ = render(Ledger())
    assert result.success is False


def test_the_reason_is_shown_for_anything_not_done():
    _, text = render(ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED))
    assert "no progress" in text


def test_iterations_counts_attempted_tasks():
    ledger = ledger_with(TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.PENDING)
    for task in ledger.tasks:
        task.attempts = 1 if task.status is not TaskStatus.PENDING else 0
    result, _ = render(ledger)
    assert result.iterations == 2
```

Create `tests/test_loop_run.py`:

```python
"""The outer loop: when the planner is consulted, and when the run stops."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_loop
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.state.paths import rudra_paths


@dataclass
class FakeAgentCfg:
    max_fix_attempts: int = 3
    verbose: bool = False


@dataclass
class FakeToolsCfg:
    test_timeout: int = 600


@dataclass
class FakeCfg:
    agent: FakeAgentCfg = field(default_factory=FakeAgentCfg)
    tools: FakeToolsCfg = field(default_factory=FakeToolsCfg)
    models: dict = field(default_factory=dict)


@pytest.fixture
def context(tmp_path):
    return LoopContext(
        subagents=object(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(tmp_path),
    )


class FakePlanner:
    """Records why it was consulted; adds whatever it was scripted to add."""

    def __init__(self, script=None):
        self.reasons: list[str] = []
        self.script = list(script or [])

    async def __call__(self, ledger, request, *, reason, task=None):
        self.reasons.append(reason)
        if self.script:
            for description in self.script.pop(0):
                ledger.add(description)


async def test_the_planner_is_consulted_first(monkeypatch, context):
    planner = FakePlanner([["a"]])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner)
    assert planner.reasons[0] == "initial"


async def test_no_consult_after_an_ordinary_success(monkeypatch, context):
    # This is what bounds the design's model-call cost.
    planner = FakePlanner([["a", "b"], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE, Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner)
    assert planner.reasons == ["initial", "ledger_empty"]


async def test_a_blocked_task_consults_the_planner(monkeypatch, context):
    planner = FakePlanner([["a"], [], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.BLOCKED))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner)
    assert "blocked" in planner.reasons


async def test_stop_run_ends_immediately_and_leaves_tasks_pending(monkeypatch, context):
    planner = FakePlanner([["a", "b", "c"]])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.STOP_RUN))
    monkeypatch.setattr(engine, "review_once", _noop)
    result = await run_loop("build it", context=context, planner=planner)
    assert result.success is False
    assert planner.reasons == ["initial"], "no consult after a stop"


async def test_the_ledger_empty_consult_happens_once(monkeypatch, context):
    planner = FakePlanner([["a"], [], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner)
    assert planner.reasons.count("ledger_empty") == 1


async def test_a_planner_that_adds_more_work_keeps_the_loop_going(monkeypatch, context):
    planner = FakePlanner([["a"], ["b"], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE, Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    result = await run_loop("build it", context=context, planner=planner)
    assert result.iterations == 2


async def test_the_reviewer_runs_once_when_something_landed(monkeypatch, context):
    planner = FakePlanner([["a"], []])
    reviews: list[int] = []

    async def review(ctx):
        reviews.append(1)

    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", review)
    await run_loop("build it", context=context, planner=planner)
    assert len(reviews) == 1


async def test_the_reviewer_is_skipped_when_nothing_landed(monkeypatch, context):
    planner = FakePlanner([["a"], []])
    reviews: list[int] = []

    async def review(ctx):
        reviews.append(1)

    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.BLOCKED))
    monkeypatch.setattr(engine, "review_once", review)
    await run_loop("build it", context=context, planner=planner)
    assert reviews == []


async def test_the_ledger_is_written_to_disk(monkeypatch, context):
    planner = FakePlanner([["a"], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner)
    assert context.paths.ledger_json.is_file()
    assert Ledger.load(context.paths.ledger_json).tasks


def _outcomes(*outcomes):
    remaining = list(outcomes)

    async def fake_run_task(task, ledger, *, context):
        outcome = remaining.pop(0) if remaining else Outcome.DONE
        task.status = {
            Outcome.DONE: TaskStatus.DONE,
            Outcome.BLOCKED: TaskStatus.BLOCKED,
            Outcome.STOP_RUN: TaskStatus.PENDING,
        }[outcome]
        task.attempts = 1
        return outcome

    return fake_run_task


async def _noop(context):
    return None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop_run.py tests/test_loop_summary.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_loop'`

- [ ] **Step 3: Add the outer loop and the summary**

Append to `src/rudra/loop/engine.py`.

`summarise` returns an `AgentResult`, which lives in `main_agent.py` — and
`main_agent.py` imports `run_loop` from here, so a module-level import would be a
cycle. Import it **inside** `summarise`, which is the pattern this codebase
already uses for exactly this (`main_agent.build_backend` imports its backends
locally, `main_agent.py:592-594`). Do not move `AgentResult`: `cli.py:19` imports
it from `rudra.agent` and that is its home.

```python
_STATUS_MARK = {
    TaskStatus.DONE: "[green]✓[/green]",
    TaskStatus.BLOCKED: "[red]✗[/red]",
    TaskStatus.DROPPED: "[yellow]–[/yellow]",
    TaskStatus.PENDING: "[dim]·[/dim]",
    TaskStatus.IN_PROGRESS: "[dim]·[/dim]",
}

_NOT_ATTEMPTED = "never attempted — the run stopped"


async def review_once(context: LoopContext) -> None:
    """One advisory pass over everything that changed. Printed, never acted on.

    D9's split: the deterministic gate decides done-or-not; the reviewer
    comments on quality and gates nothing.
    """
    result = await run_subagent(
        "reviewer",
        "Review the working-tree changes from this run and report any problems.",
        context=context.subagents,
        thread_id=f"{context.subagents.session_id}-review",
    )
    if result.text.strip():
        context.console.print("\n[bold]Review[/bold] [dim](advisory)[/dim]")
        context.console.print(result.text)


def summarise(ledger: Ledger, console: Console) -> Any:
    """Print every task and return the run's result.

    A1.25 died here: there is no filter between what was declared and what
    is reported, and a task that was never attempted says so rather than
    vanishing.
    """
    from rudra.agent.main_agent import AgentResult

    counts = ledger.counts()
    headline = (
        f"Tasks: {counts['requested']} requested · {counts['done']} done · "
        f"{counts['blocked']} blocked · {counts['dropped']} dropped"
    )
    if counts["pending"]:
        headline += f" · {counts['pending']} never attempted"
    console.print(f"\n[bold]{headline}[/bold]\n")

    for task in ledger.tasks:
        note = task.note
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            note = _NOT_ATTEMPTED
        mark = _STATUS_MARK[task.status]
        console.print(f"  {mark} {task.id}  {task.description}")
        if note:
            console.print(f"      [dim]{note}[/dim]")

    files: list[str] = []
    for task in ledger.tasks:
        files.extend(task.files_touched)

    success = counts["requested"] > 0 and counts["done"] > 0 and not (
        counts["blocked"] or counts["pending"]
    )
    return AgentResult(
        success=success,
        message=headline,
        files_created=sorted(set(files)),
        files_modified=[],
        iterations=sum(1 for task in ledger.tasks if task.attempts > 0),
    )


async def run_loop(request: str, *, context: LoopContext, planner: Any) -> Any:
    """Plan, work, verify, and stop. The whole run.

    `planner` is an awaitable called as
    `planner(ledger, request, reason=..., task=...)`; it adds or drops
    tasks through the ledger tools and returns nothing. Injected rather
    than constructed here so the loop is testable without a model.
    """
    ledger = Ledger()
    ledger.save(context.paths.ledger_json)
    await planner(ledger, request, reason="initial")
    consulted_on_empty = False

    while True:
        task = ledger.next_pending()
        if task is None:
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, reason="ledger_empty")
            continue

        outcome = await run_task(task, ledger, context=context)
        ledger.save(context.paths.ledger_json)

        if outcome is Outcome.STOP_RUN:
            context.console.print(
                "\n[bold red]Run stopped early.[/bold red] "
                f"[dim]{task.note}[/dim]"
            )
            break
        if outcome is Outcome.BLOCKED:
            # Only a stall consults the planner -- never an ordinary success.
            consulted_on_empty = False
            await planner(ledger, request, reason="blocked", task=task)

    if any(task.status is TaskStatus.DONE for task in ledger.tasks):
        await review_once(context)
    return summarise(ledger, context.console)
```

Update `src/rudra/loop/__init__.py`:

```python
"""The agentic loop: a task ledger, a fix loop, and a deterministic gate (Step 9c).

The model decides what work exists and what to do next; Python decides
when a task is done and when to stop. C6.1 wanted the agent to own the
todo list; D9 forbids an LLM deciding termination. Both hold because the
ledger tools cannot write DONE and loop/engine.py is the only thing that
can.
"""

from __future__ import annotations

from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.engine import LoopContext, Outcome, run_loop, run_task, summarise
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.tools import create_ledger_tools

__all__ = [
    "Ledger",
    "LoopContext",
    "Outcome",
    "Task",
    "TaskStatus",
    "create_ledger_tools",
    "failure_signature",
    "run_loop",
    "run_task",
    "summarise",
    "tests_produced_no_judgement",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_run.py tests/test_loop_summary.py -q`
Expected: PASS, 19 tests

- [ ] **Step 5: Run the gates and commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
git add src/rudra/loop/ tests/test_loop_run.py tests/test_loop_summary.py
git commit -m "feat(loop): the outer loop and the summary

The planner is consulted on stalls, never after an ordinary success --
that is what bounds this design's model-call cost. A stop leaves tasks
pending and the summary names them as never attempted, which is the case
a user is most likely to misread as success.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: The planner and the coder prompt

**Files:**
- Modify: `src/rudra/agent/planner_agent.py`, `src/rudra/subagents/registry.py`
- Test: `tests/test_planner_ledger.py`

**Interfaces:**
- Consumes: `create_ledger_tools` (Task 3).
- Produces: `create_planner_agent(...)` wired to the ledger tools; `consult_planner(...)` — the awaitable `run_loop` takes as `planner`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_ledger.py`:

```python
"""The planner declares work; it cannot declare anything finished."""

from __future__ import annotations

from rudra.agent.planner_agent import build_planner_prompt
from rudra.loop.ledger import Ledger
from rudra.loop.tools import create_ledger_tools


def test_the_prompt_names_no_rudra_paths(tmp_path):
    # The ledger is reached through tools, so the prompt has no path to
    # drift from (CLAUDE.md §3).
    prompt = build_planner_prompt("build a parser", tmp_path, "")
    assert "PLAN.md" not in prompt
    assert "current_task.md" not in prompt
    assert ".rudra" not in prompt


def test_the_prompt_asks_for_work_not_filenames(tmp_path):
    prompt = build_planner_prompt("build a parser", tmp_path, "").lower()
    assert "add_tasks" in prompt
    assert "filename" in prompt  # it says NOT to use them


def test_the_prompt_says_it_cannot_mark_anything_done(tmp_path):
    prompt = build_planner_prompt("build a parser", tmp_path, "").lower()
    assert "done" in prompt
    assert "verification" in prompt or "gate" in prompt


def test_the_planner_gets_exactly_the_ledger_tools(tmp_path):
    names = {tool.name for tool in create_ledger_tools(Ledger(), tmp_path / "l.json")}
    assert names == {"add_tasks", "drop_task", "read_ledger"}


def test_the_coder_prompt_no_longer_demands_a_single_file():
    from rudra.subagents.registry import CODER

    prompt = CODER.system_prompt.lower()
    assert "one file" not in prompt
    assert "every file" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_planner_ledger.py -q`
Expected: FAIL — the prompt still mentions `PLAN.md`.

- [ ] **Step 3: Rewrite the planner prompt and its tools**

In `src/rudra/agent/planner_agent.py`, replace `build_planner_prompt`'s body with:

```python
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: decide WHAT WORK the request needs. You never write project code.

## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    if tech_stack_content:
        base += f"\n## Tech Stack\n{tech_stack_content}\n"

    base += f"""
## REQUEST
{task}

## WHAT A TASK IS

A task is a unit of WORK, described in plain language. Not a filename.

  GOOD: "write a CSV parser that handles quoted commas"
  GOOD: "write tests for the parser"
  GOOD: "add a --format flag to the CLI"
  BAD:  "parser.py"
  BAD:  "create the project structure"

One task may touch several files. A task that needs tests is its own task.

## WORKFLOW

1. Call read_file() on anything you need to understand the project
2. Call add_tasks() ONCE with every task you can foresee
3. STOP

You will be consulted again if a task fails or if the work runs out. When
that happens, add a task taking a DIFFERENT approach, or drop_task() one
that turned out to be unnecessary.

## WHAT YOU CANNOT DO

You cannot mark anything done. A deterministic verification gate decides
that — it parses the code, type checks it, runs the tests, and scans for
placeholders. Do not claim a task is complete, and do not add a task whose
description is "verify" or "check" — that already happens.

Do NOT call ask_user() if the request already specifies a framework or
language.
"""
    return base
```

Replace the `custom_tools` assignment (`planner_agent.py:113-118`) with:

```python
    # The ledger replaced PLAN.md and current_task.md (C6.10). git and
    # testing tools are gone from the planner: the loop runs the gate
    # itself, and the tester subagent writes tests (S9c.5).
    custom_tools = create_ledger_tools(ledger, paths.ledger_json) + create_interaction_tools(
        console, project_path
    )
```

Add `ledger` and `paths` to `create_planner_agent`'s signature, and drop the now
unused `create_git_tools` / `create_testing_tools` / `create_planning_tools`
imports.

- [ ] **Step 4: Add the consult function**

Append to `src/rudra/agent/planner_agent.py`:

```python
async def consult_planner(
    agent: Any,
    ledger: Ledger,
    request: str,
    *,
    reason: str,
    task: Any = None,
    gate: Any,
    console: Console,
    session_id: str,
) -> None:
    """Ask the planner to add or drop tasks. It mutates the ledger via tools.

    Four reasons, four messages. There is deliberately no consult after an
    ordinary success -- that is what bounds the loop's model-call cost.
    """
    messages = {
        "initial": f"Break this request into tasks: {request}",
        "ledger_empty": (
            "Every task is finished. Is anything missing before we stop? "
            "Call add_tasks if so; otherwise reply DONE and stop."
        ),
        "blocked": "",  # filled in below -- it needs the task
    }
    if reason == "blocked" and task is not None:
        messages["blocked"] = (
            f"Task {task.id} ({task.description}) failed and was given up on:\n\n"
            f"{task.note}\n\n"
            "Add a task taking a DIFFERENT approach, or drop_task it if it is "
            "not worth doing. If neither, reply DONE and stop."
        )

    await _stream_planner_turn(
        agent,
        messages[reason],
        thread_id=f"{session_id}-planner",
        gate=gate,
        console=console,
    )
```

`_stream_planner_turn` is `RudraAgent._stream_planner` (`main_agent.py:156-235`)
lifted to a module function. Move it here rather than leaving it on the class:
Task 7 deletes the class's loop, and this becomes its only caller. The body is
unchanged — the same `run_with_approvals` stream, the same two guards — only the
signature moves off `self`:

```python
async def _stream_planner_turn(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    gate: Any,
    console: Console,
) -> bool:
    """Stream one planner turn. Returns False if a guard halted it.

    Lifted from RudraAgent._stream_planner in Step 9c. Every `self.gate`
    becomes `gate`, every `self.console` becomes `console`, and
    `self._log_always(...)` becomes `console.print(...)`; the guard logic,
    the MAX_PLANNING_CALLS counter and the consecutive-failure counter are
    copied verbatim. Note it still carries A1.20's remaining half -- one
    `processed` counter across namespaces -- which is re-pointed to C9.1.
    """
```

The watched-tool names in that guard reference `update_plan` and `read_plan`
(`main_agent.py:197-200`). Update them to `add_tasks` and `read_ledger`, or the
loop guard silently stops guarding.

- [ ] **Step 5: Revise the coder prompt**

In `src/rudra/subagents/registry.py`, replace the workflow and stop-condition
sections of `_CODER_PROMPT`:

```python
_CODER_PROMPT = """You are an expert code generator for Rudra.

Your job: complete the ONE TASK you are given, writing every file it needs.

## WORKFLOW
1. Read any files you need for context with read_file()
2. Write every file the task requires: write_file(file_path=..., content=...)
3. STOP

## FILE PATH RULES
- Use RELATIVE paths only: "src/main.rs", "package.json"
- NEVER use absolute paths or paths starting with "/" or a drive letter

## CODE QUALITY RULES
- write_file content MUST be RAW source code -- NEVER wrap it in ```markdown fences```
- Write COMPLETE, working code -- no placeholders, stubs, or TODO comments
- No bare `pass`, no unimplemented methods, no Hello World shortcuts
- All imports at the top of the file

A verification gate checks your work afterwards: it parses every file you
wrote, type checks it, runs the tests, and scans for placeholders. If it
fails you will be asked again with the exact errors, so a stub only costs
a round trip.

## STOP CONDITION
Stop when the task is complete. Do not start work the task did not ask for.
"""
```

- [ ] **Step 6: Run the tests and the gates**

```bash
uv run pytest tests/test_planner_ledger.py tests/test_subagents_registry.py -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run pytest -q
```

`tests/test_agent_wiring.py:278` asserts the planner has `update_plan`,
`read_plan`, `write_task_assignment`. Update it to the ledger tools — a rename,
not a behaviour change.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/agent/planner_agent.py src/rudra/subagents/registry.py tests/
git commit -m "feat(planner): declare work, not filenames

The planner's prompt names no .rudra paths at all now -- the ledger is
reached through tools, so there is no path for the prompt to drift from.
It is also told plainly that it cannot mark anything done.

The coder prompt stops demanding exactly one file, which was correct for
the loop being deleted and wrong for task-shaped work.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Delete the old loop

**Files:**
- Modify: `src/rudra/agent/main_agent.py`, `src/rudra/agent/__init__.py`, `src/rudra/state/paths.py`
- Delete: `src/rudra/tools/planning_tools.py`, `src/rudra/agent/coder_agent.py`, `tests/test_plan_items.py`
- Test: `tests/test_main_agent_helpers.py`, `tests/test_agent_wiring.py`, `tests/test_rudra_dir_migration.py`, `tests/test_no_direct_provider_imports.py`

**Interfaces:**
- Consumes: `run_loop`, `LoopContext` (Task 5); `consult_planner` (Task 6).
- Produces: `RudraAgent.run()` delegating to `run_loop`; `AgentResult` unchanged.

- [ ] **Step 1: Rewrite `RudraAgent.run`**

Replace `RudraAgent.run` (`main_agent.py:362-465`) with:

```python
    async def run(self) -> AgentResult:
        """Plan, work, verify, fix, and report. The whole run (C6.1)."""
        try:
            if self.context.dry_run:
                # A1.40: this still previews nothing. The flag's behaviour is
                # unchanged by Step 9c; the row moved with the code.
                self._status("Dry run — no files written.")
                return AgentResult(
                    success=True,
                    message="Dry run completed (no files written)",
                    files_created=[],
                    files_modified=[],
                )

            # Before the planner, so the plan and every file it produces land
            # on the new branch rather than straddling two.
            _maybe_auto_branch(
                self.context.project_path,
                self.context.task,
                cfg=get_config(),
                gate=self.gate,
                console=self.context.console,
            )

            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")
            return await run_loop(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
            )
        except Exception:
            import traceback

            self._log_always("[bold red]\n!! Agent crashed — full traceback:[/bold red]")
            self._log_always(traceback.format_exc())
            raise
```

`RudraAgent.__init__` gains two arguments, and `create_main_agent` builds them.
In `__init__`, replace the `coder_config` and `plan_path` parameters with:

```python
        loop_context,          # LoopContext
        planner_callback,      # the awaitable run_loop calls as `planner`
```

and store them as `self._loop_context` / `self._planner_callback`.

In `create_main_agent`, after the gate and backend are built, replace the
`coder_config` / `plan_path` block with:

```python
    from rudra.agent.planner_agent import consult_planner, create_planner_agent
    from rudra.loop import Ledger, LoopContext
    from rudra.subagents import SubagentContext

    subagent_context = SubagentContext(
        project_path=project_path,
        backend=filesystem_backend,
        gate=gate,
        console=console,
        cfg=cfg,
        checkpointer=checkpointer,
        session_id=session_id,
    )
    loop_context = LoopContext(
        subagents=subagent_context,
        project_path=project_path,
        console=console,
        cfg=cfg,
        paths=paths,
    )

    # The planner and the loop share one Ledger object, not a copy: the
    # agent adds tasks through its tools and the engine reads them back.
    ledger = Ledger()
    planner = create_planner_agent(
        task=task,
        project_path=project_path,
        tech_stack_content=tech_stack_content,
        filesystem_backend=filesystem_backend,
        checkpointer=checkpointer,
        console=console,
        gate=gate,
        ledger=ledger,
        paths=paths,
    )

    async def planner_callback(run_ledger, request, *, reason, task=None):
        # run_ledger is the same object `ledger` above; the parameter exists
        # so run_loop's contract does not depend on this closure's scope.
        await consult_planner(
            planner,
            run_ledger,
            request,
            reason=reason,
            task=task,
            gate=gate,
            console=console,
            session_id=session_id,
        )
```

Then pass `loop_context=loop_context, planner_callback=planner_callback` to
`RudraAgent(...)`.

**One thing to get right:** `run_loop` creates its own `Ledger()` at the top
(Task 5). That would leave the planner writing into a different object than the
engine reads. Change `run_loop`'s signature to accept the ledger:

```python
async def run_loop(request, *, context, planner, ledger=None):
    ledger = ledger if ledger is not None else Ledger()
```

and pass `ledger=ledger` from `RudraAgent.run`. The tests in Task 5 keep working
unchanged, because they rely on the default.

- [ ] **Step 2: Delete the dead functions**

From `src/rudra/agent/main_agent.py`, delete `_parse_pending_files` (`:48`),
`_check_off_file` (`:64`), `_stream_coder` (`:237`), `_run_planner_phase`
(`:317`), `_request_task_assignment` (`:328`), `_run_coder_for_file` (`:343`),
and `_stream_planner` (`:156`, moved to `planner_agent.py` in Task 6). Remove the
now-unused `looks_like_path` import (`:15`).

- [ ] **Step 3: Delete the superseded modules**

```bash
git rm src/rudra/tools/planning_tools.py src/rudra/agent/coder_agent.py tests/test_plan_items.py
```

Remove `create_coder_agent` from `src/rudra/agent/__init__.py:3,13`, and
`plan_md` / `current_task_md` from `RudraPaths` and `rudra_paths()` in
`src/rudra/state/paths.py` — nothing writes them once `planning_tools` is gone.

`tests/test_plan_items.py` goes wholesale: every subject in it —
`looks_like_path`, `update_plan`, `_parse_pending_files` — no longer exists. Its
one surviving idea, that a plan item must never be silently dropped, is now
`tests/test_loop_summary.py::test_every_task_appears_in_the_output`.

- [ ] **Step 4: Update the tests that referenced deleted things**

- `tests/test_main_agent_helpers.py:22-62` — delete the four `_check_off_file`
  tests and the import. Everything from `:64` down (`_ensure_agents_md`,
  `_write_tech_stack_file`, `_maybe_auto_branch`) stays untouched.
- `tests/test_agent_wiring.py:170-195,287-290` — the `create_coder_agent` cases
  become `build_agent(REGISTRY["coder"], …)` from `rudra.subagents`.
- `tests/test_rudra_dir_migration.py:20-32` — assert `ledger.json` under `run/`
  instead of `PLAN.md` and `current_task.md`.
- `tests/test_no_direct_provider_imports.py:121-123` — swap `create_coder_agent`
  for `build_agent(REGISTRY["coder"], …)`; the test's point is that no module
  imports a provider package directly, which is unchanged.

- [ ] **Step 5: Run everything**

```bash
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: green. The count will **drop** — `test_plan_items.py` and four
`_check_off_file` tests are gone — and then rise past the old floor with Tasks
1-6's additions. Record the real number; do not assume it.

A failure naming something this plan did not list is a finding: stop, log it in
`TODO.md` with `file:line`, then fix it.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(agent): delete the file-by-file loop

RudraAgent.run delegates to run_loop. Gone: _parse_pending_files,
_check_off_file, _run_coder_for_file, _stream_coder,
_request_task_assignment, planning_tools.py, coder_agent.py.

A1.8 dies here -- success is VerifyReport.passed, not os.path.exists.
A1.24 dies by deletion: looks_like_path and the checklist it mis-parsed
are both gone. A1.25 dies in the summary.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Documentation

**Files:**
- Modify: `Documentation/05-how-it-works.md`, `08-project-status.md`, `02-configuration.md`, `README.md`

**Interfaces:** none.

- [ ] **Step 1: Rewrite the loop description**

`Documentation/05-how-it-works.md` currently says a file counts as done when it
exists (`:139`), that running tests does not change this (`:145`), and describes a
planner/coder pair. Replace "The short story" diagram with:

```
you ──▶ planner ──▶ ledger of tasks ──▶ for each task:
                                              │
                                    ┌─────────▼─────────┐
                                    │ coder writes it   │
                                    │ gate verifies it  │
                                    │ fix loop retries  │
                                    └─────────┬─────────┘
                                              ▼
                                   gate passed? task done
```

Replace the whole "How Rudra decides it's finished" section with the five gate
stages, the attempt budget, and the no-progress rule. Update "The `.rudra/`
folder" table: `PLAN.md` and `current_task.md` are gone, `ledger.json` is new.

- [ ] **Step 2: Update the status page**

`Documentation/08-project-status.md` says the gate that acts on test results does
not exist. It does now. Say what is true: the loop verifies, retries, and stops
on no progress; it does not yet resume (`C7.2`), retry a flaky provider
(`A1.39`), or update `AGENTS.md` (`A1.9`).

- [ ] **Step 3: Document the new config key**

In `Documentation/02-configuration.md`'s `[agent]` coverage, add:

```markdown
| `max_fix_attempts` | How many times the fix loop may retry one task before giving up. Default 3 |
```

- [ ] **Step 4: Update the README's honesty note**

`README.md:17` says "nothing loops back to fix a failing test, and a file still
counts as done when it exists". Replace with a description of the loop and its
bounds, and keep the review-everything advice.

- [ ] **Step 5: Commit**

```bash
git add Documentation/ README.md
git commit -m "docs: the loop verifies its own work now

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Ledger

**Files:** `TODO.md`, `CLAUDE.md`

**Interfaces:** none.

- [ ] **Step 1: Close the rows**

Mark `C6.1`, `C6.5`, `C6.5a`, `C6.10`, `A1.8`, `A1.24`, `A1.25` **DONE
2026-08-12**, each with evidence: module paths, the `uv run pytest -q` count, and
the acceptance runs from Task 10. Write them **after** Task 10 has run — session
rule 4 requires the output.

`A1.24`'s row says it was re-pointed from `C6.6` to `C6.10` during 9a. Close it
noting it died by deletion rather than by a fix: `looks_like_path` and the
checklist it mis-parsed are both gone.

- [ ] **Step 2: Re-point the two rows that moved**

- `A1.40` (`--dry-run` previews nothing) — its citation was
  `main_agent.py:352-358`, inside deleted code. Behaviour is unchanged;
  update the citation to the new early return and leave it PENDING.
- `A1.52` (factories read `Path.cwd()`) — citations move to the constructors
  Task 7 touched. Still PENDING.

- [ ] **Step 3: Mark §E's 9c row complete**

Update the `9c` row added during Step 9a, and add a line to §E noting that the
Step 9 decomposition is finished — 9a, 9b and 9c are all closed — so a fresh
session reads Stage III as complete and starts at Step 10.

- [ ] **Step 4: Update CLAUDE.md**

§3's tree: add `loop/`, remove `tools/planning_tools.py` and `agent/coder_agent.py`,
and rewrite the "Control flow" block (`main_agent.py:345-437`) — it describes the
deleted loop step by step and is now actively wrong. Replace with the ledger,
the fix loop, and the gate.

The `.rudra/` state table: drop `run/PLAN.md` and `run/current_task.md`, add
`run/ledger.json`. Update §8's test count and add `[agent] max_fix_attempts` to
§6's config example.

- [ ] **Step 5: Commit**

```bash
git add TODO.md CLAUDE.md
git commit -m "docs: close C6.1, C6.5, C6.5a, C6.10, A1.8, A1.24, A1.25

Step 9's decomposition is complete: 9a built the gate, 9b the subagents,
9c the loop that uses both.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Acceptance

**Files:** none modified. Produces the evidence Task 9 records.

**Interfaces:** none.

- [ ] **Step 1: Greenfield, end to end**

```bash
WORK=$(mktemp -d) && cd "$WORK" && git init -q
mkdir -p .rudra && cat > .rudra/config.toml <<'TOML'
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model = "gemma4:31b-cloud"
context_tokens = 131072

[permissions]
mode = "auto"

[tools]
shell_in_auto = true
TOML
uv run --project /Users/archish/Documents/ai-ml/Rudra rudra --auto --allow-shell \
  "build a CSV parser in parser.py that handles quoted commas, with tests"
echo "exit=$?"
cat .rudra/run/ledger.json
```

Expected: the planner declares tasks in plain language, the coder writes them,
the gate passes, every task is `done`, and the summary's counts match the files
on disk. Then **run the generated code by hand** to confirm it works — the bar
Steps 2, 5 and 6 used. Record the ledger and the summary verbatim.

- [ ] **Step 2: The environment wall**

```bash
sed -i '' 's/shell_in_auto = true/shell_in_auto = false/' .rudra/config.toml
rm -rf parser.py test_parser.py .rudra/run/ledger.json
uv run --project /Users/archish/Documents/ai-ml/Rudra rudra --auto \
  "build a CSV parser in parser.py that handles quoted commas, with tests"
echo "exit=$?"
grep auto-shell .rudra/run/logs/permissions.jsonl | tail -2
```

Expected: the gate's command stages are denied, `escalate` is true, and the loop
**stops on the first task**. The summary must show the remaining tasks as
`never attempted` rather than omitting them — that is spec §6's claim, and the
run that proves it.

- [ ] **Step 3: No-progress, forced deterministically**

```bash
WORK2=$(mktemp -d) && cd "$WORK2" && git init -q
printf '[project]\nname="acc"\nversion="0.1.0"\n' > pyproject.toml
# A pre-existing type error in a module the request never mentions.
# Typecheck is project-wide, so every attempt fails identically.
printf 'def broken(n: int) -> str:\n    return n\n' > legacy.py
git add -A && git -c user.email=t@t -c user.name=t commit -qm initial
mkdir -p .rudra && cp "$WORK/.rudra/config.toml" .rudra/config.toml
sed -i '' 's/shell_in_auto = false/shell_in_auto = true/' .rudra/config.toml
uv run --project /Users/archish/Documents/ai-ml/Rudra rudra --auto --allow-shell \
  "add a greet(name) function in greet.py that returns a greeting"
echo "exit=$?"
python3 -c "import json;print(json.load(open('.rudra/run/ledger.json'))['tasks'])"
```

Expected: attempt 1 fails typecheck, attempt 2 produces the **same** signature,
the task is `blocked` with `no progress: the same failure twice`, and `attempts`
is **2** — not 3. A budget-exhausted result here means the signature is not
stable and `bounds.py` needs re-checking, not the test.

This case earns its place because it is real — it is `A1.59` on Rudra's own tree
— and because a loop that cannot stop is exactly what `C6.5a` exists to prevent.

- [ ] **Step 4: Final gates**

```bash
cd /Users/archish/Documents/ai-ml/Rudra
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
uv run rudra --version
```

- [ ] **Step 5: Clean up and record**

```bash
rm -rf "$WORK" "$WORK2"
```

Write the observed output into Task 9's rows, then commit.

---

## Appendix: What this plan deliberately does not do

- **No `--continue`, no checkpoint resumption.** The ledger is written after every
  status change and never read back at startup. `A1.2` and `A1.3` stay PENDING;
  resumption is `C7.2`.
- **No retry around model invocation.** The loop *stops* on a provider error
  rather than retrying. `A1.39` stays PENDING — this is containment, not a fix,
  and it means a long run can die at task 4 of 6 with four tasks' work already on
  disk and correctly recorded as done.
- **No changes to `verify/`.** 9a's gate is used exactly as it shipped.
- **No changes to `subagents/`** beyond `_CODER_PROMPT`.
- **`AGENTS.md` is still written once and never updated** (`A1.9`).
- **No three-stage planning, no clarifying-question budget, no plan mode** —
  `C6.7`–`C6.9`, Step 10.
- **`A1.59`** (Rudra's own 69 mypy errors) is untouched. Acceptance step 3
  deliberately reproduces the same class in a scratch project.
