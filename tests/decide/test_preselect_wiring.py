"""OPEN-179 Task 3: wire the MCP + skill hints into the coder/tester prompt.

The helpers (``mcp_hint``, ``skill_topk``) are proven in test_gate_mcp /
test_gate_skills. This covers the wiring: the hint blocks render into
``_prompt_for``, a hint-free build is byte-identical to today, and
``resolve_prompt_hints`` computes both off the per-run engine at dispatch
(never raising, degrading to no hint when the engine is unavailable).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rudra.decide.engine import Decision
from rudra.decide.gates import McpHint
from rudra.subagents.build import (
    PromptHints,
    _mcp_hint_block,
    _prompt_for,
    _skills_hint_block,
    resolve_prompt_hints,
)
from rudra.subagents.registry import REGISTRY

# --- block renderers ---------------------------------------------------------


def test_mcp_hint_block_names_tool_schema_and_skip_instruction():
    hint = McpHint(
        tool_id="mempalace__search",
        description="search long-term memory",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    block = _mcp_hint_block(hint)
    assert "mempalace__search" in block
    assert "search long-term memory" in block
    assert "call_mcp_tool" in block
    # tells the agent it can skip the exploration meta-tools
    assert "list_mcp_tools" in block and "describe_mcp_tool" in block
    # the schema is inlined so the model can write arguments without describe
    assert '"query"' in block


def test_skills_hint_block_lists_names_and_empty_is_blank():
    block = _skills_hint_block(["test-driven-development", "systematic-debugging"])
    assert "test-driven-development" in block
    assert "systematic-debugging" in block
    assert _skills_hint_block([]) == ""


# --- _prompt_for wiring ------------------------------------------------------


class _Ctx:
    """Bare duck context -- _prompt_for reads these via getattr."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path


def test_prompt_for_without_hints_is_unchanged(tmp_path):
    spec = REGISTRY["coder"]
    base = _prompt_for(spec, _Ctx(tmp_path), "do a thing")
    # default and an empty PromptHints both leave the prompt byte-identical
    assert _prompt_for(spec, _Ctx(tmp_path), "do a thing", hints=None) == base
    assert _prompt_for(spec, _Ctx(tmp_path), "do a thing", hints=PromptHints()) == base


def test_prompt_for_appends_both_hint_blocks(tmp_path):
    spec = REGISTRY["coder"]
    hints = PromptHints(
        mcp=McpHint("kala__verify", "verify a design", {"type": "object"}),
        skills=("test-driven-development", "systematic-debugging"),
    )
    out = _prompt_for(spec, _Ctx(tmp_path), "verify the design", hints=hints)
    assert "kala__verify" in out
    assert "test-driven-development" in out
    assert "systematic-debugging" in out


# --- resolve_prompt_hints ----------------------------------------------------


class _FakeEngine:
    def __init__(self, *, available: bool, answers: dict) -> None:
        self._available = available
        self._answers = answers
        self.calls = 0

    def available(self) -> bool:
        return self._available

    def decide(self, state, questions):
        self.calls += 1
        # questions has exactly one qid; return its pre-built answer
        qid = next(iter(questions))
        return {qid: self._answers[qid]}


def _skill_decision(value, probabilities):
    return Decision(
        question_id="skills_topk",
        kind="choice",
        value=value,
        probabilities=probabilities,
        prediction_set=None,
        abstain=False,
        route="auto",
        confidence=0.9,
        backend="mlx",
    )


def _write_skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: the {name} skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )


class _SkillCtx:
    def __init__(self, engine, source: Path) -> None:
        self.decide = engine
        self.skills_sources = (str(source),)
        self.mcp = None  # no MCP in this context -> only the skill half runs


def test_resolve_populates_skills_from_sources(tmp_path):
    src = tmp_path / "skills"
    for n in ("test-driven-development", "systematic-debugging", "writing-plans"):
        _write_skill(src, n)
    probs = {"writing-plans": 0.8, "systematic-debugging": 0.15, "test-driven-development": 0.05}
    eng = _FakeEngine(
        available=True, answers={"skills_topk": _skill_decision("writing-plans", probs)}
    )

    hints = asyncio.run(resolve_prompt_hints(REGISTRY["coder"], _SkillCtx(eng, src), "plan it"))

    assert "writing-plans" in hints.skills
    # the process floor present on disk is always kept
    assert "test-driven-development" in hints.skills
    assert hints.mcp is None


def test_resolve_returns_empty_when_engine_unavailable(tmp_path):
    src = tmp_path / "skills"
    _write_skill(src, "test-driven-development")
    eng = _FakeEngine(available=False, answers={})

    hints = asyncio.run(resolve_prompt_hints(REGISTRY["coder"], _SkillCtx(eng, src), "x"))

    assert hints == PromptHints()
    assert eng.calls == 0


def test_resolve_returns_empty_for_blank_task(tmp_path):
    src = tmp_path / "skills"
    _write_skill(src, "test-driven-development")
    eng = _FakeEngine(available=True, answers={})

    hints = asyncio.run(resolve_prompt_hints(REGISTRY["coder"], _SkillCtx(eng, src), "   "))

    assert hints == PromptHints()
    assert eng.calls == 0


def test_resolve_never_raises_on_a_broken_context(tmp_path):
    # A context whose skills_sources point nowhere and whose engine raises must
    # degrade to no hint, never fail the dispatch (CLAUDE.md 8a).
    class _Boom:
        def available(self):
            return True

        def decide(self, state, questions):
            raise RuntimeError("laya exploded")

    class _Ctx2:
        decide = _Boom()
        skills_sources = ("/does/not/exist",)
        mcp = None

    hints = asyncio.run(resolve_prompt_hints(REGISTRY["coder"], _Ctx2(), "x"))
    assert hints == PromptHints()
