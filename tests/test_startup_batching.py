from __future__ import annotations

from pathlib import Path

import pytest

BASE = "http://localhost:8000/v1"


@pytest.fixture
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from rudra.config.loader import reset_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_config()
    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[permissions]\nmode = "auto"\n', encoding="utf-8"
    )
    yield tmp_path
    reset_config()


async def test_contexts_carry_limiter(hermetic: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from rudra.agent.main_agent import create_main_agent

    calls: list[object] = []

    def fake(cfg, **_kw):
        calls.append(cfg)
        return {BASE: 3}

    monkeypatch.setattr("rudra.agent.main_agent.resolve_endpoint_concurrency", fake)
    agent = await create_main_agent(hermetic, "task")
    try:
        loop_limiter = agent._loop_context.limiter
        assert loop_limiter.limit_for(BASE) == 3
        assert agent._loop_context.subagents.limiter is loop_limiter
        assert len(calls) == 1
    finally:
        await agent.close()
