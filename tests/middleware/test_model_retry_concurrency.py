"""ModelRetryMiddleware gates in-flight model calls per endpoint (Task 4)."""

from __future__ import annotations

import asyncio

from rudra.llm.concurrency import EndpointLimiter
from rudra.middleware.model_retry import ModelRetryMiddleware

B = "http://vaayu.test/v1"


class _Probe:
    def __init__(self) -> None:
        self.live = 0
        self.peak = 0

    async def handler(self, request):
        self.live += 1
        self.peak = max(self.peak, self.live)
        await asyncio.sleep(0.01)
        self.live -= 1
        return request


async def _drive(mw: ModelRetryMiddleware, probe: _Probe, n: int = 6) -> None:
    await asyncio.gather(*(mw.awrap_model_call(i, probe.handler) for i in range(n)))


async def test_caps_at_two() -> None:
    probe = _Probe()
    mw = ModelRetryMiddleware("coder", base_url=B, limiter=EndpointLimiter({B: 2}))
    await _drive(mw, probe)
    assert probe.peak == 2


async def test_serial_at_one() -> None:
    probe = _Probe()
    mw = ModelRetryMiddleware("coder", base_url=B, limiter=EndpointLimiter({B: 1}))
    await _drive(mw, probe)
    assert probe.peak == 6  # cap 1 means NO gating: byte-identical to today


async def test_no_limiter_unchanged_and_retry_works(monkeypatch) -> None:
    probe = _Probe()
    await _drive(ModelRetryMiddleware("coder", base_url=B), probe)
    assert probe.peak == 6

    real_sleep = asyncio.sleep

    async def _fast(_):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast)

    class Boom(Exception):
        status_code = 503

    calls = {"n": 0}

    async def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Boom()
        return request

    assert await ModelRetryMiddleware("coder").awrap_model_call("x", flaky) == "x"
    assert calls["n"] == 2


async def test_gated_single_call_returns() -> None:
    mw = ModelRetryMiddleware("coder", base_url=B, limiter=EndpointLimiter({B: 2}))
    assert await mw.awrap_model_call("x", _Probe().handler) == "x"


def test_sync_path_ignores_limiter() -> None:
    mw = ModelRetryMiddleware("coder", base_url=B, limiter=EndpointLimiter({B: 2}))
    assert mw.wrap_model_call("x", lambda r: r) == "x"


def test_new_kwargs_optional() -> None:
    mw = ModelRetryMiddleware("coder")
    assert mw.base_url is None and mw.limiter is None
