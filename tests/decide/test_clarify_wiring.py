"""plan() skips the clarify stage when laya is confident (OPEN-176 wiring).

The clarify Vaayu turn is the first iteration of plan()'s stage loop
(`loop/engine.py`: ``for stage in ("clarify","architect","breakdown")``). The
gate must drop ONLY that iteration, and only when laya is confident; every
other path runs all three stages exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from rudra.decide.engine import Decision
from rudra.loop.engine import LoopContext, plan
from rudra.state.paths import rudra_paths


@dataclass
class _AgentCfg:
    max_fix_attempts: int = 3
    verbose: bool = False
    max_questions: int = 5


@dataclass
class _Cfg:
    agent: _AgentCfg = field(default_factory=_AgentCfg)
    models: dict = field(default_factory=dict)


@dataclass
class _Subagents:
    gate: object = None
    session_id: str = "s1"


class _FakeEngine:
    def __init__(self, decision: Decision) -> None:
        self._decision = decision

    def decide(self, state, questions):
        return {"needs_clarification": self._decision}


def _decision(*, value, abstain, route, backend="mlx") -> Decision:
    return Decision(
        question_id="needs_clarification",
        kind="noul",
        value=value,
        probabilities=None,
        prediction_set=None,
        abstain=abstain,
        route=route,
        confidence=0.9,
        backend=backend,
    )


_CONFIDENT = _decision(value=0.05, abstain=False, route="auto")
_ABSTAIN = _decision(value=0.4, abstain=True, route="review")


def _context(tmp_path, *, decide=None, interactive=True):
    return LoopContext(
        subagents=_Subagents(),
        project_path=tmp_path,  # empty dir -> is_greenfield True
        console=Console(quiet=True),
        cfg=_Cfg(),
        paths=rudra_paths(tmp_path),
        decide=decide,
        interactive=interactive,
    )


def _spy_planner(seen):
    async def planner(ledger, request, *, stage, reason="initial", task=None):
        seen.append(stage)
        if stage == "breakdown":
            ledger.add("write it")

    return planner


@pytest.mark.asyncio
async def test_clarify_skipped_when_engine_confident(tmp_path):
    seen: list[str] = []
    ctx = _context(tmp_path, decide=_FakeEngine(_CONFIDENT))
    await plan("build a flask hello world", context=ctx, planner=_spy_planner(seen))
    assert seen == ["architect", "breakdown"]


@pytest.mark.asyncio
async def test_clarify_runs_when_engine_abstains(tmp_path):
    seen: list[str] = []
    ctx = _context(tmp_path, decide=_FakeEngine(_ABSTAIN))
    await plan("make it better", context=ctx, planner=_spy_planner(seen))
    assert seen == ["clarify", "architect", "breakdown"]


@pytest.mark.asyncio
async def test_clarify_runs_when_no_engine(tmp_path):
    # Back-compat: no engine threaded -> behaviour byte-for-byte as before.
    seen: list[str] = []
    ctx = _context(tmp_path, decide=None)
    await plan("build it", context=ctx, planner=_spy_planner(seen))
    assert seen == ["clarify", "architect", "breakdown"]


@pytest.mark.asyncio
async def test_clarify_runs_when_not_interactive(tmp_path):
    # No-TTY/--auto path identical: clarify still records facts, never skipped.
    seen: list[str] = []
    ctx = _context(tmp_path, decide=_FakeEngine(_CONFIDENT), interactive=False)
    await plan("build a flask hello world", context=ctx, planner=_spy_planner(seen))
    assert seen == ["clarify", "architect", "breakdown"]
