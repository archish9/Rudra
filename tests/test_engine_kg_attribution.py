"""A blocked dependency-changing task files a structured adverse edge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from rudra.loop.engine import LoopContext, record_block_memory
from rudra.loop.ledger import Task, TaskStatus


class RecordingKG:
    def __init__(self) -> None:
        self.adverse: list[tuple[str, str, str, str]] = []

    def record_adverse_upgrade(self, package, version, *, run_id, reason, when) -> bool:
        self.adverse.append((package, version, run_id, reason))
        return True


def loop_context(kg=None, session_id: str = "run42") -> LoopContext:
    return LoopContext(
        subagents=SimpleNamespace(session_id=session_id),
        project_path=Path("."),
        console=None,
        cfg=None,
        paths=None,
        kg=kg,
    )


def blocked_task(description: str, files_touched: tuple[str, ...], note: str) -> Task:
    return Task(
        id="t1",
        description=description,
        status=TaskStatus.BLOCKED,
        files_touched=files_touched,
        note=note,
    )


def test_blocked_dep_upgrade_writes_adverse_edge() -> None:
    kg = RecordingKG()
    task = blocked_task("Upgrade flask to 3.0", ("requirements.txt",), "broke 4 tests\n...")
    record_block_memory(loop_context(kg=kg), task)
    assert kg.adverse == [("flask", "3.0", "run42", "broke 4 tests")]


def test_blocked_non_dep_task_writes_no_edge() -> None:
    kg = RecordingKG()
    record_block_memory(
        loop_context(kg=kg), blocked_task("fix the /health route", ("app.py",), "x")
    )
    assert kg.adverse == []


def test_no_kg_is_a_silent_noop() -> None:
    task = blocked_task("Upgrade flask to 3.0", ("requirements.txt",), "boom")
    record_block_memory(loop_context(kg=None), task)
