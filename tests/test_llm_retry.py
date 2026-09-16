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


# --- OPEN-61: a 404 with evidence behind it ---------------------------------


@pytest.mark.parametrize("code", [400, 401, 403, 422])
def test_served_does_not_widen_the_other_client_errors(code: int) -> None:
    """`served` buys exactly one status back, not the 4xx family.

    A 401 after a hundred good calls is a key that just expired or a quota
    that just ran out, and retrying it four times is the behaviour A1.39's
    message exists to avoid.
    """
    assert is_transient(_Status(code), served=True) is False


def test_a_404_is_transient_once_the_model_has_answered() -> None:
    """OPEN-61, and the measurement is in `llm/retry.py`'s comment.

    Four probes against integrate.api.nvidia.com, same key, same body:
    404, then 200 for the SAME model id. RUN #7's first attempt died on one
    of those after 12 served calls.
    """
    assert is_transient(_Status(404), served=True) is True


def test_a_404_before_anything_was_served_is_still_a_missing_model() -> None:
    """The default is unchanged, which is the point of the parameter.

    A typo in a model name cannot reach the served state, so it keeps the
    old verdict: no retries, and the vendor's own message.
    """
    assert is_transient(_Status(404)) is False


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


# --- OPEN-83: the count means "tries spent", and mid-stream never spends any --


@pytest.mark.asyncio
async def test_a_mid_stream_failure_does_not_report_an_attempt_count(monkeypatch) -> None:
    """OPEN-83 §5(a). `after 1 attempt(s)` reads as "it barely tried".

    The opposite is true: four tries were budgeted and zero were spent,
    because `approval.py` retries only while nothing has been yielded. The
    count is real -- it means tries SPENT -- but printing it next to a
    failure that was never eligible for a retry tells the user Rudra gave
    up early, and run `689f0ea263be` ended on exactly that sentence with
    26 tasks queued.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(500), after_chunks=1)

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    text = str(excinfo.value)
    assert "attempt(s)" not in text
    assert "1 attempt" not in text


@pytest.mark.asyncio
async def test_a_mid_stream_failure_says_it_was_not_retried_and_why(monkeypatch) -> None:
    """Saying nothing about the retry is how the old count got read as one.

    The user needs both halves: it was not retried, and the reason is that
    the run was already in flight -- which is also why `--continue` is the
    answer rather than re-running from the top.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(500), after_chunks=1)

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    text = str(excinfo.value)
    assert "does not retry" in text
    assert "streaming" in text
    assert "on disk" in text


@pytest.mark.asyncio
async def test_a_pre_stream_failure_still_reports_every_attempt(monkeypatch) -> None:
    """The regression guard for the pair (plan §6.2).

    Both paths share `ProviderUnavailable`, so the wording change above can
    only be trusted while this one still names the tries it really spent.
    Four delays configured, four tries, `after 4 attempt(s)`.
    """
    monkeypatch.setattr("rudra.llm.retry.retry_delays", lambda **_: [0.0, 0.0, 0.0])
    agent = _FlakyAgent(failures=99, error=_Status(502))

    with pytest.raises(ProviderUnavailable) as excinfo:
        await _drain(agent)

    text = str(excinfo.value)
    assert "after 4 attempt(s)" in text
    assert agent.calls == 4
    assert "Nothing was written" in text


def test_the_attempt_count_survives_on_the_exception_either_way() -> None:
    """The wording drops the count; the attribute must not.

    `attempts` is what a caller inspects, and OPEN-83 changed only what a
    human reads.
    """
    err = ProviderUnavailable("the model provider", 1, _Status(500), progress="Files exist.")

    assert err.attempts == 1
    assert err.progress == "Files exist."


# --- OPEN-115: the policy the client SDKs used to apply ---------------------
#
# Rudra now turns every client SDK's own retries off, so whatever that layer
# retried and `is_transient` did not would silently become fatal. Measured
# against the installed SDKs: openai and anthropic retry EVERY status >= 500
# (`openai/_base_client.py:862`, `anthropic/_base_client.py:870`), obey
# `x-should-retry` (`:832`, `:844`), and wait out `Retry-After`. Anthropic's
# overload is 529, which this set did not contain.


class _Response:
    """The duck-typed shape of `error.response` -- httpx's, in practice."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _with_headers(error: BaseException, **headers: str) -> BaseException:
    error.response = _Response({name.replace("_", "-"): value for name, value in headers.items()})  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize("code", [501, 505, 507, 520, 524, 529, 599])
def test_every_server_error_is_retryable(code: int) -> None:
    """Parity with the SDK layer this replaces, and the owner's call.

    529 is the one that matters most: it is how Anthropic says overloaded
    (`anthropic/_exceptions.py:145`), and `status_of` finding it meant the
    `overloaded` name hint was never read -- 0 Rudra retries, measured.
    """
    assert is_transient(_Status(code)) is True


def test_anthropics_overload_is_retryable_by_its_real_shape() -> None:
    class OverloadedError(Exception):
        status_code = 529

    assert is_transient(OverloadedError("Overloaded")) is True


def test_a_status_past_the_http_range_is_not_a_server_error() -> None:
    assert is_transient(_Status(600)) is False


def test_the_server_can_ask_for_a_retry_the_status_would_refuse() -> None:
    """`x-should-retry: true` wins over the status, as both SDKs obey it."""
    assert is_transient(_with_headers(_Status(400), x_should_retry="true")) is True


def test_the_server_can_refuse_a_retry_the_status_would_allow() -> None:
    assert is_transient(_with_headers(_Status(500), x_should_retry="false")) is False


def test_a_refusal_from_the_server_outranks_served_evidence() -> None:
    """OPEN-61 widens a 404 on evidence; the endpoint's own word is better."""
    error = _with_headers(_Status(404), x_should_retry="false")

    assert is_transient(error, served=True) is False


def test_an_unrecognised_directive_leaves_the_status_to_decide() -> None:
    assert is_transient(_with_headers(_Status(503), x_should_retry="maybe")) is True
    assert is_transient(_with_headers(_Status(401), x_should_retry="maybe")) is False


def test_retry_after_in_seconds_is_read() -> None:
    from rudra.llm.retry import retry_after_of

    assert retry_after_of(_with_headers(_Status(429), retry_after="2")) == 2.0


def test_retry_after_in_milliseconds_is_preferred() -> None:
    """openai reads the non-standard `retry-after-ms` first; so does Rudra."""
    from rudra.llm.retry import retry_after_of

    error = _with_headers(_Status(429), retry_after_ms="1500", retry_after="9")

    assert retry_after_of(error) == 1.5


def test_retry_after_as_an_http_date_is_read() -> None:
    import email.utils
    import time

    from rudra.llm.retry import retry_after_of

    stamp = email.utils.formatdate(time.time() + 30, usegmt=True)
    seconds = retry_after_of(_with_headers(_Status(429), retry_after=stamp))

    assert seconds is not None
    assert 25 <= seconds <= 31


@pytest.mark.parametrize("value", ["", "soon", "0", "-3", "nan", "inf"])
def test_a_retry_after_that_asks_for_no_real_wait_is_ignored(value: str) -> None:
    from rudra.llm.retry import retry_after_of

    assert retry_after_of(_with_headers(_Status(429), retry_after=value)) is None


def test_an_error_with_no_response_has_no_retry_after() -> None:
    from rudra.llm.retry import retry_after_of

    class APITimeoutError(Exception):
        pass

    assert retry_after_of(APITimeoutError("Request timed out.")) is None
    assert retry_after_of(_Status(429)) is None


def test_the_wait_is_the_backoff_when_the_server_names_none() -> None:
    from rudra.llm.retry import retry_wait

    assert retry_wait(_Status(500), 1.5) == 1.5


def test_the_wait_honours_a_longer_retry_after() -> None:
    """Without this, OPEN-115 turns a polite 429 into four fast failures."""
    from rudra.llm.retry import retry_wait

    assert retry_wait(_with_headers(_Status(429), retry_after="7"), 0.5) == 7.0


def test_the_wait_never_shortens_the_backoff() -> None:
    from rudra.llm.retry import retry_wait

    assert retry_wait(_with_headers(_Status(429), retry_after="0.1"), 2.0) == 2.0


def test_the_wait_is_capped() -> None:
    """A daily-quota 429 can say an hour; a run must not sleep that long."""
    from rudra.llm.retry import RETRY_AFTER_CAP_SECONDS, retry_wait

    error = _with_headers(_Status(429), retry_after="86400")

    assert retry_wait(error, 1.0) == RETRY_AFTER_CAP_SECONDS
