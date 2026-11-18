"""The upgrade guard gate in run_task: block before the coder, override, supersede."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.context.usage import RunUsage
from rudra.loop import engine
from rudra.loop.engine import LoopContext, Outcome, run_task
from rudra.loop.ledger import Ledger, TaskStatus
from rudra.memory.kg import ProjectKG
from rudra.state.paths import rudra_paths
from rudra.subagents import SubagentResult
from rudra.verify.result import PASSED, StageResult, VerifyReport


@dataclass
class _Agent:
    max_fix_attempts: int = 3
    verbose: bool = False


@dataclass
class _Tools:
    test_timeout: int = 600
    proceed_known_bad: bool = False


@dataclass
class _Perms:
    mode: str = "ask"


@dataclass
class _Cfg:
    agent: _Agent = field(default_factory=_Agent)
    tools: _Tools = field(default_factory=_Tools)
    permissions: _Perms = field(default_factory=_Perms)
    models: dict = field(default_factory=dict)


@dataclass
class _Subagents:
    gate: object = None
    session_id: str = "s1"


def kg_with_adverse(tmp_path, package: str, version: str) -> ProjectKG:
    kg = ProjectKG(str(tmp_path / "palace"))
    kg.record_adverse_upgrade(
        package, version, run_id="oldrun", reason="broke 4 tests", when="2026-10-02"
    )
    return kg


def loop_context(tmp_path, *, kg=None, mode="ask", proceed=False) -> LoopContext:
    return LoopContext(
        subagents=_Subagents(),
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=_Cfg(permissions=_Perms(mode), tools=_Tools(proceed_known_bad=proceed)),
        paths=rudra_paths(tmp_path),
        usage=RunUsage(),
        kg=kg,
    )


@pytest.fixture
def dispatched(monkeypatch):
    calls: list[str] = []

    async def coder(name, prompt, *, context, thread_id=None):
        calls.append(name)
        return SubagentResult(name=name, text="wrote it", ok=True)

    monkeypatch.setattr(engine, "run_subagent", coder)
    monkeypatch.setattr(engine, "git_snapshot", lambda ctx: frozenset())
    monkeypatch.setattr(engine, "changed_since", lambda ctx, before: ("requirements.txt",))

    def passing(*args, **kwargs):
        return VerifyReport.from_stages([StageResult(name="test", outcome=PASSED, blocking=True)])

    monkeypatch.setattr(engine, "verify_project", passing)
    return calls


def answer(value: bool):
    async def resolve(decision, context):
        return value

    return resolve


async def run_upgrade(ctx):
    ledger = Ledger()
    task = ledger.add("Upgrade flask to 3.0")
    outcome = await run_task(task, ledger, context=ctx)
    return outcome, task


async def test_known_bad_blocks_before_any_coder_dispatch(tmp_path, monkeypatch, dispatched):
    ctx = loop_context(tmp_path, kg=kg_with_adverse(tmp_path, "flask", "3.0"))
    monkeypatch.setattr(engine, "resolve_known_bad", answer(False))
    outcome, task = await run_upgrade(ctx)
    assert outcome is Outcome.BLOCKED and dispatched == []
    assert task.status is TaskStatus.BLOCKED
    assert "oldrun" in task.note
    assert ctx.usage.per_role["coder"].upgrade_guards_fired == 1
    assert ctx.usage.per_role["coder"].upgrade_overrides == 0


async def test_override_proceeds_and_supersedes_on_pass(tmp_path, monkeypatch, dispatched):
    kg = kg_with_adverse(tmp_path, "flask", "3.0")
    ctx = loop_context(tmp_path, kg=kg)
    monkeypatch.setattr(engine, "resolve_known_bad", answer(True))
    outcome, _ = await run_upgrade(ctx)
    assert outcome is Outcome.DONE and dispatched
    assert kg.adverse_upgrades("flask") == []
    slot = ctx.usage.per_role["coder"]
    assert slot.upgrade_overrides == 1 and slot.kg_edges_written == 1


async def test_auto_mode_blocks_without_flag(tmp_path, dispatched):
    ctx = loop_context(tmp_path, kg=kg_with_adverse(tmp_path, "flask", "3.0"), mode="auto")
    outcome, _ = await run_upgrade(ctx)
    assert outcome is Outcome.BLOCKED and dispatched == []


async def test_auto_mode_proceeds_with_flag(tmp_path, dispatched):
    ctx = loop_context(
        tmp_path, kg=kg_with_adverse(tmp_path, "flask", "3.0"), mode="auto", proceed=True
    )
    outcome, _ = await run_upgrade(ctx)
    assert outcome is Outcome.DONE


async def test_empty_graph_never_gates(tmp_path, dispatched):
    ctx = loop_context(tmp_path, kg=ProjectKG(str(tmp_path / "palace")))
    outcome, _ = await run_upgrade(ctx)
    assert outcome is Outcome.DONE
    assert "coder" not in ctx.usage.per_role


async def test_no_kg_never_gates(tmp_path, dispatched):
    outcome, _ = await run_upgrade(loop_context(tmp_path, kg=None))
    assert outcome is Outcome.DONE


async def test_unanswered_prompt_declines(tmp_path, monkeypatch):
    """Attended, EOF/no answer is a decline, never a proceed."""
    from rudra.loop.upgrade_guard import evaluate_guard

    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: True)

    def cancelled(state, *, console, **kw):
        from rudra.ui import Cancelled

        return Cancelled()

    monkeypatch.setattr("rudra.ui.prompt.ask", cancelled)
    kg = kg_with_adverse(tmp_path, "flask", "3.0")
    ctx = loop_context(tmp_path, kg=kg, proceed=True)  # flag must not leak into ask mode
    decision = evaluate_guard(
        engine.detect_upgrade_intent("Upgrade flask to 3.0", {}), kg.adverse_upgrades("flask")
    )
    assert await engine.resolve_known_bad(decision, ctx) is False


async def test_guard_fails_open_when_detection_raises(tmp_path, monkeypatch, dispatched):
    ctx = loop_context(tmp_path, kg=kg_with_adverse(tmp_path, "flask", "3.0"))

    def boom(*args, **kwargs):
        raise RuntimeError("pathological input")

    monkeypatch.setattr(engine, "detect_upgrade_intent", boom)
    outcome, _ = await run_upgrade(ctx)
    assert outcome is Outcome.DONE and dispatched


async def test_attended_proceed_choice_returns_true(tmp_path, monkeypatch):
    from rudra.loop.upgrade_guard import detect_upgrade_intent, evaluate_guard
    from rudra.ui import Chosen

    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        "rudra.ui.prompt.ask", lambda state, *, console, **kw: Chosen(values=("proceed",))
    )
    kg = kg_with_adverse(tmp_path, "flask", "3.0")
    decision = evaluate_guard(
        detect_upgrade_intent("Upgrade flask to 3.0", {}), kg.adverse_upgrades("flask")
    )
    assert await engine.resolve_known_bad(decision, loop_context(tmp_path, kg=kg)) is True
