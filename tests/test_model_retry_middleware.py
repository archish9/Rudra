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
