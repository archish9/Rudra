"""OPEN-180: run entry requires laya unless [decide] enabled = false."""

from types import SimpleNamespace

import pytest
from rich.console import Console

from rudra.agent.main_agent import AgentContext, RudraAgent
from rudra.decide import gate

pytestmark = pytest.mark.anyio


def _cfg(enabled: bool = True):
    return SimpleNamespace(decide=SimpleNamespace(enabled=enabled, backend="auto"))


def test_blocks_when_enabled_and_missing(monkeypatch):
    monkeypatch.setattr(gate, "laya_installed", lambda cfg: False)
    msg = gate.require_laya(_cfg(True))
    assert msg is not None and "Documentation/16-laya.md" in msg


def test_passes_when_installed(monkeypatch):
    monkeypatch.setattr(gate, "laya_installed", lambda cfg: True)
    assert gate.require_laya(_cfg(True)) is None


def test_disabled_lifts_the_gate(monkeypatch):
    monkeypatch.setattr(gate, "laya_installed", lambda cfg: False)
    assert gate.require_laya(_cfg(False)) is None


class _Trace:
    def __init__(self) -> None:
        self.notices: list[str] = []

    def notice(self, payload: str, *, role: str, name: str = "", **kw) -> None:
        self.notices.append(f"{name}:{payload}")


def _agent(tmp_path, trace) -> RudraAgent:
    return RudraAgent(
        context=AgentContext(project_path=tmp_path, task="t", console=Console(quiet=True)),
        session_id="abc123def456",
        db_conn=None,
        loop_context=SimpleNamespace(subagents=SimpleNamespace(trace=trace)),
        planner_callback=None,
        ledger=None,
    )


async def test_run_blocks_before_planning(tmp_path, monkeypatch):
    from rudra.agent import main_agent

    monkeypatch.setattr(main_agent, "get_config", lambda: _cfg(True))
    monkeypatch.setattr(gate, "laya_installed", lambda cfg: False)
    trace = _Trace()
    agent = _agent(tmp_path, trace)
    called = []

    async def _no(*a, **k):
        called.append(1)

    monkeypatch.setattr(agent, "_run", _no)
    result = await agent.run()
    assert result.success is False
    assert "Documentation/16-laya.md" in result.message
    assert called == []
    assert any(n.startswith("laya-gate:") for n in trace.notices)
    assert any(n.startswith("run:") and "ended: failed" in n for n in trace.notices)


def test_real_probe_through_real_gate(monkeypatch):
    """No stub on laya_installed: the real probe feeds the real gate."""
    import sys

    from rudra.decide import backends

    monkeypatch.setattr(gate, "laya_installed", backends.laya_installed)
    monkeypatch.setitem(sys.modules, "laya", None)
    monkeypatch.setitem(sys.modules, "laya_mlx", None)
    msg = gate.require_laya(_cfg(True))
    assert msg is not None and "Documentation/16-laya.md" in msg
    assert gate.require_laya(_cfg(False)) is None


async def test_run_proceeds_when_installed_or_disabled(tmp_path, monkeypatch):
    from rudra.agent import main_agent
    from rudra.agent.main_agent import AgentResult

    for enabled, installed in ((True, True), (False, False)):
        monkeypatch.setattr(main_agent, "get_config", lambda e=enabled: _cfg(e))
        monkeypatch.setattr(gate, "laya_installed", lambda cfg, i=installed: i)
        agent = _agent(tmp_path, _Trace())

        async def _ok():
            return AgentResult(True, "ran")

        monkeypatch.setattr(agent, "_run", _ok)
        result = await agent.run()
        assert result.success is True and result.message == "ran"
