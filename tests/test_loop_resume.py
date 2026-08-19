"""A killed run picks up where it stopped (Step 12c, C7.2).

S12.2: `--continue` resumes the LEDGER, not the LangGraph thread. What a
user loses to a provider failure is completed work, and the ledger is the
record of that.
"""

from __future__ import annotations

from rudra.loop.ledger import Ledger, TaskStatus


def test_a_fresh_ledger_has_no_request():
    assert Ledger().request == ""


def test_save_records_the_request(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    assert Ledger.load(path).request == "build a JSON parser"


def test_save_without_a_request_keeps_the_one_already_there(tmp_path):
    """Every mid-run save passes no request and must not erase it."""
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    reloaded = Ledger.load(path)
    reloaded.tasks[0].status = TaskStatus.DONE
    reloaded.save(path)

    assert Ledger.load(path).request == "build a JSON parser"


def test_saving_stamps_the_time(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("t")
    ledger.save(path, request="r")

    assert Ledger.load(path).saved_at


def test_a_ledger_written_before_this_change_still_loads(tmp_path):
    """No migration: an old file loads with an empty request (S10a.8)."""
    import json

    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "t1", "description": "old", "status": "pending"}]}),
        encoding="utf-8",
    )

    ledger = Ledger.load(path)
    assert ledger.request == ""
    assert ledger.saved_at == ""
    assert len(ledger.tasks) == 1


def test_only_pending_tasks_are_resumable():
    """BLOCKED is not retried: it hit two identical failure signatures.

    Retrying it identically burns a run to reach the same place (C6.5a).
    """
    ledger = Ledger()
    done = ledger.add("already finished")
    pending = ledger.add("still to do")
    blocked = ledger.add("gave up")
    dropped = ledger.add("removed")

    done.status = TaskStatus.DONE
    blocked.status = TaskStatus.BLOCKED
    dropped.status = TaskStatus.DROPPED

    assert ledger.resumable() == (pending,)


def test_resumable_keeps_declaration_order():
    ledger = Ledger()
    first = ledger.add("one")
    second = ledger.add("two")
    assert ledger.resumable() == (first, second)


def test_attempts_survive_the_seam(tmp_path):
    """max_fix_attempts must still mean what it says across a resume."""
    path = tmp_path / "ledger.json"
    ledger = Ledger()
    task = ledger.add("half-tried")
    task.attempts = 2
    ledger.save(path, request="r")

    assert Ledger.load(path).tasks[0].attempts == 2


def test_a_resume_with_a_different_request_is_refused(tmp_path):
    """Working an old plan against a new intent is worse than refusing."""
    import pytest

    from rudra.agent.main_agent import ResumeRefused, check_resumable

    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    with pytest.raises(ResumeRefused) as caught:
        check_resumable(path, "build a YAML parser")

    assert "different request" in str(caught.value)


def test_a_resume_with_no_prompt_reuses_the_recorded_request(tmp_path):
    """`rudra --continue` alone is the normal form."""
    from rudra.agent.main_agent import check_resumable

    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    loaded = check_resumable(path, None)
    assert loaded.request == "build a JSON parser"


def test_a_resume_with_the_same_request_is_allowed(tmp_path):
    from rudra.agent.main_agent import check_resumable

    path = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.add("write the parser")
    ledger.save(path, request="build a JSON parser")

    assert check_resumable(path, "build a JSON parser").request == "build a JSON parser"


def test_a_resume_with_no_ledger_on_disk_is_refused(tmp_path):
    import pytest

    from rudra.agent.main_agent import ResumeRefused, check_resumable

    with pytest.raises(ResumeRefused) as caught:
        check_resumable(tmp_path / "nothing.json", None)

    assert "no previous run" in str(caught.value)


def test_a_resume_with_nothing_pending_is_refused(tmp_path):
    import pytest

    from rudra.agent.main_agent import ResumeRefused, check_resumable

    path = tmp_path / "ledger.json"
    ledger = Ledger()
    task = ledger.add("done already")
    task.status = TaskStatus.DONE
    ledger.save(path, request="r")

    with pytest.raises(ResumeRefused) as caught:
        check_resumable(path, None)

    assert "nothing pending" in str(caught.value)


def test_a_resume_with_only_blocked_tasks_says_so(tmp_path):
    """The message must not read as "nothing to do" when work was blocked."""
    import pytest

    from rudra.agent.main_agent import ResumeRefused, check_resumable

    path = tmp_path / "ledger.json"
    ledger = Ledger()
    task = ledger.add("gave up")
    task.status = TaskStatus.BLOCKED
    ledger.save(path, request="r")

    with pytest.raises(ResumeRefused) as caught:
        check_resumable(path, None)

    assert "blocked" in str(caught.value)


def test_plan_records_the_request_in_the_ledger(tmp_path):
    """The gap a live run found: nothing wrote the request.

    Task 1 gave the ledger a `request` field and check_resumable compares
    against it, but plan() saved without one -- so every real ledger had
    `"request": ""` and the mismatch refusal could never fire. Passing it
    at the one save that knows the request closes that.
    """
    import asyncio
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any

    from rich.console import Console

    from rudra.loop.engine import plan

    @dataclass
    class Paths:
        ledger_json: Path

    @dataclass
    class Ctx:
        paths: Any
        console: Console
        cfg: Any = None
        subagents: Any = None
        project_path: Any = None
        usage: Any = None

    ledger_path = tmp_path / "ledger.json"
    context = Ctx(paths=Paths(ledger_json=ledger_path), console=Console(quiet=True))

    async def planner(ledger, request, *, stage, reason="initial", task=None, feedback=""):
        return None

    asyncio.run(plan("build a JSON parser", context=context, planner=planner))

    assert Ledger.load(ledger_path).request == "build a JSON parser"
