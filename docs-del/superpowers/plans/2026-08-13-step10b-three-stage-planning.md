# Three-Stage Planning Implementation Plan (Step 10b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single pre-work planner consult into three staged consults — clarify → architect → breakdown — each an agent that structurally cannot do another stage's job.

**Architecture:** `run_loop` consults the planner three times before any coder runs. Each stage is its own `create_deep_agent` with its own tool set, its own thread, and its own prompt, all built through one function so no assembly path can forget the permission gate. The 10a fact store is the only channel between stages: nothing carries over except what was recorded, which forces the architect to write decisions down and makes every stage testable by seeding a `FactStore`. Re-consultation on a block or an empty ledger enters **breakdown only**.

**Tech Stack:** Python 3.12+, `deepagents` 0.7.4, LangChain tools, Rich, pytest, ruff, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-13-step10b-three-stage-planning-design.md` — read it before Task 1. Owner decisions are S10b.1–S10b.5.

## Global Constraints

- **Session rule 2 (non-negotiable):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence **first**, then fix, then mark `DONE`.
- **Evidence-based only.** Every claim about the codebase cites `file.py:line`.
- `uv run ruff check src/ tests/` must print `All checks passed!` — absolute gate.
- `uv run ruff format --check src/ tests/` must be clean.
- `uv run pytest -q` must never drop below **977 passed, 2 skipped**. Prefer `uv run`: a local `.venv` drifts from `uv.lock` and will lie (CLAUDE.md §9).
- **No stage may write `DONE` or `BLOCKED`.** S9c.1 is untouched: only `loop/engine.py` writes those, and only on `VerifyReport.passed`.
- **No prompt may name a `.rudra/` path.** `tests/test_rudra_dir_migration.py::test_no_source_names_a_stale_volatile_path` enforces it.
- The permission gate goes **first** in every middleware list, for every stage (`planner_agent.py:179-182`).
- Stage names are exactly `"clarify"`, `"architect"`, `"breakdown"`. An unknown stage raises — a typo must not silently yield a toolless agent.
- Commit after every task, ending the message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Unattended runs cannot claim a fact was "asked" (A1.72)

**Files:**
- Modify: `src/rudra/tools/interaction_tools.py` (the `record_fact` body, around `:73-79`)
- Test: `tests/test_interaction_tools.py` (append)

**Interfaces:**
- Consumes: `create_interaction_tools(console, store, path, *, max_questions, interactive)` as it exists today.
- Produces: no signature change. Behaviour change only — with `interactive=False`, a `record_fact` call carrying `source="asked"` records `source="inferred"`.

This is first because it is independent of the staging work and because Task 4's clarify stage is the first consumer that would trust the label.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_interaction_tools.py`:

```python
# --- A1.72: an unattended run cannot have asked anybody ---


def test_an_unattended_record_fact_downgrades_asked_to_inferred(tmp_path: Path):
    """Measured in Step 10a's acceptance run: --auto, ask_user never
    registered, and all three facts came back source="asked".

    Python knows with certainty that nobody was asked, and `asked` is the
    label a later stage trusts as "the user settled this, do not revisit".
    """
    store, tools = _tools(tmp_path, interactive=False)
    out = tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "the request says Rust", "source": "asked"}
    )

    assert store.get("language").source == "inferred"
    assert "inferred" in out


def test_an_interactive_record_fact_keeps_asked(tmp_path: Path):
    store, tools = _tools(tmp_path, interactive=True)
    tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "the user said so", "source": "asked"}
    )

    assert store.get("language").source == "asked"


def test_the_other_sources_are_untouched_when_unattended(tmp_path: Path):
    store, tools = _tools(tmp_path, interactive=False)
    tools["record_fact"].invoke(
        {"key": "a", "value": "v", "why": "w", "source": "inferred"}
    )
    tools["record_fact"].invoke(
        {"key": "b", "value": "v", "why": "w", "source": "detected"}
    )

    assert store.get("a").source == "inferred"
    assert store.get("b").source == "detected"


def test_an_invalid_source_is_still_rejected_when_unattended(tmp_path: Path):
    """Coercion must not become a place bad input gets laundered."""
    store, tools = _tools(tmp_path, interactive=False)
    out = tools["record_fact"].invoke(
        {"key": "a", "value": "v", "why": "w", "source": "invented"}
    )

    assert out.startswith("REJECTED:")
    assert store.items() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_interaction_tools.py -q -k unattended_record_fact`
Expected: FAIL — `assert 'asked' == 'inferred'`.

- [ ] **Step 3: Coerce in the tool**

In `src/rudra/tools/interaction_tools.py`, inside `record_fact`, immediately before the `store.record(...)` call:

```python
        # A1.72: an unattended run has no ask_user at all (S10a.5), so a
        # fact cannot have been asked. The model said otherwise in Step
        # 10a's acceptance run -- honestly, since the request it read was
        # written by the user -- and `asked` is the label a later stage
        # trusts as "settled, do not revisit". Python knows the truth
        # here; the prompt can only request it.
        #
        # An unknown source still falls through to store.record and is
        # rejected there: coercion must not launder bad input.
        if not interactive and source == "asked":
            source = "inferred"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_interaction_tools.py -q`
Expected: PASS, 16 tests.

Run: `uv run pytest -q`
Expected: `981 passed, 2 skipped`.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/tools tests/test_interaction_tools.py
git add src/rudra/tools/interaction_tools.py tests/test_interaction_tools.py
git commit -m "$(cat <<'EOF'
fix: an unattended run cannot claim a fact was asked (A1.72)

Measured in Step 10a's acceptance run: --auto, ask_user never registered,
and all three facts came back source="asked". The value and the why were
right; only the provenance was wrong, in the one direction that matters,
because `asked` is what a later stage trusts as settled.

Python knows nobody was asked. The prompt can only request honesty.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

Then mark `A1.72` **DONE** in `TODO.md` with this commit's evidence (Task 6 collects the ledger edits, but this row may be closed now since its fix is complete).

---

### Task 2: One stage, one tool set

**Files:**
- Modify: `src/rudra/agent/planner_agent.py` — `create_planner_agent` (`:130-200`)
- Test: `tests/test_planner_stages.py` (create)

**Interfaces:**
- Consumes: `create_ledger_tools(ledger, path)`, `create_interaction_tools(console, store, path, *, max_questions, interactive)`.
- Produces: `STAGES: tuple[str, ...] = ("clarify", "architect", "breakdown")`; `_tools_for_stage(stage, *, ledger, facts, paths, console, cfg, interactive) -> list`; `create_planner_agent(..., stage: str = "breakdown")`. An unknown stage raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_stages.py`:

```python
"""Three stages, three tool sets (Step 10b, C6.7).

A stage cannot do another stage's job because the tool is not registered
-- the enforcement S9b.3 used for the reviewer and S10a.5 for ask_user.
These tests assert the tool lists, because that is the mechanism; a
prompt saying "clarify first" is a hint, and Step 7's acceptance run
already watched a model route around one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.agent.planner_agent import STAGES, create_planner_agent
from rudra.facts import FactStore


def _tool_names(monkeypatch, tmp_path: Path, stage: str, **kwargs) -> set[str]:
    captured = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr(
        "rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent
    )
    create_planner_agent(
        task="build a web API",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
        stage=stage,
        **kwargs,
    )
    return {tool.name for tool in captured["tools"]}


def test_the_stage_names_are_the_three_the_spec_names():
    assert STAGES == ("clarify", "architect", "breakdown")


def test_clarify_can_ask_and_record_but_not_plan(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "clarify")
    assert "ask_user" in names
    assert "record_fact" in names
    assert "add_tasks" not in names
    assert "drop_task" not in names


def test_architect_can_record_but_not_ask_or_plan(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "architect")
    assert "record_fact" in names
    assert "ask_user" not in names, "the questions are settled by now"
    assert "add_tasks" not in names


def test_breakdown_can_plan_but_not_ask_or_record(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "breakdown")
    assert {"add_tasks", "drop_task", "read_ledger"} <= names
    assert "ask_user" not in names
    assert "record_fact" not in names


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_can_mark_anything_done(monkeypatch, tmp_path: Path, stage: str):
    """S9c.1 is structural and stays that way."""
    names = _tool_names(monkeypatch, tmp_path, stage)
    assert not {"mark_done", "complete_task", "set_status"} & names


def test_an_unattended_clarify_has_no_ask_user(monkeypatch, tmp_path: Path):
    names = _tool_names(monkeypatch, tmp_path, "clarify", interactive=False)
    assert "ask_user" not in names
    assert "record_fact" in names


def test_an_unknown_stage_raises(monkeypatch, tmp_path: Path):
    """A typo must not silently produce a toolless agent."""
    with pytest.raises(ValueError, match="architcet"):
        _tool_names(monkeypatch, tmp_path, "architcet")


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_stage_carries_the_permission_gate(monkeypatch, tmp_path: Path, stage: str):
    from rudra.config.loader import build_config, reset_config
    from rudra.permissions import build_gate

    captured = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr(
        "rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent
    )
    reset_config()
    try:
        gate = build_gate(build_config(tmp_path), tmp_path)
        create_planner_agent(
            task="t",
            project_path=tmp_path,
            filesystem_backend=object(),
            checkpointer=None,
            console=Console(quiet=True),
            gate=gate,
            stage=stage,
        )
    finally:
        reset_config()

    assert captured["middleware"][0] is gate.middleware
    assert captured["interrupt_on"] is gate.interrupt_on


def test_a_fact_recorded_in_clarify_reaches_the_architect(monkeypatch, tmp_path: Path):
    """The store is the only channel between stages (S10b.1)."""
    captured = {}

    def fake_create_deep_agent(**call):
        captured.update(call)
        return object()

    monkeypatch.setattr(
        "rudra.agent.planner_agent.create_deep_agent", fake_create_deep_agent
    )
    store = FactStore()
    store.record("language", "Rust", "the user asked for Rust", "asked")

    create_planner_agent(
        task="build a CLI",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
        facts=store,
        stage="architect",
    )

    assert "Rust" in captured["system_prompt"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planner_stages.py -q`
Expected: FAIL — `ImportError: cannot import name 'STAGES'`.

- [ ] **Step 3: Split the tool set by stage**

In `src/rudra/agent/planner_agent.py`, add above `create_planner_agent`:

```python
# The three stages, in the order run_loop runs them. C6.7's clarify ->
# architect -> task breakdown.
STAGES: tuple[str, ...] = ("clarify", "architect", "breakdown")


def _tools_for_stage(
    stage: str,
    *,
    ledger: Any,
    facts: Any,
    paths: Any,
    console: Console,
    cfg: Any,
    interactive: bool,
) -> list:
    """The tools one stage may call, and no others (S10b.1).

    Absence is the enforcement. A prompt telling the model to clarify
    before planning is a hint -- Step 7's acceptance run watched a model
    denied on write_file reach for `echo > /abs/path` instead -- so the
    breakdown stage simply has no way to ask a question, and the clarify
    stage no way to declare work.

    The single assembly point, for the reason subagents/build.py is one:
    two paths that decide a tool list will eventually disagree.
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)

    if stage == "breakdown":
        # No record_fact and no ask_user: by now the facts are settled,
        # and re-opening them mid-plan is what C6.8 exists to prevent.
        return create_ledger_tools(ledger, paths.ledger_json)

    interaction = create_interaction_tools(
        console,
        facts,
        paths.facts_json,
        max_questions=cfg.agent.max_questions,
        interactive=interactive and stage == "clarify",
    )
    # The architect reasons; it does not interrogate. Dropping ask_user by
    # filtering rather than by a second factory keeps one construction
    # path, so the budget counter cannot fork.
    if stage == "architect":
        return [tool for tool in interaction if tool.name != "ask_user"]
    return interaction
```

Then change `create_planner_agent`'s signature and body:

```python
def create_planner_agent(
    task: str,
    project_path: Path,
    filesystem_backend,
    checkpointer,
    console: Console,
    gate=None,
    ledger=None,
    paths=None,
    facts=None,
    interactive: bool = True,
    stage: str = "breakdown",
):
    """Create one stage of the planner (S10b.1).

    `ledger` and `facts` must be the SAME objects the loop reads: the
    stages mutate them in place, and a copy would leave the engine with no
    tasks and the coder with no facts. They are also the only channel
    between stages -- each stage gets its own thread, so nothing carries
    over except what was recorded.

    `interactive` False means nobody can answer, so ask_user is never
    registered (S10a.5). The prompt is built to match, so the model is
    never told to call a tool it does not have.

    `stage` defaults to "breakdown" because that is the behaviour every
    pre-10b caller expected: declare the work.
    """
```

Inside, replace the `custom_tools = ...` block with:

```python
    custom_tools = _tools_for_stage(
        stage,
        ledger=ledger,
        facts=facts,
        paths=paths,
        console=console,
        cfg=cfg,
        interactive=interactive,
    )
```

and pass the stage to the prompt builder (Task 3 gives `build_planner_prompt` its `stage` parameter; until then, leave the existing call and this task's prompt assertion will pass on the facts block alone):

```python
        system_prompt=build_planner_prompt(
            task,
            project_path,
            facts,
            can_ask=interactive and stage == "clarify" and cfg.agent.max_questions > 0,
            max_questions=cfg.agent.max_questions,
        ),
```

Add `STAGES` and `_tools_for_stage` to the module's `__all__` if it has one; if it does not, leave it (the module currently exports by convention).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planner_stages.py -q`
Expected: PASS, 13 tests.

Run: `uv run pytest -q`
Expected: `994 passed, 2 skipped` — no existing test breaks, because `stage` defaults to `"breakdown"` and every current caller wants exactly that.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/agent tests/test_planner_stages.py
git add src/rudra/agent/planner_agent.py tests/test_planner_stages.py
git commit -m "$(cat <<'EOF'
feat(planner): one stage, one tool set

The clarify stage cannot declare work, the breakdown stage cannot ask a
question, and neither can mark anything done. Enforced by what is
registered, not by what the prompt requests -- Step 7's acceptance run
watched a model denied on write_file reach for echo > /abs/path instead.

stage defaults to "breakdown", which is what every pre-10b caller
already expected, so nothing else changes yet.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: A prompt per stage

**Files:**
- Modify: `src/rudra/agent/planner_agent.py` — `build_planner_prompt` (`:29-106`)
- Test: `tests/test_planner_prompt.py` (extend), `tests/test_planner_stages.py` (extend)

**Interfaces:**
- Consumes: `facts_block(store)`, `project_tree(path)`, `STAGES` from Task 2.
- Produces: `build_planner_prompt(task, project_path, facts=None, *, stage="breakdown", can_ask=True, max_questions=5) -> str`. The existing keyword arguments keep their meaning.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_planner_stages.py`:

```python
# --- each stage's prompt states its own job and no other ---


def _prompt(tmp_path: Path, stage: str, **kwargs) -> str:
    from rudra.agent.planner_agent import build_planner_prompt

    return build_planner_prompt("build a web API", tmp_path, stage=stage, **kwargs)


def test_the_clarify_prompt_asks_for_facts_not_tasks(tmp_path: Path):
    prompt = _prompt(tmp_path, "clarify")
    assert "record_fact" in prompt
    assert "ask_user" in prompt
    assert "add_tasks" not in prompt, "clarify has no such tool; naming it invites a dead call"


def test_the_architect_prompt_asks_for_decisions_with_reasons(tmp_path: Path):
    prompt = _prompt(tmp_path, "architect")
    assert "record_fact" in prompt
    assert "ask_user" not in prompt
    assert "add_tasks" not in prompt
    for word in ("layout", "why"):
        assert word in prompt.lower()


def test_the_breakdown_prompt_is_the_task_one(tmp_path: Path):
    prompt = _prompt(tmp_path, "breakdown")
    assert "add_tasks" in prompt
    assert "record_fact" not in prompt
    assert "ask_user" not in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_prompt_claims_it_can_finish_anything(tmp_path: Path, stage: str):
    """S9c.1: the gate decides done, and every stage must say so."""
    prompt = _prompt(tmp_path, stage).lower()
    assert "cannot mark" in prompt or "gate" in prompt


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_no_stage_prompt_names_a_rudra_path(tmp_path: Path, stage: str):
    assert ".rudra" not in _prompt(tmp_path, stage)


def test_an_unattended_clarify_prompt_says_nobody_can_answer(tmp_path: Path):
    prompt = _prompt(tmp_path, "clarify", can_ask=False)
    assert "unattended" in prompt
    assert "ask_user" not in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planner_stages.py -q -k prompt`
Expected: FAIL — `build_planner_prompt() got an unexpected keyword argument 'stage'`.

- [ ] **Step 3: Write the three prompts**

Replace `build_planner_prompt` in `src/rudra/agent/planner_agent.py` with:

```python
_COMMON_HEADER = """You are a senior software architect and planning agent for Rudra.

You never write project code. A separate coder does that.
"""

_CANNOT_FINISH = """
## WHAT YOU CANNOT DO

You cannot mark anything done. A deterministic verification gate decides
that — it parses the code, type checks it, runs the tests, and scans for
placeholders. Do not claim anything is complete.
"""

_CLARIFY_BODY = """
## YOUR STAGE: ESTABLISH THE FACTS

This is the first of three stages. Yours is to settle what the work
depends on, before anyone decides how to build it or what to build.

Record every fact you establish with record_fact(), and say WHY you
believe it. The architect, the coder, the tester and the reviewer all
read those facts; one you keep to yourself is one they do not have.
"""

_CLARIFY_CAN_ASK = """
Ask only what you cannot infer from the request or the codebase. Batch
related questions into a SINGLE ask_user() call — one key per question.
You have {max_questions} questions for this whole run.
"""

_CLARIFY_UNATTENDED = """
This run is unattended: there is nobody to ask. Infer what you need from
the request and the codebase, and record each inference.
"""

_ARCHITECT_BODY = """
## YOUR STAGE: DECIDE HOW IT WILL BE BUILT

The facts above are settled. Yours is to decide the shape of the work,
before it is broken into tasks.

Decide and record, each with record_fact() and a WHY:
  - layout: which file holds what, and why that split
  - boundaries: what each module is responsible for
  - error handling: how failures are surfaced
  - tests: what is worth testing and at which level

Read the existing code first if there is any — a decision that fights the
project it lands in is worse than no decision.

Keep each one short and concrete. "src/parser.rs holds parsing, src/main.rs
holds the CLI" is a decision; three paragraphs about separation of concerns
is not. You cannot ask the user anything at this stage.
"""

_BREAKDOWN_BODY = """
## YOUR STAGE: BREAK IT INTO WORK

The facts above are settled, including the layout. Yours is to turn them
into tasks.

A task is a unit of WORK, described in plain language. Not a filename.

  GOOD: "write a CSV parser that handles quoted commas"
  GOOD: "write tests for the parser"
  GOOD: "add a --format flag to the CLI"
  BAD:  "parser.py"
  BAD:  "create the project structure"

One task may touch several files. Work that needs tests gets its own task.

Call add_tasks() ONCE with every task you can foresee, then STOP. Do not
add a task whose description is "verify" or "check": the gate does that on
its own.

You will be consulted again if a task fails or if the work runs out. When
that happens, add a task taking a DIFFERENT approach, or drop_task() one
that turned out to be unnecessary.
"""


def build_planner_prompt(
    task: str,
    project_path: Path,
    facts: Any = None,
    *,
    stage: str = "breakdown",
    can_ask: bool = True,
    max_questions: int = 5,
) -> str:
    """The system prompt for one planning stage (S10b.1).

    Each stage's prompt names only the tools that stage actually has.
    Naming a tool the model cannot call buys a dead call and a confused
    retry -- the same reasoning `_tools_for` follows when it raises on an
    unknown tool rather than dropping it (subagents/build.py:64-70).
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)

    prompt = f"""{_COMMON_HEADER}
## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    block = facts_block(facts)
    if block:
        prompt += f"\n{block}"

    prompt += f"""
## REQUEST
{task}
"""

    if stage == "clarify":
        prompt += _CLARIFY_BODY
        prompt += (
            _CLARIFY_CAN_ASK.format(max_questions=max_questions)
            if can_ask
            else _CLARIFY_UNATTENDED
        )
    elif stage == "architect":
        prompt += _ARCHITECT_BODY
    else:
        prompt += _BREAKDOWN_BODY

    prompt += _CANNOT_FINISH
    return prompt
```

Delete the old single-prompt body entirely — no stage may inherit the "NEVER call write_file()" line by accident, so keep it only where it belongs (it is covered by the gate regardless, and `_CANNOT_FINISH` carries the load-bearing half).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planner_stages.py tests/test_planner_prompt.py -q`
Expected: PASS. `tests/test_planner_prompt.py`'s existing cases assert the *breakdown* prompt (the default), so they must still pass unchanged; if one asserts wording this task moved, update that assertion to the stage it now belongs to — do not weaken it to "any stage".

Run: `uv run pytest -q`
Expected: `1000 passed, 2 skipped` or thereabouts.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/agent tests/
git add src/rudra/agent/planner_agent.py tests/
git commit -m "$(cat <<'EOF'
feat(planner): a prompt per stage

Each stage's prompt names only the tools that stage has. Naming one it
cannot call buys a dead call and a confused retry, which is why
_tools_for raises on an unknown tool rather than dropping it.

The architect prompt asks for short concrete decisions with reasons --
"src/parser.rs holds parsing" is a decision, three paragraphs on
separation of concerns is not. A 512-character fact value enforces that
anyway (S10b.2).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `consult_planner` takes a stage

**Files:**
- Modify: `src/rudra/agent/planner_agent.py` — `consult_planner` (`:345-380`)
- Test: `tests/test_planner_consult.py` (create)

**Interfaces:**
- Consumes: `_stream_planner_turn(agent, message, *, thread_id, gate, console)`.
- Produces: `consult_planner(agent, ledger, request, *, stage, reason="initial", task=None, gate, console, session_id)`. Thread id becomes `f"{session_id}-{stage}"`. Unknown stage or unknown reason raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_consult.py`:

```python
"""What each stage is actually asked (Step 10b)."""

from __future__ import annotations

import pytest
from rich.console import Console

from rudra.agent import planner_agent
from rudra.agent.planner_agent import consult_planner
from rudra.loop.ledger import Ledger


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    async def fake_turn(agent, message, *, thread_id, gate, console):
        calls.append({"message": message, "thread_id": thread_id})
        return True

    monkeypatch.setattr(planner_agent, "_stream_planner_turn", fake_turn)
    return calls


async def _consult(stage: str, **kwargs):
    await consult_planner(
        object(),
        Ledger(),
        "build a web API",
        stage=stage,
        gate=None,
        console=Console(quiet=True),
        session_id="sess",
        **kwargs,
    )


async def test_each_stage_gets_its_own_thread(captured):
    for stage in ("clarify", "architect", "breakdown"):
        await _consult(stage)

    assert [call["thread_id"] for call in captured] == [
        "sess-clarify",
        "sess-architect",
        "sess-breakdown",
    ]


async def test_each_stage_gets_a_different_message(captured):
    for stage in ("clarify", "architect", "breakdown"):
        await _consult(stage)

    messages = [call["message"] for call in captured]
    assert len(set(messages)) == 3
    assert all("build a web API" in message for message in messages)


async def test_a_block_re_enters_breakdown_with_the_failure(captured):
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.note = "typecheck failed: expected str, got int"

    await consult_planner(
        object(),
        ledger,
        "build it",
        stage="breakdown",
        reason="blocked",
        task=task,
        gate=None,
        console=Console(quiet=True),
        session_id="sess",
    )

    message = captured[0]["message"]
    assert task.id in message
    assert "typecheck failed" in message, "the blocker goes back verbatim"


async def test_an_empty_ledger_re_enters_breakdown(captured):
    await _consult("breakdown", reason="ledger_empty")
    assert "missing" in captured[0]["message"].lower()


async def test_an_unknown_stage_raises(captured):
    with pytest.raises(ValueError, match="architcet"):
        await _consult("architcet")


async def test_a_non_breakdown_stage_rejects_a_re_entry_reason(captured):
    """Clarify and architect run once. A blocked clarify is a bug, not a mode."""
    with pytest.raises(ValueError):
        await _consult("clarify", reason="blocked")
```

No async marker is needed: `pyproject.toml:103` sets `asyncio_mode = "auto"`, which is why `tests/test_loop_run.py`'s coroutines carry no decorator.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planner_consult.py -q`
Expected: FAIL — `consult_planner() got an unexpected keyword argument 'stage'`.

- [ ] **Step 3: Rewrite `consult_planner`**

Replace it in `src/rudra/agent/planner_agent.py`:

```python
_STAGE_MESSAGES = {
    "clarify": (
        "Establish what this work depends on, and record each fact with "
        "why you believe it. The request is:\n\n{request}"
    ),
    "architect": (
        "Decide how this will be built — layout, boundaries, error "
        "handling, tests — and record each decision with its reason. The "
        "request is:\n\n{request}"
    ),
    "breakdown": "Break this request into tasks: {request}",
}


async def consult_planner(
    agent: Any,
    ledger: Any,
    request: str,
    *,
    stage: str,
    reason: str = "initial",
    task: Any = None,
    gate: Any,
    console: Console,
    session_id: str,
) -> None:
    """Ask one planning stage to do its job. It mutates state via tools.

    Three stages run once each, in order, before any coder runs. Only
    `breakdown` is ever re-entered -- on a blocked task or an empty ledger
    (S10b.3). Clarify and architect are not: re-opening the questions
    after code exists costs a model call to churn decisions the coder has
    already built on.

    Each stage has its own thread, so the only thing that carries between
    them is what was recorded -- which is the point (S10b.1).
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)
    if reason != "initial" and stage != "breakdown":
        msg = f"stage {stage!r} runs once and cannot be re-entered (reason {reason!r})"
        raise ValueError(msg)

    if reason == "initial":
        message = _STAGE_MESSAGES[stage].format(request=request)
    elif reason == "ledger_empty":
        message = (
            "Every task is finished. Is anything missing before we stop? "
            "Call add_tasks if so; otherwise reply DONE and stop."
        )
    elif reason == "blocked" and task is not None:
        message = (
            f"Task {task.id} ({task.description}) failed and was given up on:\n\n"
            f"{task.note}\n\n"
            "Add a task taking a DIFFERENT approach, or drop_task it if it is "
            "not worth doing. If neither, reply DONE and stop."
        )
    else:  # pragma: no cover - guarded by the caller
        raise ValueError(f"unknown consult reason {reason!r}")

    await _stream_planner_turn(
        agent, message, thread_id=f"{session_id}-{stage}", gate=gate, console=console
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planner_consult.py -q`
Expected: PASS, 6 tests.

Run: `uv run pytest -q`
Expected: failures only in `tests/test_loop_run.py` and any test calling `consult_planner` without `stage`. Task 5 rewires the caller; do not patch it here.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/ && uv run ruff format src/rudra/agent tests/test_planner_consult.py
git add src/rudra/agent/planner_agent.py tests/test_planner_consult.py
git commit -m "$(cat <<'EOF'
feat(planner): consult_planner takes a stage

Three stages run once each, in order; only breakdown is ever re-entered,
on a blocked task or an empty ledger (S10b.3). A re-entry reason on
clarify or architect raises rather than being quietly honoured -- those
stages running twice would mean the caller has a bug, and churning the
architecture under a coder that already built on it is worse than
failing loudly.

Each stage gets its own thread, so the only thing carrying between them
is what was recorded.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The loop runs three stages

**Files:**
- Modify: `src/rudra/loop/engine.py` — `run_loop` (`:380-420`)
- Modify: `src/rudra/agent/main_agent.py` — `create_main_agent`'s planner construction and `planner_callback` (`:365-405`)
- Test: `tests/test_loop_run.py` (extend)

**Interfaces:**
- Consumes: `consult_planner(..., stage=..., reason=...)` from Task 4; `create_planner_agent(..., stage=...)` from Task 2.
- Produces: the planner callback's signature becomes `planner(ledger, request, *, stage, reason="initial", task=None)`. `run_loop` calls it three times before the work loop and only with `stage="breakdown"` afterwards.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_loop_run.py`:

```python
# --- Step 10b: three stages before any work ---


async def test_the_three_stages_run_in_order_before_any_task(monkeypatch, context):
    seen: list[tuple[str, str]] = []
    ran: list[str] = []

    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append((stage, reason))
        if stage == "breakdown" and reason == "initial":
            ledger.add("write it")

    async def fake_run_task(task, ledger, *, context):
        ran.append(task.id)
        task.status = TaskStatus.DONE
        return Outcome.DONE

    monkeypatch.setattr(engine, "run_task", fake_run_task)
    monkeypatch.setattr(engine, "review_once", _noop)

    await engine.run_loop("build it", context=context, planner=planner)

    assert seen[:3] == [
        ("clarify", "initial"),
        ("architect", "initial"),
        ("breakdown", "initial"),
    ]
    assert ran, "the work must still run after planning"


async def test_a_block_re_enters_breakdown_only(monkeypatch, context):
    seen: list[tuple[str, str]] = []

    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append((stage, reason))
        if stage == "breakdown" and reason == "initial":
            ledger.add("write it")

    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.BLOCKED))
    monkeypatch.setattr(engine, "review_once", _noop)

    await engine.run_loop("build it", context=context, planner=planner)

    after_planning = seen[3:]
    assert after_planning, "a block must consult the planner"
    assert all(stage == "breakdown" for stage, _ in after_planning)
    assert ("clarify", "blocked") not in seen
    assert ("architect", "blocked") not in seen


async def test_planning_runs_even_when_the_breakdown_declares_nothing(monkeypatch, context):
    """An empty plan is a real outcome, not a crash."""
    seen: list[tuple[str, str]] = []

    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append((stage, reason))

    monkeypatch.setattr(engine, "review_once", _noop)

    result = await engine.run_loop("build it", context=context, planner=planner)

    assert ("clarify", "initial") in seen
    assert result.success is False
    assert "0 requested" in result.message or "Tasks: 0" in result.message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_loop_run.py -q -k stages`
Expected: FAIL — `planner() got an unexpected keyword argument 'reason'` is *not* what you should see; expect instead `TypeError: planner() missing 1 required keyword-only argument: 'stage'`.

- [ ] **Step 3: Wire the loop and the factory**

In `src/rudra/loop/engine.py`, in `run_loop`, replace the single initial consult:

```python
    ledger = ledger if ledger is not None else Ledger()
    ledger.save(context.paths.ledger_json)

    # Three stages, in order, before any coder runs (C6.7, S10b.1): settle
    # the facts, decide the shape, then declare the work. Each is a
    # separate agent with its own tools, so a stage cannot do another
    # stage's job. Only `breakdown` is ever re-entered below.
    for stage in ("clarify", "architect", "breakdown"):
        await planner(ledger, request, stage=stage, reason="initial")

    consulted_on_empty = False
```

and update the two re-consults:

```python
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
```

```python
            await planner(ledger, request, stage="breakdown", reason="blocked", task=task)
```

Update `run_loop`'s docstring, which documents the callback's shape:

```python
    `planner` is an awaitable called as
    `planner(ledger, request, stage=..., reason=..., task=...)`; it adds or
    drops tasks and records facts through its tools and returns nothing.
    Injected rather than constructed here so the loop is testable without
    a model.
```

In `src/rudra/agent/main_agent.py`, replace the single `create_planner_agent` call with one per stage, and dispatch in the callback:

```python
    # One agent per stage (S10b.1). Construction is cheap -- build_model
    # makes no network call (llm/factory.py:64-68) and create_deep_agent
    # only compiles a graph -- and building all three up front keeps the
    # callback a lookup rather than a factory.
    #
    # Every stage shares the SAME ledger and fact store: they are the only
    # channel between stages, and a copy would leave the coder with an
    # architecture nobody recorded.
    planners = {
        stage: create_planner_agent(
            task=task,
            project_path=project_path,
            filesystem_backend=filesystem_backend,
            checkpointer=checkpointer,
            console=console,
            gate=gate,
            ledger=ledger,
            paths=paths,
            facts=facts,
            interactive=interactive,
            stage=stage,
        )
        for stage in STAGES
    }

    async def planner_callback(run_ledger, request, *, stage, reason="initial", task=None):
        await consult_planner(
            planners[stage],
            run_ledger,
            request,
            stage=stage,
            reason=reason,
            task=task,
            gate=gate,
            console=console,
            session_id=session_id,
        )
```

Change the import line to bring `STAGES` in:

```python
    from rudra.agent.planner_agent import STAGES, consult_planner, create_planner_agent
```

`RudraAgent` is constructed with `planner_agent=planner`; pass the breakdown agent so the attribute keeps meaning something:

```python
        planner_agent=planners["breakdown"],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop_run.py -q`
Expected: PASS. Existing tests in this file define their own `planner` stubs; each needs `stage` added to its signature — update them (`async def planner(ledger, request, *, stage=None, reason="initial", task=None)`), do not delete the assertions.

Run: `uv run pytest -q`
Expected: `≥ 1000 passed, 2 skipped`, zero failures.

Run: `uv run ruff check src/ tests/`
Expected: `All checks passed!` — ruff is what catches a dangling name in `create_main_agent`, which still has no unit coverage (the Step 6 lesson).

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/ tests/
git add src/rudra/loop/engine.py src/rudra/agent/main_agent.py tests/test_loop_run.py
git commit -m "$(cat <<'EOF'
feat(loop): clarify, then architect, then break down

run_loop consults the planner three times before any coder runs, and
only re-enters breakdown afterwards (S10b.3). The stages share one
ledger and one fact store by reference: those are the only channel
between them, and a copy would leave the coder with an architecture
nobody recorded.

All three agents are built up front so the callback is a lookup rather
than a factory. That is three model constructions for one role --
build_model is not cached, deliberately, and sharing one instance across
stages would hand three threads one object.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Documentation and ledger

**Files:**
- Modify: `CLAUDE.md` (§3 control flow, the architecture tree's `agent/` entry)
- Modify: `TODO.md` (`C6.7`, `C6.8`, `A1.72`, the §E Step 10b row)
- Modify: `Documentation/05-how-it-works.md` (the run walkthrough), `Documentation/08-project-status.md` (the clarifying-questions section 10a rewrote)

**Interfaces:** consumes the shipped behaviour of Tasks 1–5. Produces no code.

- [ ] **Step 1: Update `CLAUDE.md`**

In §3's control-flow list, replace step 1:

```markdown
1. **Planning runs in three stages before any code** (Step 10b, C6.7): `clarify`
   settles the facts, `architect` records layout and boundaries, `breakdown`
   declares the work. Each is a separate agent with its own tool set — clarify
   cannot `add_tasks`, breakdown cannot `ask_user` — so a stage cannot do
   another stage's job. The fact store is the only channel between them.
```

and in the re-consult bullet, state that only `breakdown` is re-entered.

- [ ] **Step 2: Update `TODO.md`**

- `C6.7` → **DONE**, citing the spec, the plan, the three stages, and the acceptance evidence from Task 7.
- `C6.8` → **DONE** — its tool half shipped in 10a, its sequencing half here. Say both halves explicitly, since the row was split across two steps.
- `A1.72` → **DONE** (Task 1).
- §E: mark **10b COMPLETE** with evidence, and note that 10c (`C6.9`) is unblocked.

- [ ] **Step 3: Update `Documentation/`**

- `05-how-it-works.md`: the run walkthrough currently goes straight from stack detection to "the planner plans". Replace that with the three stages, and say what each records.
- `08-project-status.md`: 10a rewrote "No clarifying questions" into "Clarifying questions are not yet staged". They are staged now — rewrite it again to describe the shipped behaviour, and say the remaining gap is plan approval (10c).

- [ ] **Step 4: Verify the docs match the code**

Run: `grep -rn "the planner plans\|not yet staged" Documentation/`
Expected: no hits.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md TODO.md Documentation/
git commit -m "$(cat <<'EOF'
docs: close C6.7, C6.8, A1.72; Step 10b complete

C6.8 spanned two steps by design: 10a shipped the tool contract
(batching, budget, provenance), 10b shipped the sequencing. Both halves
are named in the row so a future session does not go looking for the
other one.

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

Expected: `All checks passed!`; `N files already formatted`; `≥ 1000 passed, 2 skipped`. Record the real numbers. A count that *fell* means a test was deleted — name which and why.

- [ ] **Step 2: Acceptance run 1 — batching, through a real pty**

This is the evidence 10a could not produce: its run asked one combined question and was then cut short.

```bash
D=$(mktemp -d); cd "$D" && git init -q .
mkdir -p .rudra && cp <a known-good config.toml> .rudra/config.toml
python3 -c "import os,pty; os.chdir('$D'); pty.spawn(['/abs/path/.venv/bin/rudra','build a web API with one health endpoint'])"
cat "$D/.rudra/facts.json"
```

Expected: **one** `ask_user` call carrying several questions (not several calls carrying one each); answers recorded with `source: "asked"` and a `why` naming the question; the architect's facts present with reasons; the breakdown's tasks referencing those decisions. Quote `facts.json` and the `CALL ask_user` line verbatim into the TODO row.

- [ ] **Step 3: Acceptance run 2 — unattended, A1.72 proven live**

```bash
D=$(mktemp -d); cd "$D" && git init -q .
mkdir -p .rudra && cp <the same config.toml> .rudra/config.toml
env -i HOME="$HOME" PATH="$PATH" /abs/path/.venv/bin/rudra --auto --allow-shell \
  "build a CLI in Rust with clap that reverses its input string" 2>&1 | tee /tmp/10b-auto.log
grep -c "CALL ask_user" /tmp/10b-auto.log      # must be 0
python3 -c "import json;print(json.load(open('$D/.rudra/facts.json')))"
```

Expected: three stages visible in the trace; zero `ask_user` calls; **every** fact `source: "inferred"` (this is A1.72 proven live, not merely unit-tested); an architect layout fact present; and the coder's work consistent with it.

- [ ] **Step 4: Acceptance run 3 — brownfield**

Seed a repo with a committed source file and a `facts.json` recording its stack, then give a small request. Expected: the run does not re-ask what is recorded, and the architect builds on the existing facts rather than contradicting them.

- [ ] **Step 5: Record the evidence and commit**

Write the measured numbers and quoted output into `TODO.md`'s `C6.7` row and the §E Step 10b row. Log any defect the runs surface as a new `PENDING` row **before** fixing it — session rule 2 — continuing from the highest current `A1.x`.

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs: Step 10b acceptance evidence

Three runs: batching through a real pty (the evidence 10a could not
produce), an unattended run proving A1.72's coercion live, and a
brownfield run that does not re-ask what is already recorded.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage.** S10b.1 (three consults, one agent per stage) → Tasks 2, 4, 5. S10b.2 (architect persists facts) → Task 3's architect prompt; no new store appears in any task, which is the decision. S10b.3 (breakdown-only re-entry) → Task 4's guard and Task 5's call sites, tested in both. S10b.4 (clarify runs in every mode) → Task 2's `interactive` handling and Task 3's unattended prompt variant. S10b.5 (A1.72) → Task 1. Spec §4's failure table → Task 4's raises and Task 5's empty-plan test. Spec §5 tests → each task's own step. Spec §6 acceptance → Task 7. Spec §7 out-of-scope items appear in no task.

**Placeholder scan.** Every code step carries real code. Two steps deliberately point at the repo rather than quoting it — the existing `planner` stubs in Task 5 and the known-good `config.toml` in Task 7 — because each depends on something an implementer must read first; each names the exact command that finds it. A third deferral was removed during review: Task 4 had asked the implementer to work out the async marker convention, and `pyproject.toml:103` already answers it.

**Type consistency.** `create_planner_agent(..., stage: str = "breakdown")` is defined in Task 2 and called that way in Task 5. `build_planner_prompt(task, project_path, facts=None, *, stage=..., can_ask=..., max_questions=...)` is defined in Task 3 and called from Task 2's constructor with exactly those keywords. `consult_planner(..., stage=..., reason="initial", task=None, ...)` is defined in Task 4 and called from Task 5's callback with the same names. The callback signature `planner(ledger, request, *, stage, reason="initial", task=None)` is identical in Task 5's engine calls, its `main_agent` closure, and every test stub. `STAGES` is defined once in Task 2 and imported in Task 5.
