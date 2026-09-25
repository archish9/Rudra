"""What each stage is actually asked (Step 10b)."""

from __future__ import annotations

import pytest
from rich.console import Console

from rudra.agent import planner_agent
from rudra.agent.planner_agent import consult_planner
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.loop.tools import BLOCKED_RECOVERY


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    async def fake_turn(
        agent, message, *, thread_id, gate, console, trace=None, usage=None, telemetry=None
    ):
        calls.append(
            {
                "message": message,
                "thread_id": thread_id,
                "trace": trace,
                "usage": usage,
                "telemetry": telemetry,
            }
        )
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


async def test_a_block_never_tells_the_planner_to_drop_the_blocked_task(captured):
    """OPEN-24. This message used to end "or drop_task it if it is not worth
    doing", where `it` is the task run_task had already set to BLOCKED
    (engine.py:356,370) before work() consults at engine.py:792. drop_task
    refuses every _SETTLED status by construction (tools.py:24,92), so Rudra
    was ordering a call that cannot succeed -- and the planner issued it
    three times, correctly obeying its instructions.
    """
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.BLOCKED
    task.note = "typecheck failed"

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
    assert "add_tasks" in message, "the move that works has to be named"
    assert "cannot be dropped" in message, "and the one that cannot, ruled out"
    # The legal use of drop_task survives: a blocker can make a task that is
    # still PENDING pointless, and retracting those is the planner's job.
    assert "pending" in message.lower()


async def test_a_block_says_the_work_stays_and_asks_for_the_cause(captured):
    """OPEN-156. "it cannot be retried. Call add_tasks with a task taking a
    DIFFERENT approach" was answered, in two runs, by re-declaring the
    blocked work: reworded (4989aefefacb t22), split per file (t23, t24),
    or with "(write ... directly)" appended (e1a57a3e3791 t9-t13) -- two of
    those dispatched, 417.8 s, both blocked again on the unchanged cause.
    e1a's planner never saw a duplicate refusal, so this message is the
    common cause. It now says what the model was missing: the work is on
    disk and re-tested, so the move is a task that fixes the cause.
    """
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.BLOCKED
    task.note = "ModuleNotFoundError: No module named 'greenlet'"

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
    assert BLOCKED_RECOVERY in message
    assert "DIFFERENT approach" not in message


async def test_the_breakdown_prompt_does_not_ask_for_a_different_approach():
    """OPEN-156: the stage body told every consult the same thing up front."""
    prompt = planner_agent.build_planner_prompt("build it", tmp_ledger_path().parent)

    assert "DIFFERENT approach" not in prompt
    assert "cause" in prompt


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


async def test_stale_failures_asks_for_an_owner_not_a_different_approach(captured):
    """OPEN-23's second half. When every task is finished and the suite is
    still red, the failures belong to nobody -- no task owns them, so no
    coder will ever be handed them. This is the one consult that exists to
    give them an owner."""
    await _consult(
        "breakdown",
        reason="stale_failures",
        feedback="  tests/test_cli.py:119\n  tests/test_cli.py:138",
    )

    message = captured[0]["message"]
    assert "tests/test_cli.py:119" in message
    assert "tests/test_cli.py:138" in message
    assert "add_tasks" in message
    # It must not read like the `blocked` consult: nothing failed to be
    # done here, so "take a DIFFERENT approach" would be a wrong instruction.
    assert "DIFFERENT approach" not in message


async def test_stale_failures_needs_the_failure_list(captured):
    """An empty list means the caller had nothing to report and should not
    have spent a model call. Same guard `revision` carries, same reason."""
    with pytest.raises(ValueError, match="failure"):
        await _consult("breakdown", reason="stale_failures", feedback="   ")


async def test_stale_failures_cannot_re_enter_clarify_or_architect(captured):
    """The rule every non-initial reason obeys (S10b.3)."""
    for stage in ("clarify", "architect"):
        with pytest.raises(ValueError, match="runs once"):
            await _consult(stage, reason="stale_failures", feedback="  x.py:1")


# --- OPEN-65: every re-consult is shown the ledger it is writing into ---


def _ledger_with_history() -> Ledger:
    """run11's ledger at the moment its `ledger_empty` consult fired."""
    ledger = Ledger()
    for description in (
        "create the flask app in app.py",
        "define the Todo model in models.py",
        "write the routes blueprint",
        "write unit tests in test_app.py",
        "fix the delete endpoint's success message",
    ):
        ledger.add(description)
    for task in ledger.tasks:
        task.status = TaskStatus.DONE
    ledger.tasks[3].status = TaskStatus.BLOCKED
    return ledger


@pytest.mark.parametrize(
    ("reason", "extra"),
    [
        ("ledger_empty", {}),
        ("stale_failures", {"feedback": "  tests/test_app.py:11"}),
        ("revision", {"feedback": "add a task for the README"}),
    ],
)
async def test_every_re_consult_is_shown_the_ledger(captured, reason, extra):
    """OPEN-65. `ledger_empty` used to assert "Every task is finished" and
    interpolate nothing, into a thread whose last `read_ledger` snapshot was
    taken before the last task was worked. Measured over run8-run13
    (`docs/superpowers/plans/2026-08-31-open59-duplicate-rate.py` §4): six
    such consults, `read_ledger` called on none of them, and the one that
    added a task added a reworded duplicate of a task already DONE.
    """
    ledger = _ledger_with_history()

    await consult_planner(
        object(),
        ledger,
        "build it",
        stage="breakdown",
        reason=reason,
        gate=None,
        console=Console(quiet=True),
        session_id="sess",
        **extra,
    )

    message = captured[0]["message"]
    for task in ledger.tasks:
        assert task.id in message, f"{reason} hides {task.id}"
        assert task.description in message, f"{reason} hides what {task.id} was"
        assert task.status.value in message, f"{reason} hides that {task.id} is settled"


async def test_a_blocked_consult_is_shown_the_ledger_too(captured):
    """The blocked branch already names the one task it is about; the other
    four are what tell the planner whether its next task duplicates one."""
    ledger = _ledger_with_history()
    blocked = ledger.tasks[3]
    blocked.note = "typecheck failed"

    await consult_planner(
        object(),
        ledger,
        "build it",
        stage="breakdown",
        reason="blocked",
        task=blocked,
        gate=None,
        console=Console(quiet=True),
        session_id="sess",
    )

    message = captured[0]["message"]
    assert "typecheck failed" in message, "the blocker still goes back verbatim"
    for task in ledger.tasks:
        assert task.id in message


async def test_the_initial_breakdown_is_not_shown_an_empty_ledger(captured):
    """There is nothing to show, and a heading over no tasks is prompt spend
    that buys nothing. Every RE-consult has a ledger by construction."""
    await _consult("breakdown")
    assert "ledger" not in captured[0]["message"].lower()


async def test_the_ledger_shown_is_the_one_read_ledger_returns(captured):
    """One renderer, not two. A second copy would drift, and the planner
    would be shown a ledger in a spelling its own tool never produces."""
    from rudra.loop.tools import create_ledger_tools, render_ledger

    ledger = _ledger_with_history()
    tools = {tool.name: tool for tool in create_ledger_tools(ledger, tmp_ledger_path())}

    await consult_planner(
        object(),
        ledger,
        "build it",
        stage="breakdown",
        reason="ledger_empty",
        gate=None,
        console=Console(quiet=True),
        session_id="sess",
    )

    assert render_ledger(ledger) in captured[0]["message"]
    assert tools["read_ledger"].invoke({}) == render_ledger(ledger)


def tmp_ledger_path():
    import pathlib
    import tempfile

    return pathlib.Path(tempfile.mkdtemp()) / "ledger.json"
