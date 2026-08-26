"""Session grants outlive one run (OPEN-30).

`build_gate` is called inside `create_main_agent` (main_agent.py), which the
REPL calls once per input (cli.py) -- so before this, `always` and
`auto-accept` were forgotten the moment the user pressed enter. The REPL
owns one SessionGrants and hands it to every turn.

Built through the factory, not `build_gate` directly: the factory is the
call the REPL actually makes, and it is the one that could drop the
argument on the floor by forwarding it into `**kwargs`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rudra.agent.main_agent import create_main_agent
from rudra.config import reset_config
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import Rule


@pytest.fixture(autouse=True)
def _coded_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Build against the shipped defaults, not the developer's .env."""
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


async def test_the_factory_gives_its_gate_the_grants_it_was_handed(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    grants = SessionGrants()
    agent = await create_main_agent(project_path=project, task="x", grants=grants)
    try:
        assert agent.gate.grants is grants
    finally:
        await agent.close()


async def test_auto_accept_taken_in_one_turn_holds_in_the_next(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    grants = SessionGrants()

    first = await create_main_agent(project_path=project, task="turn one", grants=grants)
    try:
        assert first.gate.engine.decide("write_file", {"file_path": "a.py"}).effect == "ask"
        grants.grant_all()
    finally:
        await first.close()

    second = await create_main_agent(project_path=project, task="turn two", grants=grants)
    try:
        assert second.gate.engine.decide("write_file", {"file_path": "a.py"}).effect == "allow"
    finally:
        await second.close()


async def test_an_always_grant_also_holds_in_the_next_turn(tmp_path: Path) -> None:
    """The widening the owner accepted: one object carries both."""
    project = tmp_path / "demo"
    project.mkdir()
    grants = SessionGrants()
    grants.add(Rule("execute", "pytest*"))

    agent = await create_main_agent(project_path=project, task="turn two", grants=grants)
    try:
        assert agent.gate.engine.decide("execute", {"command": "pytest -q"}).effect == "allow"
    finally:
        await agent.close()


async def test_a_run_given_no_grants_gets_its_own(tmp_path: Path) -> None:
    """Single-shot passes nothing and behaves exactly as it did before."""
    project = tmp_path / "demo"
    project.mkdir()
    agent = await create_main_agent(project_path=project, task="x")
    try:
        assert agent.gate.grants.approve_all is False
        assert len(agent.gate.grants) == 0
    finally:
        await agent.close()


# -- the REPL half ---------------------------------------------------------
#
# Driven through CliRunner with scripted input, the way test_cli_cancel.py
# reaches `_repl_session` -- it is a nested function, so there is no other
# way in without a terminal.


def _repl_with(monkeypatch, inputs, agent_factory):
    from typer.testing import CliRunner

    from rudra import cli as cli_module
    from rudra.cli import app

    remaining = list(inputs)

    async def scripted(session, prompt_text):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    monkeypatch.setattr(cli_module, "_prompt_input", scripted)
    monkeypatch.setattr(cli_module, "create_main_agent", agent_factory)
    monkeypatch.setattr(cli_module, "print_banner", lambda: None)
    return CliRunner().invoke(app, ["--auto"])


class _StubAgent:
    async def run(self):
        from rudra.agent.main_agent import AgentResult

        return AgentResult(success=True, message="done")

    async def close(self):
        return None


def test_every_repl_turn_is_handed_the_same_grants_object(monkeypatch, tmp_path):
    """One session, one SessionGrants -- otherwise `!` lasts one turn."""
    monkeypatch.chdir(tmp_path)
    seen: list[object] = []

    async def factory(**kwargs):
        seen.append(kwargs.get("grants"))
        return _StubAgent()

    _repl_with(monkeypatch, ["first task", "second task", "/exit"], factory)

    assert len(seen) == 2
    assert seen[0] is not None
    assert seen[0] is seen[1]


def test_the_repl_panel_reports_auto_accept_once_it_is_on(monkeypatch, tmp_path):
    """The panel prints the permission line every turn; after `!` the
    unqualified 'prompting before each write' would be a lie."""
    monkeypatch.chdir(tmp_path)

    async def factory(**kwargs):
        # What pressing `!` at a prompt during the first turn does.
        kwargs["grants"].grant_all()
        return _StubAgent()

    result = _repl_with(monkeypatch, ["first task", "second task", "/exit"], factory)

    # The token alone: the full sentence is wrapped by the Rich panel at 80
    # columns, so matching more than one word tests the terminal width.
    assert result.output.count("auto-accept") == 1
