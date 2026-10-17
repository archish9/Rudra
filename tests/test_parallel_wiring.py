"""create_main_agent wires the three worktree-parallel factories (Task 9b).

The factories are ALWAYS set; `_should_parallelize` keeps the path dormant for
a cap-1 endpoint (today's default), so wiring them must not change serial
behaviour. These tests build a real agent (hermetically -- no network, no
model, no git) and check the factories are callable, that worktrees land
OUTSIDE the project, that run_main_gate never raises, and that a cap-1 config
still refuses to parallelize.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose XDG state base is a SIBLING, so worktrees are outside it.

    Unlike test_startup_batching's fixture (which nests XDG_STATE_HOME inside
    the project), the worktrees root must be outside the project tree -- so the
    project and the state home are separate directories here, as they are in a
    real install (`~/.local/state` vs. the repo).
    """
    from rudra.config.loader import reset_config

    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_config()
    (project / ".rudra").mkdir(parents=True, exist_ok=True)
    (project / ".rudra" / "config.toml").write_text(
        '[permissions]\nmode = "auto"\n', encoding="utf-8"
    )
    yield project
    reset_config()


async def test_factories_set_and_callable(wired: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deepagents.backends.composite import CompositeBackend

    from rudra.agent.main_agent import create_main_agent
    from rudra.loop.ledger import Task

    # Hermetic: no startup hint probe.
    monkeypatch.setattr("rudra.agent.main_agent.resolve_endpoint_concurrency", lambda cfg, **_k: {})

    agent = await create_main_agent(wired, "task")
    try:
        ctx = agent._loop_context

        # All three are wired -- not None.
        assert callable(ctx.reroot)
        assert callable(ctx.make_worktree)
        assert callable(ctx.run_main_gate)

        # make_worktree: a leaf OUTSIDE the project, parent made, leaf NOT made.
        wt = ctx.make_worktree(Task(id="t1", description="x"))
        assert not wt.is_relative_to(wired.resolve())
        assert wt.parent.exists()
        assert not wt.exists()

        # run_main_gate: a frozenset, never raising.
        keys = ctx.run_main_gate()
        assert isinstance(keys, frozenset)

        # reroot: a (backend, gate) tuple rooted at the worktree, sharing the
        # main gate's grants object (one policy; only the root differs).
        wt_dir = wired.parent / "wt-sample"
        backend, gate = ctx.reroot(wt_dir)
        assert isinstance(backend, CompositeBackend)
        assert Path(backend.default.cwd) == wt_dir.resolve()
        assert gate.grants is agent.gate.grants
    finally:
        await agent.close()


async def test_cap_one_stays_serial(wired: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rudra.agent.main_agent import create_main_agent
    from rudra.loop.engine import _should_parallelize, effective_fanout

    # No batching hint -> every endpoint reads as 1.
    monkeypatch.setattr("rudra.agent.main_agent.resolve_endpoint_concurrency", lambda cfg, **_k: {})

    agent = await create_main_agent(wired, "task")
    try:
        ctx = agent._loop_context
        # Factories are wired, but the fan-out is 1, so the parallel path is
        # dormant -- the serial body runs exactly as before.
        assert effective_fanout(ctx) == 1
        assert _should_parallelize(ctx) is False
    finally:
        await agent.close()
