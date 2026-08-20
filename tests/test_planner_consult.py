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

    async def fake_turn(agent, message, *, thread_id, gate, console, trace=None):
        calls.append({"message": message, "thread_id": thread_id, "trace": trace})
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
