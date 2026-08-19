"""One store per run, reaching both the loop and the subagents.

The same object, not two: two MemoryStores would hold two ChromaDB
handles on one palace, which is the shape mine_palace_lock exists to
arbitrate and there is no reason to provoke.

Built through `create_main_agent`, not `RudraAgent(...)` directly.
RudraAgent's constructor takes eight assembled collaborators
(main_agent.py:130) and `agent.context` is an AgentContext, not the
SubagentContext -- that one lives at `loop_context.subagents`. The
factory is what a real run calls, so it is what this tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rudra.agent.main_agent import create_main_agent
from rudra.config import reset_config


@pytest.fixture(autouse=True)
def _coded_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build against the shipped defaults, not the developer's .env.

    Config.load() reads <cwd>/.env, so running from the repo root would
    build this against whatever backend the developer happens to have --
    the reason test_no_direct_provider_imports.py has the same fixture.
    """
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


async def test_the_run_builds_one_store_shared_by_loop_and_subagents(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="write greet.py")
    try:
        loop = agent._loop_context
        assert loop.memory is not None
        assert loop.subagents.memory is loop.memory
    finally:
        await agent.close()


async def test_the_store_points_at_this_projects_palace(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="x")
    try:
        assert str(project) in agent._loop_context.memory.palace_path
    finally:
        await agent.close()


async def test_an_unusable_project_name_does_not_stop_the_run(tmp_path: Path) -> None:
    """A wing that cannot be sanitized is a loud TaxonomyError from the
    store's constructor, and C8.6 says that costs memory, never the run."""
    project = tmp_path / "___"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="x")
    try:
        assert agent._loop_context.memory is None
    finally:
        await agent.close()
