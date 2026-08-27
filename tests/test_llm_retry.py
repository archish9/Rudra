"""A1.39: transient provider errors must not end a run as a stack dump.

Measured on 2026-08-17 across four distinct failure modes in one day --
429 quota, 502 upstream, 500 internal, APIConnectionError -- from two
providers. Every one killed the run, wrote nothing, and surfaced as a
full Rich traceback. Two of them voided a Step 11c measurement cell.
"""

from __future__ import annotations

import pytest

from rudra.llm.retry import ProviderUnavailable, is_transient, retry_delays


class _Status(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"status {code}")
        self.status_code = code


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_status_codes_are_retryable(code: int) -> None:
    assert is_transient(_Status(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(code: int) -> None:
    """Retrying a bad key or a missing model wastes time and hides the cause."""
    assert is_transient(_Status(code)) is False


def test_connection_errors_are_retryable_by_name() -> None:
    """Providers raise their own connection classes; match on the name.

    openai raises APIConnectionError, ollama raises httpx errors, and
    importing every provider here would violate the one-file rule
    tests/test_no_direct_provider_imports.py enforces.
    """

    class APIConnectionError(Exception):
        pass

    assert is_transient(APIConnectionError("Connection error.")) is True


def test_a_plain_error_is_not_retryable() -> None:
    assert is_transient(ValueError("something in Rudra broke")) is False


def test_the_502_shape_actually_measured_is_retryable() -> None:
    """The exact error that killed Step 11b's acceptance run 4."""
    err = ValueError({"message": "Upstream error from Nvidia: Internal server error", "code": 502})

    assert is_transient(err) is True


def test_delays_grow_and_are_jittered() -> None:
    delays = retry_delays(attempts=3)

    assert len(delays) == 3
    assert delays[0] < delays[1] < delays[2]
    assert delays[0] >= 0.5


def test_provider_unavailable_names_the_provider_and_the_cause() -> None:
    """The message a user sees instead of 40 lines of traceback."""
    err = ProviderUnavailable("openai_compatible", 3, _Status(429))

    text = str(err)
    assert "openai_compatible" in text
    assert "3" in text
    assert "429" in text
    assert "Nothing was written" in text


# --- the invocation boundary -------------------------------------------------


class _FlakyAgent:
    """Fails a given number of times before streaming normally."""

    def __init__(self, failures: int, error: BaseException, *, after_chunks: int = 0) -> None:
        self.failures = failures
        self.error = error
        self.after_chunks = after_chunks
        self.calls = 0

    async def astream(self, *args, **kwargs):
        self.calls += 1
        for index in range(self.after_chunks):
            yield {"chunk": index}
        if self.failures > 0:
            self.failures -= 1
            raise self.error
        yield {"ok": True}


async def _drain(agent):
    from rudra.permissions.approval import run_with_approvals

    return [chunk async for chunk in run_with_approvals(agent, {}, {}, None, None)]


@pytest.mark.asyncio
async def test_a_transient_failure_before_any_chunk_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=2, error=_Status(429))

    chunks = await _drain(agent)

    assert agent.calls == 3
    assert chunks == [{"ok": True}]


@pytest.mark.asyncio
async def test_a_non_transient_failure_is_not_retried() -> None:
    agent = _FlakyAgent(failures=1, error=_Status(401))

    with pytest.raises(Exception) as excinfo:
        await _drain(agent)

    assert agent.calls == 1
    assert "401" in str(excinfo.value) or excinfo.value.args


@pytest.mark.asyncio
async def test_exhausted_retries_raise_a_readable_error(monkeypatch) -> None:
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(502))

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    assert "Nothing was written" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_failure_after_streaming_began_is_not_retried(monkeypatch) -> None:
    """Retrying mid-stream would re-emit chunks the caller already parsed.

    The conservative half of the fix: once a chunk is out, the run is
    in flight and only resume (C7.2) can save it.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(429), after_chunks=1)

    with pytest.raises(Exception):
        await _drain(agent)

    assert agent.calls == 1


@pytest.mark.asyncio
async def test_a_failure_after_streaming_began_is_still_a_readable_error(monkeypatch) -> None:
    """OPEN-41. Not retried is not the same as not reported.

    `ProviderUnavailable` used to be reachable only on the not-yielded
    path, so the case that costs the user MORE work -- the run is further
    along -- was the one case that got no message. Run `82fa4385bb22` was
    the measurement: 850 terminal lines, 557 of them Rich traceback frames
    through site-packages/openai, and zero mentions of the provider being
    at fault.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(500), after_chunks=1)

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    assert agent.calls == 1
    assert "500" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_mid_stream_failure_does_not_claim_nothing_was_written(monkeypatch) -> None:
    """The default clause is a lie once the run has started.

    A coder that wrote four files before the provider failed leaves four
    files on disk, and telling the user otherwise sends them looking for
    work that is already there.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(500), after_chunks=1)

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    assert "Nothing was written" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_mid_stream_NON_transient_failure_still_raises_itself(monkeypatch) -> None:
    """A 401 is not a provider outage and must not be dressed as one."""
    agent = _FlakyAgent(failures=99, error=_Status(401), after_chunks=1)

    with pytest.raises(_Status):
        await _drain(agent)

    assert agent.calls == 1
