"""OPEN-40 — `rudra models test` times the two calls it already makes.

A run's duration is `calls x per-call latency`, and 91% of run6's wall
clock was the model. `probe_role` already invokes twice per endpoint and
timed neither, so a model answering in 400ms and one answering in 30s
produced an identical green row -- the command that exists to answer "is
this model usable?" answered only half of it.

The measurement is free: no probe call is added, the existing two are
wrapped. Every failure path reports `None` rather than a duration, because
a failed call's elapsed time is the error's and not the model's.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from rudra.config import build_config
from rudra.llm.probe import probe_role

BASE = (
    '[model.default]\nprovider = "ollama"\n'
    'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
)

SLEEP = 0.05
"""Long enough that a real clock separates it from zero, short enough that
five of these do not lengthen the suite noticeably."""


def config_for(tmp_path) -> Any:
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(BASE, encoding="utf-8")
    return build_config(tmp_path)


class StubResponse:
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        self.tool_calls = tool_calls


class StubBound:
    """What `bind_tools([echo])` returns."""

    def __init__(self, sleep: float, error: Exception | None) -> None:
        self._sleep = sleep
        self._error = error

    def invoke(self, _prompt: str) -> StubResponse:
        time.sleep(self._sleep)
        if self._error is not None:
            raise self._error
        return StubResponse([{"name": "echo", "args": {"text": "hello"}}])


class StubModel:
    profile = {"max_input_tokens": 32768}

    def __init__(
        self,
        *,
        reach_sleep: float = 0.0,
        tools_sleep: float = 0.0,
        reach_error: Exception | None = None,
        tools_error: Exception | None = None,
    ) -> None:
        self._reach_sleep = reach_sleep
        self._tools_sleep = tools_sleep
        self._reach_error = reach_error
        self._tools_error = tools_error

    def invoke(self, _prompt: str) -> StubResponse:
        time.sleep(self._reach_sleep)
        if self._reach_error is not None:
            raise self._reach_error
        return StubResponse([])

    def bind_tools(self, _tools: list[Any]) -> StubBound:
        return StubBound(self._tools_sleep, self._tools_error)


@pytest.fixture
def probe_with(monkeypatch, tmp_path):
    """Probe `planner` against a stub model, with no network anywhere."""

    def run(model: Any = None, construct_error: Exception | None = None):
        def build(_role: str, _config: Any = None) -> Any:
            if construct_error is not None:
                raise construct_error
            return model

        monkeypatch.setattr("rudra.llm.probe.build_model", build)
        return probe_role("planner", config_for(tmp_path))

    return run


def test_the_tool_call_is_timed(probe_with) -> None:
    """The tool call is the one the table shows: it is the shape an agent
    actually makes, and being the second call it is not paying connection
    setup."""
    result = probe_with(StubModel(tools_sleep=SLEEP))

    assert result.tools == "ok"
    assert result.tools_seconds is not None
    assert result.tools_seconds >= SLEEP


def test_the_reach_call_is_timed_separately(probe_with) -> None:
    """Two calls, two durations. Collapsing them would hide which half of a
    slow endpoint is slow."""
    result = probe_with(StubModel(reach_sleep=SLEEP))

    assert result.reach_seconds is not None
    assert result.reach_seconds >= SLEEP
    assert result.tools_seconds is not None
    assert result.tools_seconds < SLEEP


def test_a_construct_failure_reports_no_durations(probe_with) -> None:
    """Nothing was called, so there is nothing to time."""
    result = probe_with(construct_error=RuntimeError("no api key"))

    assert result.ok is False
    assert result.reach_seconds is None
    assert result.tools_seconds is None


def test_a_reach_failure_reports_no_durations(probe_with) -> None:
    """How long a connection took to refuse is the error's number, not the
    model's, and printing it beside the error would invite reading it as
    one."""
    result = probe_with(StubModel(reach_sleep=SLEEP, reach_error=RuntimeError("refused")))

    assert result.ok is False
    assert result.reach_seconds is None
    assert result.tools_seconds is None


def test_a_tools_failure_keeps_the_reach_duration(probe_with) -> None:
    """The reach call succeeded and its number is real. Only the failed
    half goes missing."""
    result = probe_with(StubModel(reach_sleep=SLEEP, tools_error=RuntimeError("no tool support")))

    assert result.ok is False
    assert result.reach_seconds is not None
    assert result.reach_seconds >= SLEEP
    assert result.tools_seconds is None


def test_no_tool_calls_emitted_is_a_real_round_trip_and_keeps_its_time(probe_with) -> None:
    """`no tool_calls emitted` is not an exception: the model answered, it
    just answered wrong. That round trip happened and its duration is the
    model's."""

    class Silent(StubModel):
        def bind_tools(self, _tools: list[Any]) -> Any:
            class Bound:
                @staticmethod
                def invoke(_prompt: str) -> StubResponse:
                    time.sleep(SLEEP)
                    return StubResponse([])

            return Bound()

    result = probe_with(Silent())

    assert result.tools == "no tool_calls emitted"
    assert result.tools_seconds is not None
    assert result.tools_seconds >= SLEEP


# --- the column ------------------------------------------------------------


def render_models_test(monkeypatch, tmp_path, result: Any) -> str:
    """Run `rudra models test` against a canned ProbeResult."""
    from typer.testing import CliRunner

    from rudra.cli import app
    from rudra.config import reset_config

    monkeypatch.chdir(tmp_path)
    reset_config()
    config_for(tmp_path)
    monkeypatch.setattr("rudra.llm.probe.probe_role", lambda *_args, **_kwargs: result)
    try:
        return CliRunner().invoke(app, ["models", "test"]).output
    finally:
        reset_config()


def probe_result(**overrides: Any) -> Any:
    from rudra.llm.probe import ProbeResult

    fields: dict[str, Any] = {
        "role": "planner",
        "provider": "ollama",
        "model": "qwen3:32b",
        "construct": "ok",
        "reach": "ok",
        "tools": "ok",
        "context_tokens": 32768,
        "ok": True,
    }
    fields.update(overrides)
    return ProbeResult(**fields)


def test_the_table_has_a_latency_column(monkeypatch, tmp_path) -> None:
    output = render_models_test(monkeypatch, tmp_path, probe_result(tools_seconds=3.14))

    assert "Latency" in output
    assert "3.1s" in output


def test_an_unmeasured_latency_renders_a_dash(monkeypatch, tmp_path) -> None:
    """A failed probe has no duration, and the row must still render --
    `models test` reporting a broken endpoint is the case it exists for.

    Asserted on the cell rather than on the error text beside it: eight
    columns at an 80-column width fold a phrase like `Connection refused`
    across lines, so a substring match would be testing Rich's wrapping.
    """
    import re

    output = render_models_test(
        monkeypatch,
        tmp_path,
        probe_result(reach="Connection refused", tools="skipped", ok=False),
    )

    assert "Latency" in output
    assert re.search(r"│\s*-\s*│", output), output


def test_the_table_says_what_one_round_trip_is(monkeypatch, tmp_path) -> None:
    """A bare number invites the wrong conclusion. Two probe calls is not a
    benchmark, and the line under the table must not imply it is."""
    output = render_models_test(monkeypatch, tmp_path, probe_result(tools_seconds=3.14))

    assert "round trip" in output


# --- OPEN-115: the probe's only retry layer is Rudra's now ------------------


class _Status(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"Error code: {code}")
        self.status_code = code


class _FlakyReach(StubModel):
    """Fails the reach call `failures` times, then answers."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.reach_calls = 0

    def invoke(self, prompt: str) -> StubResponse:
        self.reach_calls += 1
        if self.reach_calls <= self.failures:
            raise _Status(502)
        return super().invoke(prompt)


def test_a_transient_reach_failure_is_retried_rather_than_reported(probe_with, monkeypatch) -> None:
    """The client SDK used to absorb a flap here, and its retries are off.
    A red row for an endpoint that answers on the second try would send a
    user to debug a config that is fine."""
    monkeypatch.setattr("rudra.middleware.model_retry.time.sleep", lambda *_: None)
    model = _FlakyReach(failures=2)

    result = probe_with(model)

    assert result.reach == "ok"
    assert result.tools == "ok"
    assert model.reach_calls == 3


def test_a_transient_tools_failure_is_retried_too(probe_with, monkeypatch) -> None:
    monkeypatch.setattr("rudra.middleware.model_retry.time.sleep", lambda *_: None)

    class FlakyBound(StubBound):
        calls = 0

        def invoke(self, prompt: str) -> StubResponse:
            FlakyBound.calls += 1
            if FlakyBound.calls == 1:
                raise _Status(500)
            return super().invoke(prompt)

    class Model(StubModel):
        def bind_tools(self, _tools: list[Any]) -> StubBound:
            return FlakyBound(0.0, None)

    result = probe_with(Model())

    assert result.tools == "ok"
    assert FlakyBound.calls == 2


def test_an_exhausted_reach_is_reported_as_a_failure(probe_with, monkeypatch) -> None:
    monkeypatch.setattr("rudra.middleware.model_retry.time.sleep", lambda *_: None)

    result = probe_with(_FlakyReach(failures=99))

    assert result.ok is False
    assert result.reach_seconds is None
    assert "502" in result.reach
