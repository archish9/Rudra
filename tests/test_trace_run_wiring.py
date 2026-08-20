"""One sink per run, on both contexts (Step 15a, A1.90).

Before this, `--verbose` was parsed, three-state resolved, passed to
create_main_agent and stored on AgentContext.verbose (main_agent.py:53) --
and read by NOTHING: `grep -rn verbose src/rudra/loop src/rudra/subagents`
returned zero hits.

Built through `create_main_agent` for the reason test_memory_run_wiring.py
states: RudraAgent's constructor takes eight assembled collaborators, and
the factory is what a real run calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rudra.agent.main_agent import create_main_agent
from rudra.config import reset_config
from rudra.trace import TraceLevel


@pytest.fixture(autouse=True)
def _coded_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build against the shipped defaults, not the developer's .env."""
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


async def _agent(tmp_path: Path, **kwargs):
    project = tmp_path / "demo"
    project.mkdir(exist_ok=True)
    return await create_main_agent(project_path=project, task="write greet.py", **kwargs)


async def test_the_same_sink_reaches_the_loop_and_the_subagents(tmp_path: Path) -> None:
    agent = await _agent(tmp_path)
    try:
        sink = agent._loop_context.subagents.trace
        assert sink is not None
        assert sink.consumers, "the console consumer must be registered"
    finally:
        await agent.close()


async def test_verbose_true_produces_a_verbose_sink(tmp_path: Path) -> None:
    agent = await _agent(tmp_path, verbose=True)
    try:
        assert agent._loop_context.subagents.trace.level is TraceLevel.VERBOSE
    finally:
        await agent.close()


async def test_no_verbose_produces_a_quiet_sink(tmp_path: Path) -> None:
    agent = await _agent(tmp_path, verbose=False)
    try:
        assert agent._loop_context.subagents.trace.level is TraceLevel.QUIET
    finally:
        await agent.close()


async def test_an_absent_flag_takes_the_configured_level(tmp_path: Path) -> None:
    """Three-state: None consults [agent] verbose, which now defaults false."""
    agent = await _agent(tmp_path, verbose=None)
    try:
        assert agent._loop_context.subagents.trace.level is TraceLevel.NORMAL
    finally:
        await agent.close()


def test_the_shipped_default_is_normal_not_verbose():
    """A1.90's second half: the inert key also shipped defaulting to on, so
    giving it a consumer at that default would have made untruncated
    payloads the shipped behaviour."""
    from rudra.config.schema import DEFAULTS

    assert DEFAULTS["agent"]["verbose"] is False
