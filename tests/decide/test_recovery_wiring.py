"""S5 Task 2: wiring laya recovery into the BLOCKED re-consult path.

``recover_or_consult`` replaces the bare ``planner(..., reason="blocked")`` call
at both sites (loop/engine.py work() + loop/parallel.py). When laya is confident
about a BLOCKED task's cause AND a safe deterministic recovery applies, it takes
that recovery and the planner re-consult is SKIPPED; otherwise the planner runs
exactly as today. The laya decision never runs inside run_task, so C6.5a and the
other bounds are untouched -- laya only acts AFTER a task is already BLOCKED.
"""

from pathlib import Path

import pytest

from rudra.decide.engine import Decision
from rudra.loop import engine as engine_mod
from rudra.loop.engine import recover_or_consult
from rudra.loop.ledger import Ledger, Task, TaskStatus


class _FakeEngine:
    def __init__(self, decision: Decision | None, *, available=True) -> None:
        self._decision = decision
        self._available = available
        self.called = False

    def available(self):
        return self._available

    def decide(self, state, questions):
        self.called = True
        return {"failure_class": self._decision}


def _choice(value, *, abstain=False, route="auto", backend="mlx") -> Decision:
    return Decision(
        question_id="failure_class",
        kind="choice",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=0.9,
        backend=backend,
    )


class _Planner:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, ledger, request, **kw):
        self.calls.append(kw)


def _ctx(engine, tmp_path):
    class _Paths:
        ledger_json = tmp_path / "ledger.json"

    class _Ctx:
        decide = engine
        project_path = tmp_path
        paths = _Paths()

    return _Ctx()


def _blocked_ledger(**task_kw) -> tuple[Ledger, Task]:
    task = Task(id="t1", description="build the thing", status=TaskStatus.BLOCKED, **task_kw)
    ledger = Ledger(tasks=[task], request="build the thing")
    # a pending task after it, so an inserted recovery task's position is testable
    ledger.tasks.append(Task(id="t2", description="later", status=TaskStatus.PENDING))
    return ledger, task


@pytest.mark.asyncio
async def test_confident_missing_dependency_inserts_dep_task_and_skips_planner(tmp_path):
    eng = _FakeEngine(_choice("missing_dependency"))
    ledger, task = _blocked_ledger(
        block_gate_output="No module named 'sqlalchemy'", block_packages=("sqlalchemy",)
    )
    planner = _Planner()
    await recover_or_consult(
        task,
        ledger,
        "build the thing",
        context=_ctx(eng, tmp_path),
        planner=planner,
        can_rerun=True,
    )
    assert planner.calls == []  # the Vaayu re-consult was skipped
    added = [t for t in ledger.tasks if t.unblocks == "t1"]
    assert len(added) == 1
    assert "sqlalchemy" in added[0].description
    # worked NEXT: inserted before the first pre-existing PENDING task (t2)
    assert ledger.tasks.index(added[0]) < ledger.tasks.index(ledger.get("t2"))


@pytest.mark.asyncio
async def test_abstain_falls_back_to_planner(tmp_path):
    eng = _FakeEngine(_choice("missing_dependency", abstain=True, route="review"))
    ledger, task = _blocked_ledger(block_gate_output="No module named 'x'", block_packages=("x",))
    planner = _Planner()
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert len(planner.calls) == 1
    assert planner.calls[0].get("reason") == "blocked"
    assert [t for t in ledger.tasks if t.unblocks == "t1"] == []


@pytest.mark.asyncio
async def test_missing_dependency_without_package_falls_back_to_planner(tmp_path):
    eng = _FakeEngine(_choice("missing_dependency"))
    ledger, task = _blocked_ledger(block_gate_output="ImportError: boom", block_packages=())
    planner = _Planner()
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert len(planner.calls) == 1


@pytest.mark.asyncio
async def test_unavailable_engine_falls_back_to_planner(tmp_path):
    eng = _FakeEngine(None, available=False)
    ledger, task = _blocked_ledger(block_gate_output="No module named 'x'", block_packages=("x",))
    planner = _Planner()
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert len(planner.calls) == 1
    assert eng.called is False  # no laya call when unavailable


@pytest.mark.asyncio
async def test_no_block_signal_falls_back_without_laya(tmp_path):
    # A task BLOCKED for a reason that produced no gate output (e.g. the parallel
    # redo cap) has no signal to classify -> re-consult, no laya call.
    eng = _FakeEngine(_choice("missing_dependency"))
    ledger, task = _blocked_ledger(block_gate_output="", block_packages=())
    planner = _Planner()
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert len(planner.calls) == 1
    assert eng.called is False


@pytest.mark.asyncio
async def test_flaky_rerun_passes_marks_done_and_skips_planner(tmp_path, monkeypatch):
    eng = _FakeEngine(_choice("flaky_or_environment"))
    ledger, task = _blocked_ledger(block_gate_output="ConnectionError: reset")
    planner = _Planner()

    class _Report:
        passed = True

    async def _fake_verify(t, c):
        return _Report()

    monkeypatch.setattr(engine_mod, "_verify", _fake_verify)
    monkeypatch.setattr(engine_mod, "_on_task_done", lambda *a, **k: None)
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert task.status is TaskStatus.DONE
    assert planner.calls == []


@pytest.mark.asyncio
async def test_flaky_rerun_still_failing_falls_back_to_planner(tmp_path, monkeypatch):
    eng = _FakeEngine(_choice("flaky_or_environment"))
    ledger, task = _blocked_ledger(block_gate_output="ConnectionError: reset")
    planner = _Planner()

    class _Report:
        passed = False
        blocker = None  # failure_keys short-circuits on a blocker-less report

    async def _fake_verify(t, c):
        return _Report()

    monkeypatch.setattr(engine_mod, "_verify", _fake_verify)
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=True
    )
    assert task.status is TaskStatus.BLOCKED
    assert len(planner.calls) == 1


@pytest.mark.asyncio
async def test_flaky_without_rerun_capability_falls_back_to_planner(tmp_path, monkeypatch):
    # Parallel path: the blocked task's worktree is gone, so the gate cannot be
    # re-run in the main tree. RERUN_GATE degrades to a re-consult.
    eng = _FakeEngine(_choice("flaky_or_environment"))
    ledger, task = _blocked_ledger(block_gate_output="ConnectionError: reset")
    planner = _Planner()

    async def _boom(t, c):
        raise AssertionError("_verify must not be called when can_rerun is False")

    monkeypatch.setattr(engine_mod, "_verify", _boom)
    await recover_or_consult(
        task, ledger, "r", context=_ctx(eng, tmp_path), planner=planner, can_rerun=False
    )
    assert len(planner.calls) == 1


def test_block_signal_is_masked_like_exception_lines():
    # The gate_output stored for laya is masked exactly as C6.5a's signature is
    # (absolute paths -> <path>, addresses -> <addr>): the loop owns the masking.
    from rudra.loop.engine import _record_block_signal

    class _Blocker:
        name = "test"
        output_tail = "E   RuntimeError: at /Users/x/proj/app.py object 0xdeadbeef failed"

    class _Report:
        blocker = _Blocker()

    class _Ctx:
        project_path = Path("/tmp/does-not-matter")

    task = Task(id="t1", description="x")
    _record_block_signal(task, _Report(), _Ctx())
    assert "<path>" in task.block_gate_output
    assert "<addr>" in task.block_gate_output
    assert "/Users/x/proj/app.py" not in task.block_gate_output


def test_block_signal_names_a_collection_import_error_package():
    # A pytest COLLECTION ImportError (outcome ERROR) is excluded by
    # missing_packages, but _record_block_signal must still name the package so
    # ADD_DEP_TASK can fire for the dominant missing-dependency shape (OPEN-177).
    from rudra.loop.engine import _record_block_signal

    class _Blocker:
        name = "test"
        outcome = "error"  # a collection error, NOT FAILED
        output_tail = (
            "E   ModuleNotFoundError: No module named 'rudra_fx_client'\n"
            "ImportError while importing test module"
        )
        findings = ()

    class _Report:
        blocker = _Blocker()
        passed = False

    class _Ctx:
        project_path = Path("/tmp/does-not-matter")

    task = Task(id="t1", description="x")
    _record_block_signal(task, _Report(), _Ctx())
    assert task.block_packages == ("rudra_fx_client",)


def test_both_sites_use_recover_or_consult():
    # Both block-consult sites route through the one helper so laya recovery is
    # not half-wired. The blocked-planner call survives only inside the helper
    # (engine.py, exactly once); parallel.py no longer calls the planner inline.
    src_root = Path(engine_mod.__file__).parent
    engine_src = (src_root / "engine.py").read_text()
    parallel_src = (src_root / "parallel.py").read_text()
    assert "recover_or_consult(" in engine_src
    assert "recover_or_consult(" in parallel_src
    blocked_planner = 'stage="breakdown", reason="blocked"'
    assert engine_src.count(blocked_planner) == 1  # only inside recover_or_consult
    assert blocked_planner not in parallel_src
