# Plan Mode Implementation Plan (Step 10c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present the plan Rudra produced, let the user approve, revise, or cancel it, and only then execute — and make `--plan` finally do what its name says.

**Architecture:** `run_loop` splits into `plan()` (the three 10b stages, returns a filled ledger) and `work()` (the task loop, reviewer, summary), with `run_loop` surviving as their composition so no existing caller or test changes. `RudraAgent.run` uses the seam: it plans, renders the facts and tasks, and consults an **injected** approval callable — defaulting to auto-approve — before calling `work()`. Revision re-enters the `breakdown` stage with the user's words verbatim, which is the one stage 10b made re-enterable.

**Tech Stack:** Python 3.12+, `deepagents` 0.7.4, Rich (`Prompt.ask`, `rich.markup.escape`), Typer, pytest, ruff, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-13-step10c-plan-approval-design.md` — read it before Task 1. Owner decisions are S10c.1–S10c.5.

## Global Constraints

- **Session rule 2 (non-negotiable):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence **first**, then fix, then mark `DONE`.
- **Evidence-based only.** Every claim about the codebase cites `file.py:line`.
- `uv run ruff check src/ tests/` → `All checks passed!`; `uv run ruff format --check src/ tests/` clean. Both absolute gates.
- `uv run pytest -q` must never drop below **1017 passed, 2 skipped**. Prefer `uv run` (CLAUDE.md §9).
- **`run_loop`'s signature and behaviour must not change.** Thirteen cases in `tests/test_loop_run.py` drive it; if any needs editing, the split is wrong.
- **`EOF` is cancel, never approve** (S10c.5). A plan must not execute because a pipe closed.
- **Everything printed goes through `rich.markup.escape`** — a task description containing `[bold]` must appear, not vanish (A1.48, A1.67).
- Approval is an **injected callable**, defaulting to auto-approve, so every test runs with no TTY and no model.
- S9c.1 holds: the user approves *what to attempt*; Python still decides what is done.
- Commit after every task, ending the message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Split `run_loop` into `plan()` and `work()`

**Files:**
- Modify: `src/rudra/loop/engine.py` — `run_loop` (`:374-424`), `__all__`
- Modify: `src/rudra/loop/__init__.py` (export the two new names)
- Test: `tests/test_loop_split.py` (create)

**Interfaces:**
- Consumes: `run_task`, `review_once`, `summarise`, `Ledger` as they are.
- Produces: `plan(request, *, context, planner, ledger=None) -> Ledger`; `work(request, *, context, planner, ledger) -> AgentResult`; `run_loop(...)` unchanged, now implemented as `plan()` then `work()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_loop_split.py`:

```python
"""plan() and work(), and that run_loop still equals their sum (Step 10c).

The seam exists so RudraAgent can put an approval gate between planning
and working. run_loop keeping its exact behaviour is what lets every 9c
and 10b test stand unmodified, so that equality is asserted here rather
than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, plan, run_loop, work
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


@dataclass
class FakeSubagents:
    gate: object = None
    session_id: str = "s1"


@pytest.fixture
def context(tmp_path):
    return LoopContext(
        subagents=FakeSubagents(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=FakeCfg(),
        paths=rudra_paths(tmp_path),
    )


def _planner(seen, tasks=("write it",)):
    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append((stage, reason))
        if stage == "breakdown" and reason == "initial":
            for description in tasks:
                ledger.add(description)

    return planner


async def _noop(context, ledger=None):
    return None


def _always_done():
    async def fake_run_task(task, ledger, *, context):
        task.status = TaskStatus.DONE
        task.attempts = 1
        return Outcome.DONE

    return fake_run_task


async def test_plan_runs_the_three_stages_and_returns_a_filled_ledger(context):
    seen: list[tuple[str, str]] = []

    ledger = await plan("build it", context=context, planner=_planner(seen))

    assert seen == [
        ("clarify", "initial"),
        ("architect", "initial"),
        ("breakdown", "initial"),
    ]
    assert [task.description for task in ledger.tasks] == ["write it"]


async def test_plan_runs_no_task(monkeypatch, context):
    """Planning must not touch the project. That is the whole point."""
    ran: list[str] = []

    async def fake_run_task(task, ledger, *, context):
        ran.append(task.id)
        return Outcome.DONE

    monkeypatch.setattr(engine, "run_task", fake_run_task)
    await plan("build it", context=context, planner=_planner([]))

    assert ran == []


async def test_plan_uses_the_ledger_it_is_given(context):
    """The planner's tools are bound to one Ledger; a copy loses the tasks."""
    shared = Ledger()
    returned = await plan("build it", context=context, planner=_planner([]), ledger=shared)
    assert returned is shared


async def test_work_consults_neither_clarify_nor_architect(monkeypatch, context):
    seen: list[tuple[str, str]] = []
    ledger = Ledger()
    ledger.add("write it")

    monkeypatch.setattr(engine, "run_task", _always_done())
    monkeypatch.setattr(engine, "review_once", _noop)

    await work("build it", context=context, planner=_planner(seen), ledger=ledger)

    assert all(stage == "breakdown" for stage, _ in seen), seen


async def test_work_runs_the_tasks_it_is_handed(monkeypatch, context):
    ran: list[str] = []

    async def fake_run_task(task, ledger, *, context):
        ran.append(task.description)
        task.status = TaskStatus.DONE
        task.attempts = 1
        return Outcome.DONE

    ledger = Ledger()
    ledger.add("write it")
    ledger.add("test it")

    monkeypatch.setattr(engine, "run_task", fake_run_task)
    monkeypatch.setattr(engine, "review_once", _noop)

    result = await work("build it", context=context, planner=_planner([]), ledger=ledger)

    assert ran == ["write it", "test it"]
    assert result.success is True


async def test_run_loop_equals_plan_then_work(monkeypatch, context, tmp_path):
    """The composition must be exactly that -- no third behaviour."""
    monkeypatch.setattr(engine, "run_task", _always_done())
    monkeypatch.setattr(engine, "review_once", _noop)

    composed_seen: list[tuple[str, str]] = []
    composed_ledger = await plan("build it", context=context, planner=_planner(composed_seen))
    composed = await work(
        "build it", context=context, planner=_planner(composed_seen), ledger=composed_ledger
    )

    whole_seen: list[tuple[str, str]] = []
    whole = await run_loop("build it", context=context, planner=_planner(whole_seen))

    assert whole.success == composed.success
    assert whole.message == composed.message
    assert whole_seen == composed_seen
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop_split.py -q`
Expected: FAIL — `ImportError: cannot import name 'plan' from 'rudra.loop.engine'`.

- [ ] **Step 3: Perform the split**

In `src/rudra/loop/engine.py`, replace `run_loop` with three functions:

```python
async def plan(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Ledger:
    """Run the three planning stages and return the ledger they filled.

    Touches nothing in the project: no coder runs here, which is what
    makes `--plan` honest and what lets `RudraAgent` put an approval gate
    between this and `work()` (C6.9).

    `ledger` must be the SAME object the planner's tools were bound to --
    otherwise the tasks it adds are invisible. Defaults to a fresh one
    only so tests can drive planning without wiring an agent.
    """
    ledger = ledger if ledger is not None else Ledger()
    ledger.save(context.paths.ledger_json)

    # Three stages, in order (C6.7, S10b.1): settle the facts, decide the
    # shape, then declare the work. Each is a separate agent with its own
    # tools, so a stage cannot do another stage's job. Only `breakdown` is
    # ever re-entered, and only by work() below (S10b.3).
    for stage in ("clarify", "architect", "breakdown"):
        await planner(ledger, request, stage=stage, reason="initial")

    return ledger


async def work(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger,
) -> Any:
    """Run every pending task to a verdict, review once, and report.

    Consults only the `breakdown` stage, and only on a stall: the facts
    and the architecture were settled by plan(), and a user may since have
    approved them.
    """
    consulted_on_empty = False

    while True:
        task = ledger.next_pending()
        if task is None:
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
            continue

        outcome = await run_task(task, ledger, context=context)
        ledger.save(context.paths.ledger_json)

        if outcome is Outcome.STOP_RUN:
            context.console.print(
                f"\n[bold red]Run stopped early.[/bold red] [dim]{task.note}[/dim]"
            )
            break
        if outcome is Outcome.BLOCKED:
            # Only a stall consults the planner -- never an ordinary success.
            consulted_on_empty = False
            await planner(ledger, request, stage="breakdown", reason="blocked", task=task)

    if any(task.status is TaskStatus.DONE for task in ledger.tasks):
        await review_once(context, ledger)
    return summarise(ledger, context.console)


async def run_loop(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Any:
    """Plan, work, verify, and stop. The whole run.

    Kept as the composition of plan() and work() rather than replaced by
    them: it is what every 9c and 10b test drives, and a caller with no
    interest in the seam should not have to know one exists. The seam is
    used by RudraAgent, which puts the approval gate between the two
    (C6.9).

    `planner` is an awaitable called as
    `planner(ledger, request, stage=..., reason=..., task=...)`; it adds
    or drops tasks and records facts through its tools and returns
    nothing. Injected rather than constructed here so the loop is
    testable without a model.
    """
    filled = await plan(request, context=context, planner=planner, ledger=ledger)
    return await work(request, context=context, planner=planner, ledger=filled)
```

Extend `__all__` in that module with `"plan"` and `"work"`, and re-export both from `src/rudra/loop/__init__.py` beside `run_loop` (check its current export list first with `sed -n 1,29p src/rudra/loop/__init__.py`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_split.py -q`
Expected: PASS, 6 tests.

Run: `uv run pytest -q`
Expected: **1023 passed, 2 skipped** — and **zero** edits to `tests/test_loop_run.py`. If any case there fails, the split changed behaviour; fix the split, not the test.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/loop tests/test_loop_split.py
git add src/rudra/loop tests/test_loop_split.py
git commit -m "$(cat <<'EOF'
refactor(loop): split run_loop into plan() and work()

Planning produces a ledger; working consumes one. The seam exists so
RudraAgent can put an approval gate between them (C6.9).

run_loop survives as their composition, unchanged in signature and
behaviour, because thirteen cases drive it -- a seam bought by rewriting
the regression net would not be worth having. A test asserts the
composition really is the sum of its parts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Render the plan

**Files:**
- Create: `src/rudra/loop/plan_view.py`
- Test: `tests/test_plan_render.py` (create)

**Interfaces:**
- Consumes: `Ledger`, `Task` (`loop/ledger.py`), `FactStore`/`Fact` (`rudra.facts`).
- Produces: `render_plan(ledger: Ledger, facts: Any = None) -> str` — a Rich-markup-safe block; `""` is never returned (an empty plan renders a sentence saying so).

- [ ] **Step 1: Write the failing test**

Create `tests/test_plan_render.py`:

```python
"""What the user sees before approving (Step 10c, C6.9)."""

from __future__ import annotations

from rudra.facts import FactStore
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.loop.plan_view import render_plan


def _ledger(*descriptions: str) -> Ledger:
    ledger = Ledger()
    for description in descriptions:
        ledger.add(description)
    return ledger


def test_every_task_appears_with_its_id():
    text = render_plan(_ledger("write the parser", "write tests"))
    assert "t1" in text and "write the parser" in text
    assert "t2" in text and "write tests" in text


def test_facts_appear_with_their_source():
    """The source is the difference between "you told me" and "I guessed"."""
    facts = FactStore()
    facts.record("language", "Rust", "the user asked for Rust", "asked")
    facts.record("layout", "src/parser.rs parsing", "keeps parsing testable", "inferred")

    text = render_plan(_ledger("write it"), facts)

    assert "language" in text and "Rust" in text and "asked" in text
    assert "layout" in text and "inferred" in text


def test_no_facts_renders_no_facts_section():
    text = render_plan(_ledger("write it"), FactStore())
    assert "write it" in text
    assert "facts" not in text.lower()


def test_no_facts_argument_at_all_is_fine():
    assert "write it" in render_plan(_ledger("write it"))


def test_an_empty_plan_says_so_rather_than_rendering_nothing():
    text = render_plan(Ledger())
    assert text.strip(), "an empty plan must still produce a sentence"
    assert "no task" in text.lower()


def test_markup_in_a_task_description_survives():
    """A1.48/A1.67's class: Rich eats [word] unless it is escaped."""
    text = render_plan(_ledger("handle [bold] markers in input"))
    assert "[bold]" in text


def test_markup_in_a_fact_value_survives():
    facts = FactStore()
    facts.record("style", "[dim]never[/dim]", "the user pasted it", "asked")
    assert "[dim]never[/dim]" in render_plan(_ledger("write it"), facts)


def test_a_dropped_task_is_not_presented_as_work():
    """Dropped tasks are history, not plan."""
    ledger = _ledger("write it", "do not do this")
    ledger.tasks[1].status = TaskStatus.DROPPED

    text = render_plan(ledger)

    assert "write it" in text
    assert "do not do this" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_plan_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.loop.plan_view'`.

- [ ] **Step 3: Write the renderer**

Create `src/rudra/loop/plan_view.py`:

```python
"""The plan, as the user sees it before approving (C6.9).

Pure: takes a ledger and a fact store, returns a string. Printing is the
caller's job, which is what makes this testable without a console.

Everything user-supplied is escaped. A task description or fact value
containing `[bold]` must appear on screen, and Rich would otherwise parse
it as a style tag and print nothing -- the defect A1.48 recorded for
config errors and A1.67 for the ledger trace. This is a third surface
with the same exposure, and it is escaped at the point of rendering.
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from rudra.loop.ledger import Ledger, TaskStatus

# What a user is being asked to approve: work not yet attempted. A
# dropped task is history, and a done one cannot be un-approved.
_PRESENTABLE = frozenset({TaskStatus.PENDING, TaskStatus.IN_PROGRESS})


def render_plan(ledger: Ledger, facts: Any = None) -> str:
    """The facts and the tasks, ready to print."""
    lines = ["[bold]Plan[/bold]"]

    entries = list(facts.items()) if facts is not None and facts.facts else []
    if entries:
        lines.append("  [dim]facts:[/dim]")
        lines.extend(
            f"    {escape(key)} = {escape(fact.value)} [dim]({escape(fact.source)})[/dim]"
            for key, fact in entries
        )

    tasks = [task for task in ledger.tasks if task.status in _PRESENTABLE]
    if not tasks:
        lines.append("  [yellow]No tasks were declared.[/yellow]")
        return "\n".join(lines)

    lines.append("  [dim]tasks:[/dim]")
    lines.extend(f"    {task.id}  {escape(task.description)}" for task in tasks)
    return "\n".join(lines)


__all__ = ["render_plan"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_plan_render.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/loop tests/test_plan_render.py
git add src/rudra/loop/plan_view.py tests/test_plan_render.py
git commit -m "$(cat <<'EOF'
feat(loop): render the plan for a human

Facts with their source, then the tasks. The source is shown because it
is the difference between "you told me this" and "I guessed this", and a
user scanning a plan should have their attention drawn to the guesses.

Everything user-supplied is escaped: a task description containing
[bold] must appear rather than vanish, which is A1.48 and A1.67's shared
defect meeting a third surface.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Ask for approval

**Files:**
- Modify: `src/rudra/loop/plan_view.py` (append)
- Test: `tests/test_plan_approval.py` (create)

**Interfaces:**
- Consumes: `render_plan` from Task 2.
- Produces: `PlanDecision` (`StrEnum`: `APPROVE`, `REVISE`, `CANCEL`); `PlanAnswer` (frozen dataclass: `decision: PlanDecision`, `feedback: str = ""`); `ask_approval(console) -> PlanAnswer`; `auto_approve(console) -> PlanAnswer` returning `APPROVE` without prompting.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plan_approval.py`:

```python
"""The approval prompt (Step 10c, C6.9).

Driven with a patched Prompt.ask, so these run with no TTY -- the
property that makes the whole gate testable.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from rudra.loop import plan_view
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve


@pytest.fixture
def console():
    return Console(quiet=True)


def _answers(monkeypatch, *replies):
    remaining = list(replies)

    def fake_ask(*_args, **_kwargs):
        if not remaining:
            raise AssertionError("prompted more times than the test scripted")
        reply = remaining.pop(0)
        if isinstance(reply, type) and issubclass(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(plan_view.Prompt, "ask", fake_ask)
    return remaining


def test_a_approves(monkeypatch, console):
    _answers(monkeypatch, "a")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.APPROVE
    assert answer.feedback == ""


def test_c_cancels(monkeypatch, console):
    _answers(monkeypatch, "c")
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_r_collects_feedback(monkeypatch, console):
    _answers(monkeypatch, "r", "drop the tests task, I have my own")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "drop the tests task, I have my own"


def test_empty_feedback_is_asked_again(monkeypatch, console):
    remaining = _answers(monkeypatch, "r", "   ", "actually split task two")
    answer = ask_approval(console)
    assert answer.decision is PlanDecision.REVISE
    assert answer.feedback == "actually split task two"
    assert remaining == [], "the empty answer must have cost a re-prompt"


def test_empty_feedback_twice_is_a_cancel(monkeypatch, console):
    """One slip is a slip; two is someone who does not want to revise."""
    _answers(monkeypatch, "r", "", "")
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_eof_at_the_choice_is_cancel_not_approve(monkeypatch, console):
    """S10c.5. A plan must not run because a pipe closed."""
    _answers(monkeypatch, EOFError)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_eof_while_collecting_feedback_is_cancel(monkeypatch, console):
    _answers(monkeypatch, "r", EOFError)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_keyboard_interrupt_is_cancel(monkeypatch, console):
    _answers(monkeypatch, KeyboardInterrupt)
    assert ask_approval(console).decision is PlanDecision.CANCEL


def test_auto_approve_never_prompts(monkeypatch, console):
    def explode(*_args, **_kwargs):
        raise AssertionError("auto approval must not prompt")

    monkeypatch.setattr(plan_view.Prompt, "ask", explode)
    assert auto_approve(console).decision is PlanDecision.APPROVE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_plan_approval.py -q`
Expected: FAIL — `ImportError: cannot import name 'PlanDecision'`.

- [ ] **Step 3: Write the prompt**

Append to `src/rudra/loop/plan_view.py` (and add `from dataclasses import dataclass`, `from enum import StrEnum`, `from rich.console import Console`, `from rich.prompt import Prompt` to its imports):

```python
class PlanDecision(StrEnum):
    """What the user decided about the plan."""

    APPROVE = "approve"
    REVISE = "revise"
    CANCEL = "cancel"


@dataclass(frozen=True)
class PlanAnswer:
    """A decision, plus the words behind a revision."""

    decision: PlanDecision
    feedback: str = ""


_CANCEL = PlanAnswer(PlanDecision.CANCEL)


def auto_approve(console: Console) -> PlanAnswer:
    """Approve without asking. The default, so nothing pauses unbidden.

    Every caller that has not opted into a prompt -- `--auto`, the tests,
    any programmatic use -- gets this, which is why adding the gate
    changes no existing behaviour.
    """
    return PlanAnswer(PlanDecision.APPROVE)


def ask_approval(console: Console) -> PlanAnswer:
    """Ask the user to approve, revise, or cancel the plan.

    EOF and Ctrl-C are cancel, never approve (S10c.5): a plan must not
    execute because a pipe closed or a user gave up. That asymmetry is
    the one safety property this function has.
    """
    try:
        choice = Prompt.ask(
            "[bold]Proceed?[/bold] [dim][a]pprove [r]evise [c]ancel[/dim]",
            choices=["a", "r", "c"],
            default="a",
        )
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]No answer — cancelled.[/dim]")
        return _CANCEL

    if choice == "a":
        return PlanAnswer(PlanDecision.APPROVE)
    if choice == "c":
        return _CANCEL

    # Revise. An empty answer is a slip and costs one re-prompt; a second
    # empty answer is someone who does not want to revise after all.
    for _ in range(2):
        try:
            feedback = Prompt.ask("[bold]What should change?[/bold]", default="").strip()
        except (EOFError, KeyboardInterrupt):
            return _CANCEL
        if feedback:
            return PlanAnswer(PlanDecision.REVISE, feedback)
        console.print("[dim]Say what should change, or press Ctrl-C to cancel.[/dim]")
    return _CANCEL
```

Extend `__all__`: `["PlanAnswer", "PlanDecision", "ask_approval", "auto_approve", "render_plan"]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_plan_approval.py -q`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/loop tests/test_plan_approval.py
git add src/rudra/loop/plan_view.py tests/test_plan_approval.py
git commit -m "$(cat <<'EOF'
feat(loop): approve, revise, or cancel a plan

EOF and Ctrl-C are cancel, never approve (S10c.5). That asymmetry is the
one safety property here: a plan must not execute because a pipe closed
or a user walked away.

auto_approve is the default everywhere else, so --auto, the tests and
any programmatic caller keep running without a pause.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `consult_planner` learns the `revision` reason

**Files:**
- Modify: `src/rudra/agent/planner_agent.py` — `consult_planner` (`:345-…`)
- Test: `tests/test_planner_consult.py` (extend)

**Interfaces:**
- Consumes: `STAGES`, `_stream_planner_turn`.
- Produces: `consult_planner(..., reason="revision", feedback="…")` — allowed on `breakdown` only, like the other re-entry reasons.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_consult.py`:

```python
# --- Step 10c: the user revising the plan ---


async def test_a_revision_carries_the_users_words_verbatim(captured):
    await _consult(
        "breakdown",
        reason="revision",
        feedback="drop the tests task, I have my own",
    )

    message = captured[0]["message"]
    assert "drop the tests task, I have my own" in message


async def test_a_revision_is_a_breakdown_re_entry_like_the_others(captured):
    await _consult("breakdown", reason="revision", feedback="split task two")
    assert captured[0]["thread_id"] == "sess-breakdown"


async def test_clarify_cannot_be_revised(captured):
    """The facts the user just approved are not re-litigated (S10b.3)."""
    with pytest.raises(ValueError):
        await _consult("clarify", reason="revision", feedback="anything")


async def test_a_revision_without_feedback_is_a_programming_error(captured):
    with pytest.raises(ValueError, match="feedback"):
        await _consult("breakdown", reason="revision")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planner_consult.py -q -k revis`
Expected: FAIL — `consult_planner() got an unexpected keyword argument 'feedback'`.

- [ ] **Step 3: Add the reason**

In `src/rudra/agent/planner_agent.py`, add `feedback: str = ""` to `consult_planner`'s keyword arguments, and add this branch **before** the final `else` that raises:

```python
    elif reason == "revision":
        if not feedback.strip():
            msg = "a revision needs the user's feedback; got an empty string"
            raise ValueError(msg)
        message = (
            "The user reviewed your plan and asked for this change:\n\n"
            f"{feedback.strip()}\n\n"
            "Adjust the task list with add_tasks and drop_task to match. "
            "Change only what they asked about; leave the rest alone."
        )
```

Update the docstring: `breakdown` is re-entered on a block, an empty ledger, **or a user revision**. The user's words go in verbatim for the reason the gate's blocker does — a paraphrase of an instruction is a worse instruction.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planner_consult.py -q`
Expected: PASS, 10 tests.

Run: `uv run pytest -q`
Expected: **1046 passed, 2 skipped** or thereabouts, zero failures.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/agent tests/test_planner_consult.py
git add src/rudra/agent/planner_agent.py tests/test_planner_consult.py
git commit -m "$(cat <<'EOF'
feat(planner): a revision is a breakdown re-entry

The user's words go to the model verbatim, for the reason the gate's
blocker does: a paraphrase of an instruction is a worse instruction.

Only breakdown can be revised. The facts and the architecture a user has
already seen are not re-litigated by a change to the task list, which is
S10b.3's guard doing its job unchanged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Wire the gate into the run

**Files:**
- Modify: `src/rudra/agent/main_agent.py` — `RudraAgent.__init__` and `.run` (`:56-180`), `create_main_agent` (`:283-…`)
- Modify: `src/rudra/cli.py` — the `--plan` help text (`:603-605`)
- Test: `tests/test_agent_wiring.py` (extend)

**Interfaces:**
- Consumes: `plan`, `work` (Task 1); `render_plan`, `ask_approval`, `auto_approve`, `PlanDecision` (Tasks 2–3); `consult_planner(reason="revision", feedback=…)` (Task 4).
- Produces: `RudraAgent(..., facts=None, approve=None)`; `RudraAgent.run` gains the present/approve/execute flow. `MAX_REVISIONS = 3` module constant.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_wiring.py`:

```python
# --- Step 10c: the approval gate ---


def _agent(tmp_path, *, mode="auto", approve=None, tasks=("write it",)):
    """A RudraAgent with every model and backend faked away."""
    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent
    from rudra.facts import FactStore
    from rudra.loop.ledger import Ledger

    ledger = Ledger()

    async def planner_callback(run_ledger, request, *, stage, reason="initial", task=None,
                               feedback=""):
        planner_callback.calls.append((stage, reason, feedback))
        if stage == "breakdown" and reason == "initial":
            for description in tasks:
                run_ledger.add(description)

    planner_callback.calls = []

    context = AgentContext(
        project_path=tmp_path, task="build it", console=Console(quiet=True)
    )
    agent = RudraAgent(
        context=context,
        planner_agent=object(),
        session_id="s1",
        db_conn=None,
        loop_context=object(),
        planner_callback=planner_callback,
        ledger=ledger,
        facts=FactStore(),
        approve=approve,
    )
    return agent, planner_callback


async def test_auto_mode_never_asks_for_approval(monkeypatch, tmp_path):
    """--auto must not pause. Nobody is there to answer."""
    import rudra.agent.main_agent as main_agent

    def explode(console):
        raise AssertionError("--auto must not prompt for plan approval")

    monkeypatch.setattr(main_agent, "ask_approval", explode)
    monkeypatch.setattr(main_agent, "plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, _ = _agent(tmp_path)
    await agent.run()

    assert worked == ["ran"], "the work must still run"


async def test_cancel_stops_before_any_work(monkeypatch, tmp_path):
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, _ = _agent(
        tmp_path, approve=lambda console: PlanAnswer(PlanDecision.CANCEL)
    )
    result = await agent.run()

    assert worked == []
    assert result.success is True, "declining a plan is not a failure"


async def test_a_revision_re_enters_breakdown_then_executes(monkeypatch, tmp_path):
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    answers = [
        PlanAnswer(PlanDecision.REVISE, "drop the tests task"),
        PlanAnswer(PlanDecision.APPROVE),
    ]
    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, planner = _agent(tmp_path, approve=lambda console: answers.pop(0))
    await agent.run()

    assert ("breakdown", "revision", "drop the tests task") in planner.calls
    assert worked == ["ran"]


async def test_revisions_are_capped(monkeypatch, tmp_path):
    from rudra.agent.main_agent import MAX_REVISIONS
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, planner = _agent(
        tmp_path, approve=lambda console: PlanAnswer(PlanDecision.REVISE, "again")
    )
    await agent.run()

    revisions = [call for call in planner.calls if call[1] == "revision"]
    assert len(revisions) == MAX_REVISIONS
    assert worked == [], "an unapproved plan must not execute"


async def test_plan_mode_presents_and_stops(monkeypatch, tmp_path):
    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, _ = _agent(tmp_path, mode="plan")
    agent.context.command = "plan"
    result = await agent.run()

    assert worked == [], "--plan must never execute"
    assert result.success is True
```

Add the two helpers near the top of that block:

```python
def _fake_plan():
    async def fake_plan(request, *, context, planner, ledger=None):
        from rudra.loop.ledger import Ledger

        ledger = ledger if ledger is not None else Ledger()
        await planner(ledger, request, stage="breakdown", reason="initial")
        return ledger

    return fake_plan


def _record_work(monkeypatch):
    ran: list[str] = []

    async def fake_work(request, *, context, planner, ledger):
        from rudra.agent.main_agent import AgentResult

        ran.append("ran")
        return AgentResult(success=True, message="done")

    monkeypatch.setattr("rudra.agent.main_agent.work", fake_work)
    return ran
```

`_agent`'s `mode` argument decides how `RudraAgent.run` reads the permission mode; if `run` reads it from `get_config()`, patch that instead — check with `grep -n "get_config()" src/rudra/agent/main_agent.py` and follow the existing call, do not add a second source of truth.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agent_wiring.py -q -k "approval or cancel or revision or plan_mode"`
Expected: FAIL — `RudraAgent.__init__() got an unexpected keyword argument 'facts'`.

- [ ] **Step 3: Wire it**

In `src/rudra/agent/main_agent.py`, add to the imports:

```python
from rudra.loop import plan, work
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve, render_plan
```

Add the constant beside the other module-level limits:

```python
# How many times a user may send the plan back before Rudra stops
# offering. Someone revising a fourth time wants to change the request,
# not the plan (S10c.4). Deliberately not configurable: an inert config
# key is worse than no key.
MAX_REVISIONS = 3
```

Extend `RudraAgent.__init__` with two parameters and store them:

```python
        facts=None,
        approve=None,
    ):
        ...
        # The run's fact store, for presenting the plan. Same object the
        # stages recorded into.
        self._facts = facts
        # How approval is obtained. Injected and defaulting to
        # auto-approve, so every existing caller and every test runs
        # without a prompt (S10c.3).
        self._approve = approve or auto_approve
```

Replace the `return await run_loop(...)` block in `run()` with:

```python
            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")

            ledger = await plan(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=self._ledger,
            )

            decision = await self._settle_plan(ledger)
            if decision is not PlanDecision.APPROVE:
                return self._plan_only_result(ledger, decision)

            return await work(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=ledger,
            )
```

Add the two helpers to `RudraAgent`:

```python
    async def _settle_plan(self, ledger) -> PlanDecision:
        """Show the plan and find out whether to run it (C6.9).

        `plan` mode never approves: it presents and stops, which is what
        `--plan` has always claimed to do. `--auto` and every
        programmatic caller get auto_approve, so they never pause.
        """
        self.console.print()
        self.console.print(render_plan(ledger, self._facts))

        if get_config().permissions.mode == "plan":
            self.console.print("\n[dim]Plan mode — nothing was executed.[/dim]")
            return PlanDecision.CANCEL

        for attempt in range(MAX_REVISIONS + 1):
            answer = self._approve(self.console)
            if answer.decision is not PlanDecision.REVISE:
                return answer.decision
            if attempt == MAX_REVISIONS:
                break
            await self._planner_callback(
                ledger,
                self.context.task,
                stage="breakdown",
                reason="revision",
                feedback=answer.feedback,
            )
            self.console.print()
            self.console.print(render_plan(ledger, self._facts))

        self.console.print(
            f"\n[yellow]Revised {MAX_REVISIONS} times — change the request instead.[/yellow]"
        )
        return PlanDecision.CANCEL

    def _plan_only_result(self, ledger, decision: PlanDecision) -> AgentResult:
        """A run that planned and stopped. Not a failure -- the user chose."""
        counts = ledger.counts()
        return AgentResult(
            success=True,
            message=f"Plan not executed ({decision.value}): {counts['requested']} task(s) declared",
            files_created=[],
            files_modified=[],
        )
```

In `create_main_agent`, the `planner_callback` closure must forward the new argument, or a revision reaches `consult_planner` without the user's words and raises the `ValueError` Task 4 added:

```python
    async def planner_callback(
        run_ledger, request, *, stage, reason="initial", task=None, feedback=""
    ):
        await consult_planner(
            planners[stage],
            run_ledger,
            request,
            stage=stage,
            reason=reason,
            task=task,
            feedback=feedback,
            gate=gate,
            console=console,
            session_id=session_id,
        )
```

Then pass the store and the prompt through — `ask_approval` only when a human can answer, which is the same `interactive` flag 10a computed:

```python
    return RudraAgent(
        ...
        facts=facts,
        approve=ask_approval if interactive else auto_approve,
    )
```

Finally, in `src/rudra/cli.py:603-605`, make the help text true:

```python
    plan: bool = typer.Option(
        False, "--plan", help="Plan only: show the plan and stop, changing nothing"
    ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_wiring.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: **≥ 1051 passed, 2 skipped**, zero failures.

Run: `uv run ruff check src/ tests/`
Expected: `All checks passed!` — ruff is what catches a dangling name in `create_main_agent`, which still has no unit coverage.

Run the construction smoke test that caught 10b's wiring:

```bash
uv run python /tmp/smoke10b.py
```
Expected: `OK: three planners constructed, callback accepts a stage`. If that file is gone, rebuild it from the Step 10b session: it calls `create_main_agent` against a `mktemp -d` project and prints the callback's parameters.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/ tests/
git add src/rudra/agent/main_agent.py src/rudra/cli.py tests/test_agent_wiring.py
git commit -m "$(cat <<'EOF'
feat(agent): present the plan, then approve, revise, or cancel

The gate sits between plan() and work() in RudraAgent, not in the loop:
the loop should not know what a terminal is. Approval is injected and
defaults to auto-approve, so --auto and every test run unchanged.

--plan now presents and stops, which is what its help text has claimed
since Step 7 while denying everything and printing nothing.

Revisions are capped at three. Someone revising a fourth time wants to
change the request, not the plan.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Documentation and ledger

**Files:**
- Modify: `CLAUDE.md` (§3 control flow, the commands block), `TODO.md` (`C6.9`, §E Step 10c row)
- Modify: `Documentation/04-cli-reference.md` (the `--plan` entry), `05-how-it-works.md` (the run walkthrough), `08-project-status.md` (the approval gap 10b left open)

- [ ] **Step 1: Update `CLAUDE.md`**

In §3's control flow, insert after the planning step:

```markdown
2. **The plan is presented and approved** (Step 10c, C6.9): facts with their
   source, then the tasks. In `ask` mode the user approves, revises (which
   re-enters `breakdown` with their words verbatim, up to three times), or
   cancels. `--plan` presents and stops. `--auto` skips the gate entirely.
   EOF is cancel, never approve.
```

and renumber the rest. In the commands block, correct the `--plan` line to say it presents a plan and stops.

- [ ] **Step 2: Update `TODO.md`**

- `C6.9` → **DONE**, citing the spec, plan, S10c.1–S10c.5, the test counts and the acceptance runs from Task 7.
- §E: mark **Step 10c COMPLETE**, and state that **the Step 10 decomposition is finished** — 10a the fact store, 10b the stages, 10c the approval — so Requirement #4 is met and a fresh session starts at **Step 11** (`C5.1–C5.9`, skills + vendored superpowers).
- Note against `A1.74` that approval now makes an overlapping plan visible to the user before it costs them a run — a mitigation, not the fix.

- [ ] **Step 3: Update `Documentation/`**

- `04-cli-reference.md`: `--plan` shows the plan and exits without changing anything; document the three verbs and the revision cap.
- `05-how-it-works.md`: after the three-stage section, add the approval step, and say what `--auto` skips.
- `08-project-status.md`: the "Planning is staged, but you do not approve it" section 10b wrote is now wrong — rewrite it to describe the shipped gate, and say what remains (`A1.74`).

- [ ] **Step 4: Verify the docs match the code**

Run: `grep -rn "you do not approve\|make no project changes, run no commands" Documentation/ CLAUDE.md README.md`
Expected: no hits.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md TODO.md Documentation/
git commit -m "$(cat <<'EOF'
docs: close C6.9 — Step 10 is complete

10a the fact store, 10b the stages, 10c the approval. Requirement #4,
plan before coding, is met.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Verification and live acceptance

**Files:** none created. This task produces evidence.

- [ ] **Step 1: The three local gates**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
```

Expected: `All checks passed!`; `N files already formatted`; `≥ 1051 passed, 2 skipped`. Record the real numbers; a count that fell means a test was deleted, and that needs a sentence naming which and why.

- [ ] **Step 2: Acceptance run 1 — `--plan` presents and changes nothing**

Use the pty driver from Step 10b — **not** `pty.spawn`, which feeds the child from our stdin and hangs when backgrounded (recorded in `A1.73`'s neighbourhood; the working script is `/tmp/rudra-10b-pty2.py`). Copy it and change the argv to `["--plan", "build a CLI in Rust with clap"]`.

```bash
D=$(mktemp -d); cd "$D" && git init -q .
mkdir -p .rudra && cp <a known-good config.toml> .rudra/config.toml
# drive with the adapted pty script, answering the clarify questions
git status --porcelain
```

Expected: the plan prints with facts and tasks; the process exits **0**; `git status --porcelain` shows **no** source file — only `.rudra/`. This is the run that makes the flag's help text true for the first time.

- [ ] **Step 3: Acceptance run 2 — approve, and the run proceeds**

Same setup in `ask` mode, answering the clarify questions and then `a` at the plan prompt. Expected: the plan is presented once, the work runs, and the summary matches 10b's run 2 in shape (`N requested · N done`).

- [ ] **Step 4: Acceptance run 3 — revise, then approve**

Answer `r` at the prompt with a concrete instruction (e.g. `drop the tests task, I will write my own`), then `a`. Expected: the re-presented plan **visibly differs** — the named task is gone — and the revised plan then executes. Quote both renderings into the TODO row. **This is the run that proves the row rather than the plumbing.**

- [ ] **Step 5: Acceptance run 4 — `--auto` is unchanged, then record**

```bash
env -i HOME="$HOME" PATH="$PATH" <rudra> --auto --allow-shell "build a CLI in Rust with clap that reverses its input string"
```

Expected: no prompt, no pause, and a summary in the shape 10b's run 2 produced.

Write the measured numbers and quoted output into `TODO.md`. Log any defect the runs surface as a new `PENDING` row **before** fixing it — session rule 2 — continuing from `A1.74`.

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs: Step 10c acceptance evidence

Four runs: --plan presenting and changing nothing, approve, revise then
approve, and --auto unchanged. The revise run is the one that proves
C6.9 rather than the plumbing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage.** S10c.1 (approval in ask; `--plan` presents and stops) → Tasks 5 and 6. S10c.2 (approve/revise/cancel) → Tasks 3 and 4. S10c.3 (the split) → Task 1, with `run_loop`'s equality asserted. S10c.4 (cap of 3) → Task 5's `MAX_REVISIONS` and its test. S10c.5 (EOF is cancel) → Task 3, tested at both prompts plus `KeyboardInterrupt`. Spec §3's escaping requirement → Task 2's two markup tests. Spec §4's failure table → Task 2 (empty plan), Task 3 (empty feedback, EOF, unparseable via `choices=`), Task 5 (`plan` mode, `--auto`, cancel exit). Spec §5's test files → Tasks 1–5. Spec §6's four runs → Task 7. Spec §7's out-of-scope items appear in no task.

**Placeholder scan.** Every code step carries real code. Three steps point at the repo rather than quoting it — `loop/__init__.py`'s current exports in Task 1, `get_config()`'s call site in Task 5, and the known-good `config.toml` in Task 7 — and each names the command that finds it. Task 7 reuses a script from the previous session and says exactly what to do if it is missing.

**Type consistency.** `plan(request, *, context, planner, ledger=None) -> Ledger` is defined in Task 1 and called that way in Task 5 and in Task 1's own composition test. `work(request, *, context, planner, ledger)` likewise. `render_plan(ledger, facts=None) -> str` is defined in Task 2 and called with both arguments in Task 5. `PlanAnswer(decision, feedback="")` and `PlanDecision.{APPROVE,REVISE,CANCEL}` are defined in Task 3 and consumed in Tasks 3 and 5. `consult_planner(..., reason="revision", feedback=…)` is defined in Task 4 and called by `_settle_plan` in Task 5 through the planner callback, whose signature gains `feedback=""` in both Task 5's test helper and `create_main_agent`'s real closure. That closure was missing from the first draft of Task 5 and is now written out in full: without it a revision reaches `consult_planner` with no feedback and raises the `ValueError` Task 4 adds.
