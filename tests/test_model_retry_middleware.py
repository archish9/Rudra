"""OPEN-41: the retry that protects a SINGLE model call, not a whole graph.

`_stream_with_retry` (permissions/approval.py:193) wraps one
`agent.astream` -- dozens of model calls -- and stops retrying the moment a
chunk is out. So of run7's 119 model calls, exactly one was protected. On a
33% failure hour that makes a clean run impossible, and no amount of
re-running fixes it.

This middleware closes the gap at the granularity that matters. It is not a
second policy: `is_transient` and `retry_delays` are the same ones the
stream-level retry uses.
"""

from __future__ import annotations

import pytest

from rudra.llm.retry import ProviderUnavailable
from rudra.middleware.model_retry import ModelRetryMiddleware
from rudra.trace import TraceLevel


class _Status(Exception):
    """A provider error carrying an HTTP status, the way openai's do."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Error code: {code}")
        self.status_code = code


class _Handler:
    """Fails `failures` times, then returns a sentinel."""

    def __init__(self, failures: int, error: BaseException) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise self.error
        return "response"

    async def acall(self, request):
        return self(request)


@pytest.fixture
def instant(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real backoff -- the delays are tested in test_llm_retry.py."""
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])


# --- the sync half -----------------------------------------------------------


def test_a_transient_model_failure_is_retried(instant) -> None:
    handler = _Handler(failures=2, error=_Status(500))

    result = ModelRetryMiddleware("coder").wrap_model_call({}, handler)

    assert result == "response"
    assert handler.calls == 3


def test_a_non_transient_model_failure_is_not_retried(instant) -> None:
    """A bad key fails identically every time; retrying only delays it."""
    handler = _Handler(failures=1, error=_Status(401))

    with pytest.raises(_Status):
        ModelRetryMiddleware("coder").wrap_model_call({}, handler)

    assert handler.calls == 1


def test_exhausted_model_retries_raise_a_readable_error(instant) -> None:
    handler = _Handler(failures=99, error=_Status(503))

    with pytest.raises(ProviderUnavailable) as excinfo:
        ModelRetryMiddleware("coder").wrap_model_call({}, handler)

    assert handler.calls == 4
    assert "coder" in str(excinfo.value)
    assert "503" in str(excinfo.value)


def test_a_successful_first_call_is_not_retried(instant) -> None:
    handler = _Handler(failures=0, error=_Status(500))

    assert ModelRetryMiddleware("coder").wrap_model_call({}, handler) == "response"
    assert handler.calls == 1


# --- the async half, which is the one a run actually takes -------------------


@pytest.mark.asyncio
async def test_the_async_half_retries_too(instant) -> None:
    """Rudra invokes every agent through `astream`, so this is the live path."""
    handler = _Handler(failures=2, error=_Status(500))

    result = await ModelRetryMiddleware("planner").awrap_model_call({}, handler.acall)

    assert result == "response"
    assert handler.calls == 3


@pytest.mark.asyncio
async def test_the_async_half_reports_exhaustion(instant) -> None:
    handler = _Handler(failures=99, error=_Status(429))

    with pytest.raises(ProviderUnavailable) as excinfo:
        await ModelRetryMiddleware("planner").awrap_model_call({}, handler.acall)

    assert "planner" in str(excinfo.value)


# --- registration, which is what makes any of the above reach a run ---------


def _names(middleware: list) -> list[str]:
    return [type(m).__name__ for m in middleware]


def test_the_planner_stack_carries_it() -> None:
    """The planner is where all three of 2026-08-27's dead runs died."""
    from rudra.agent.planner_agent import build_planner_middleware

    assert "ModelRetryMiddleware" in _names(build_planner_middleware("t", usage=object()))


def test_the_planner_stack_retries_outside_the_usage_accounting() -> None:
    """Each ATTEMPT is one recorded call, and the backoff sleep is nobody's.

    Inside the accounting, a call retried twice would read as one 40-second
    call whose duration is mostly `asyncio.sleep` -- which is the number
    OPEN-40 exists to make trustworthy.
    """
    from rudra.agent.planner_agent import build_planner_middleware

    names = _names(build_planner_middleware("t", usage=object()))
    assert names.index("ModelRetryMiddleware") < names.index("UsageMiddleware")


# --- OPEN-45: a retry that fires must say so ---------------------------------


class _Sink:
    """A TraceSink stand-in that keeps what it was told.

    Duck-typed on `notice` alone: that is the whole of the interface the
    middleware may use, and a fake carrying more would let this file pass
    against a middleware reaching for something else.
    """

    def __init__(self) -> None:
        self.notices: list[dict] = []

    def notice(self, payload, *, role, name="", namespace=(), index=0, at=0.0):
        self.notices.append(
            {
                "payload": payload,
                "role": role,
                "name": name,
                "namespace": namespace,
                "index": index,
                "at": at,
            }
        )
        return None


def test_a_fired_retry_emits_a_notice(instant) -> None:
    """The defect in one line: before this, a run could not tell a slow
    provider from one failing a third of its requests."""
    sink = _Sink()
    handler = _Handler(failures=1, error=_Status(500))

    ModelRetryMiddleware("coder", trace=sink).wrap_model_call({}, handler)

    assert len(sink.notices) == 1
    notice = sink.notices[0]
    assert notice["role"] == "coder"
    assert notice["name"] == "retry"
    assert "500" in notice["payload"]
    # The attempt number and the budget, so a reader knows how close the
    # run came to dying rather than only that something wobbled.
    assert "1" in notice["payload"]


def test_a_clean_call_emits_nothing(instant) -> None:
    """The regression that matters most: a healthy run's trace must be
    exactly what it was before OPEN-45."""
    sink = _Sink()
    handler = _Handler(failures=0, error=_Status(500))

    ModelRetryMiddleware("coder", trace=sink).wrap_model_call({}, handler)

    assert sink.notices == []


def test_a_non_transient_failure_emits_nothing(instant) -> None:
    """It is not retried, so there is no retry to report."""
    sink = _Sink()
    handler = _Handler(failures=1, error=_Status(401))

    with pytest.raises(_Status):
        ModelRetryMiddleware("coder", trace=sink).wrap_model_call({}, handler)

    assert sink.notices == []


def test_exhaustion_does_not_double_report(instant) -> None:
    """One notice per retry ACTUALLY MADE, and no give-up notice.

    Giving up already raises ProviderUnavailable, which RudraAgent renders
    as a sentence -- a second channel for one fact is how two descriptions
    of one event drift.
    """
    sink = _Sink()
    handler = _Handler(failures=99, error=_Status(503))

    with pytest.raises(ProviderUnavailable):
        ModelRetryMiddleware("coder", trace=sink).wrap_model_call({}, handler)

    # Four attempts, three of which were followed by a retry.
    assert handler.calls == 4
    assert len(sink.notices) == 3


def test_the_payload_never_carries_the_provider_body(instant) -> None:
    """A provider error body can echo the request back, and render.py
    escapes but does not redact. Status and class name only."""
    sink = _Sink()

    class _Leaky(Exception):
        def __init__(self) -> None:
            super().__init__("upstream said: api_key=sk-secret-value")
            self.status_code = 500

    handler = _Handler(failures=1, error=_Leaky())
    ModelRetryMiddleware("coder", trace=sink).wrap_model_call({}, handler)

    assert "sk-secret-value" not in sink.notices[0]["payload"]
    assert "_Leaky" in sink.notices[0]["payload"]


def test_a_fired_retry_increments_the_usage_counter(instant) -> None:
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    handler = _Handler(failures=2, error=_Status(500))

    ModelRetryMiddleware("coder", usage=usage).wrap_model_call({}, handler)

    assert usage.as_dict()["coder"]["retries"] == 2


def test_the_middleware_still_constructs_with_neither() -> None:
    """Every OPEN-41 test builds it this way, and both are optional for the
    reason SubagentContext.trace is: the machinery must stay constructible
    without a run."""
    middleware = ModelRetryMiddleware("coder")
    assert middleware.trace is None
    assert middleware.usage is None


@pytest.mark.asyncio
async def test_the_async_half_reports_too(instant) -> None:
    """The live path -- Rudra invokes every agent through astream."""
    from rudra.context.usage import RunUsage

    sink = _Sink()
    usage = RunUsage()
    handler = _Handler(failures=2, error=_Status(500))

    await ModelRetryMiddleware("planner", trace=sink, usage=usage).awrap_model_call(
        {}, handler.acall
    )

    assert len(sink.notices) == 2
    assert usage.as_dict()["planner"]["retries"] == 2


def test_a_broken_sink_does_not_end_the_run(instant) -> None:
    """Observability never fails a run -- TraceSink.emit swallows for the
    same reason (loop/engine.py:501-515)."""

    class _Broken:
        def notice(self, *args, **kwargs):
            raise RuntimeError("sink is on fire")

    handler = _Handler(failures=1, error=_Status(500))

    result = ModelRetryMiddleware("coder", trace=_Broken()).wrap_model_call({}, handler)
    assert result == "response"


def test_the_planner_stack_wires_the_trace_and_usage_through() -> None:
    """Registered is not enough: OPEN-45 is a middleware that was correctly
    registered and held nothing to report to."""
    from rudra.agent.planner_agent import build_planner_middleware

    sink = _Sink()
    usage = object()
    middleware = build_planner_middleware("t", usage=usage, trace=sink)
    retry = next(m for m in middleware if type(m).__name__ == "ModelRetryMiddleware")

    assert retry.trace is sink
    assert retry.usage is usage


# --- the whole path, with nothing faked, which is the RUN #2 row -----------


def test_a_retry_reaches_the_debug_log_and_usage_json(tmp_path, monkeypatch, instant) -> None:
    """A real TraceSink, a real debug recorder, a real usage.json.

    Every test above this line uses a fake sink, so all of them would stay
    green against a notice that never survived redaction, the level filter
    or JSON serialisation. This is the RUN #2 checklist row for OPEN-41
    made deterministic: after this, `grep '"kind": "notice"'` on
    `debug-<id>.jsonl` answers "did a retry fire" without inferring it
    across two files.
    """
    import json
    import logging

    from rudra.context.usage import RunUsage
    from rudra.loop.engine import write_usage_log
    from rudra.trace.debug import LOGGER_NAME, configure_debug_logging, debug_consumer
    from rudra.trace.sink import TraceSink

    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers), logger.level, logger.propagate
    debug_path = tmp_path / "debug-test.jsonl"
    handler = configure_debug_logging(debug_path, enabled=True)
    try:
        # NORMAL, not VERBOSE: a recorder bypasses the level filter, and
        # that is what makes the debug log the COMPLETE record (OPEN-7).
        # At VERBOSE this test would pass even if that broke.
        sink = TraceSink(level=TraceLevel.NORMAL)
        sink.add_recorder(debug_consumer())
        usage = RunUsage()

        handler_stub = _Handler(failures=2, error=_Status(503))
        ModelRetryMiddleware("coder", trace=sink, usage=usage).wrap_model_call({}, handler_stub)
        handler.flush()

        lines = [
            json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines() if line
        ]
        notices = [line for line in lines if line.get("kind") == "notice"]
        assert len(notices) == 2
        assert notices[0]["name"] == "retry"
        assert notices[0]["role"] == "coder"
        assert "503" in notices[0]["payload"]

        usage_path = tmp_path / "usage.json"
        write_usage_log(usage_path, usage)
        assert json.loads(usage_path.read_text(encoding="utf-8"))["coder"]["retries"] == 2
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
        logger.handlers, logger.level, logger.propagate = before


# --- through a real graph, which is the claim that matters ------------------


@pytest.mark.asyncio
async def test_a_500_on_the_second_model_call_no_longer_ends_the_run(instant) -> None:
    """Not a mock of the middleware's handler: the failure is raised by the
    MODEL, several model calls into ONE `astream`.

    That is exactly the case `_stream_with_retry` cannot reach -- by the
    second call it has already yielded -- and it is what killed runs
    `82fa4385bb22`, `326dd111f1eb` and `42aee1c1c7ad` on 2026-08-27.
    """
    from deepagents import create_deep_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from rudra.permissions.approval import run_with_approvals

    calls: list[int] = []

    class FlakyModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "flaky"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            calls.append(1)
            index = len(calls)
            if index == 2:
                raise _Status(500)
            message = (
                AIMessage(content="done")
                if index >= 3
                else AIMessage(
                    content="",
                    tool_calls=[{"name": "ping", "args": {}, "id": f"c{index}"}],
                )
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        def bind_tools(self, tools, **kwargs):
            return self

    def ping() -> str:
        """Ping."""
        return "pong"

    agent = create_deep_agent(
        model=FlakyModel(),
        tools=[ping],
        system_prompt="x",
        middleware=[ModelRetryMiddleware("coder")],
    )

    async for _ in run_with_approvals(
        agent, {"messages": [("user", "go")]}, {"recursion_limit": 8}, None, None
    ):
        pass

    # Three: the first tool-calling turn, the 500, and the retry that
    # produced the final answer. Without the middleware this run ends at
    # two with the provider's own exception on the terminal.
    assert len(calls) == 3
