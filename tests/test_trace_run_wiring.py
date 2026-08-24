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


def _debug_logs(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "demo" / ".rudra" / "run" / "logs").glob("debug-*.jsonl"))


async def test_the_run_log_is_a_recorder_not_an_ordinary_consumer(tmp_path: Path) -> None:
    """OPEN-7: as an ordinary consumer it sat behind the sink's level
    filter and could not hold more than the console printed, so
    `--no-verbose` cut the bug-report log down to errors. Recorders are fed
    every event, which is the whole difference.

    Asserted as "one more than without it" rather than as a fixed count: a
    hard-coded number teaches people to update the number instead of
    checking the property."""
    plain = await _agent(tmp_path, debug=False)
    baseline_recorders = len(plain._loop_context.subagents.trace.recorders)
    baseline_consumers = len(plain._loop_context.subagents.trace.consumers)
    await plain.close()

    agent = await _agent(tmp_path, debug=True)
    try:
        trace = agent._loop_context.subagents.trace
        assert len(trace.recorders) == baseline_recorders + 1
        assert len(trace.consumers) == baseline_consumers, "not a rendering consumer"
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_the_run_log_writes_into_the_volatile_subtree(tmp_path: Path) -> None:
    """D15: beside permissions.jsonl, verify.log and usage.json."""
    import json

    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path, debug=True, verbose=True)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file", payload="{}")
        )
        _flush_debug_handlers()
        written = _debug_logs(tmp_path)
        assert len(written) == 1
        # Not line 0: a real run logs before the first event -- memory and
        # model setup both use the `rudra` tree, which is the point of
        # attaching to it rather than to a private logger.
        records = [json.loads(line) for line in written[0].read_text().splitlines() if line]
        assert any(record.get("name") == "write_file" for record in records)
        assert all("kind" in record for record in records), "one grammar per file"
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_the_run_log_is_named_for_the_run(tmp_path: Path) -> None:
    """One file per run, like the transcript -- which is what makes
    retention possible at all, and what stopped one file appending forever
    once this became always-on (OPEN-7)."""
    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path, debug=True)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file")
        )
        _flush_debug_handlers()
        assert _debug_logs(tmp_path)[0].stem == f"debug-{agent.session_id}"
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_the_run_log_is_written_with_no_flag_at_all(tmp_path: Path) -> None:
    """The request OPEN-7 came from: the run somebody needs a log of is
    never the run they remembered to pass a flag to."""
    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file")
        )
        _flush_debug_handlers()
        assert len(_debug_logs(tmp_path)) == 1
    finally:
        await agent.close()
        _detach_debug_handlers()


async def test_no_debug_suppresses_the_log_for_one_run(tmp_path: Path) -> None:
    agent = await _agent(tmp_path, debug=False)
    try:
        assert _debug_logs(tmp_path) == []
    finally:
        await agent.close()


async def test_the_config_key_suppresses_the_log(tmp_path: Path) -> None:
    """`[agent] debug_log = false` for a user who does not want it, with no
    flag on every invocation."""
    from rudra.config import get_config

    project = tmp_path / "demo"
    (project / ".rudra").mkdir(parents=True, exist_ok=True)
    (project / ".rudra" / "config.toml").write_text("[agent]\ndebug_log = false\n")

    # Seeded the way cli.py:1430 does. create_main_agent calls get_config()
    # with no argument, so the project root has to be resolved into the
    # process-wide Config before it -- that ordering IS the contract (A5.2),
    # and without it this reads tmp_path rather than tmp_path/demo.
    reset_config()
    get_config(project)

    agent = await _agent(tmp_path)
    try:
        assert _debug_logs(tmp_path) == []
    finally:
        await agent.close()


async def test_every_run_writes_a_transcript(tmp_path: Path) -> None:
    """Unlike --debug, this is not behind a flag: the point of a record is
    that it exists when somebody wants it, which is after the fact."""
    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file")
        )
        written = list((tmp_path / "demo" / ".rudra" / "run" / "transcripts").glob("*.jsonl"))
        assert len(written) == 1
        assert "write_file" in written[0].read_text()
    finally:
        await agent.close()


async def test_the_transcript_is_named_for_the_run(tmp_path: Path) -> None:
    from rudra.trace import TraceEvent, TraceKind

    agent = await _agent(tmp_path)
    try:
        agent._loop_context.subagents.trace.emit(
            TraceEvent(kind=TraceKind.TOOL_CALL, role="coder", name="write_file")
        )
        written = list((tmp_path / "demo" / ".rudra" / "run" / "transcripts").glob("*.jsonl"))
        assert written[0].stem == agent.session_id
    finally:
        await agent.close()


async def test_closing_the_agent_closes_the_transcript(tmp_path: Path) -> None:
    """An unflushed handle keeps the file locked on Windows, and a record
    nobody closed is a record somebody finds empty."""
    agent = await _agent(tmp_path)
    writer = agent._transcript
    await agent.close()

    assert writer._handle is None
