"""What Rudra records without asking a model.

The data is already structured and already deterministic -- the ledger,
the VerifyReport, the FactStore, and files_touched from git rather than
from the model (S9c). The model is not consulted, which is the whole
point of the split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.loop.engine import record_block_memory, record_plan_memory, record_task_memory
from rudra.loop.ledger import Task, TaskStatus
from rudra.memory.store import MemoryStore


class _Ctx:
    def __init__(self, memory):
        self.memory = memory


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    project = tmp_path / "demo"
    project.mkdir()
    return MemoryStore(project)


def _task(**kw) -> Task:
    task = Task(id="t1", description="write greet.py")
    for key, value in kw.items():
        setattr(task, key, value)
    return task


def test_a_finished_task_is_recorded_with_its_files(store: MemoryStore) -> None:
    record_task_memory(_Ctx(store), _task(files_touched=["greet.py"], status=TaskStatus.DONE))
    hits = store.search("greet")
    assert hits
    assert "greet.py" in hits[0].content


def test_a_finished_task_is_recorded_by_rudra_not_the_agent(store: MemoryStore) -> None:
    record_task_memory(_Ctx(store), _task(files_touched=["greet.py"]))
    assert store.search("greet")[0].added_by == "rudra"


def test_a_task_that_touched_nothing_still_records_something_readable(
    store: MemoryStore,
) -> None:
    """An empty file list must not render as a dangling 'Files: .'"""
    record_task_memory(_Ctx(store), _task(files_touched=[]))
    assert "no files changed" in store.search("greet")[0].content


def test_a_blocked_task_records_its_blocker(store: MemoryStore) -> None:
    record_block_memory(
        _Ctx(store), _task(note="no progress: the same failure twice", status=TaskStatus.BLOCKED)
    )
    hits = store.search("same failure twice")
    assert hits
    assert hits[0].room == "blockers"


def test_the_approved_plan_records_its_facts_with_their_source(store: MemoryStore) -> None:
    from rudra.facts import FactStore

    facts = FactStore()
    facts.record("language", "Rust", why="the user asked for it", source="asked")
    record_plan_memory(store, facts, [_task()])
    hits = store.search("Rust")
    assert hits
    assert hits[0].room == "decisions"
    assert "asked" in hits[0].content


def test_a_context_with_no_memory_is_a_silent_no_op() -> None:
    """Every 9c-era test builds a LoopContext without a store, exactly as
    they do without a usage tally."""
    record_task_memory(_Ctx(None), _task())
    record_block_memory(_Ctx(None), _task())
    record_plan_memory(None, None, [])


def test_a_broken_store_never_raises_into_the_loop(tmp_path: Path) -> None:
    """The rule that matters: a task that genuinely finished must not be
    undone by a bookkeeping write (loop/engine.py:352-366)."""
    project = tmp_path / "demo"
    (project / ".rudra" / "memory").mkdir(parents=True)
    (project / ".rudra" / "memory" / "palace").write_text("not a directory")
    ctx = _Ctx(MemoryStore(project))
    record_task_memory(ctx, _task())
    record_block_memory(ctx, _task())
