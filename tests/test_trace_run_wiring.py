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


def _flush_debug_handlers() -> None:
    import logging

    for handler in logging.getLogger("rudra").handlers:
        handler.flush()


def _detach_debug_handlers() -> None:
    """The logger tree is process-global: a handler left attached writes a
    later test's records into a deleted tmp_path."""
    import logging

    logger = logging.getLogger("rudra")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True


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


async def test_debug_registers_a_second_consumer_on_the_same_sink(tmp_path: Path) -> None:
    """C9.7: the file and the screen read the SAME events, so they cannot
    disagree about what happened."""
    agent = await _agent(tmp_path, debug=True)
    try:
        sink = agent._loop_context.subagents.trace
        assert len(sink.consumers) == 2
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_debug_writes_into_the_volatile_subtree(tmp_path: Path) -> None:
    """D15: beside permissions.jsonl, verify.log and usage.json."""
    import json

    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path, debug=True, verbose=True)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file", payload="{}")
        )
        _flush_debug_handlers()
        log = tmp_path / "demo" / ".rudra" / "run" / "logs" / "debug.jsonl"
        assert log.exists()
        # Not line 0: a real run logs before the first event -- memory and
        # model setup both use the `rudra` tree, which is the point of
        # attaching to it rather than to a private logger.
        records = [json.loads(line) for line in log.read_text().splitlines() if line]
        assert any(record.get("name") == "write_file" for record in records)
        assert all("kind" in record for record in records), "one grammar per file"
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_without_the_flag_no_log_is_written(tmp_path: Path) -> None:
    agent = await _agent(tmp_path)
    try:
        assert not (tmp_path / "demo" / ".rudra" / "run" / "logs" / "debug.jsonl").exists()
    finally:
        await agent.close()
