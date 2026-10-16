"""EndpointLimiter: a limit of 1 means no gating, so callers get None."""

from __future__ import annotations

from rudra.llm.concurrency import EndpointLimiter


def test_limit_for_default_one():
    assert EndpointLimiter({}).limit_for("x") == 1
    assert EndpointLimiter({}).limit_for(None) == 1


def test_limit_for_known():
    assert EndpointLimiter({"b": 3}).limit_for("b") == 3


def test_semaphore_none_at_one():
    assert EndpointLimiter({"b": 1}).semaphore_for("b") is None


def test_semaphore_none_for_unknown():
    assert EndpointLimiter({}).semaphore_for("b") is None
    assert EndpointLimiter({"b": 3}).semaphore_for(None) is None


async def test_semaphore_value_and_cached():
    lim = EndpointLimiter({"b": 3})
    s = lim.semaphore_for("b")
    assert s is not None
    assert s._value == 3
    assert lim.semaphore_for("b") is s
