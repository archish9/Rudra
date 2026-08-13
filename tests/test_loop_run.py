"""The outer loop: when the planner is consulted, and when the run stops."""

from __future__ import annotations

from dataclasses import dataclass, field

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


class FakePlanner:
    """Records why it was consulted; adds whatever it was scripted to add.

    Since Step 10b the loop consults three stages, but only `breakdown`
    declares work -- clarify and architect record facts, which these tests
    do not model. So only breakdown consumes the script and appends to
    `reasons`, which keeps every existing assertion about *why* the
    planner was consulted meaning what it meant before. `stages` records
    the whole sequence for the tests that care about it.
    """

    def __init__(self, script=None):
        self.reasons: list[str] = []
        self.stages: list[str] = []
        self.script = list(script or [])

    async def __call__(self, ledger, request, *, stage="breakdown", reason="initial", task=None):
        self.stages.append(stage)
        if stage != "breakdown":
            return
        self.reasons.append(reason)
        if self.script:
            for description in self.script.pop(0):
                ledger.add(description)


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


async def _noop(context, ledger=None):
    return None


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

    async def review(ctx, ledger=None):
        reviews.append(1)

    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", review)
    await run_loop("build it", context=context, planner=planner)
    assert len(reviews) == 1


async def test_the_reviewer_is_skipped_when_nothing_landed(monkeypatch, context):
    planner = FakePlanner([["a"], []])
    reviews: list[int] = []

    async def review(ctx, ledger=None):
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


async def test_a_caller_supplied_ledger_is_the_one_used(monkeypatch, context):
    # The planner's tools are bound to one Ledger object; run_loop must use
    # that same object or the tasks it adds are invisible to the engine.
    shared = Ledger()
    planner = FakePlanner([["a"], []])
    monkeypatch.setattr(engine, "run_task", _outcomes(Outcome.DONE))
    monkeypatch.setattr(engine, "review_once", _noop)
    await run_loop("build it", context=context, planner=planner, ledger=shared)
    assert [task.description for task in shared.tasks] == ["a"]
    assert shared.tasks[0].status is TaskStatus.DONE


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
