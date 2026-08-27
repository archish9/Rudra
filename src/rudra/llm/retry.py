"""Surviving a provider hiccup, and saying so in a sentence (A1.39).

Measured on 2026-08-17: four distinct failure modes in one day -- `429`
quota, `502` upstream, `500` internal, `APIConnectionError` -- across two
providers. Each ended the run, wrote nothing, and reached the user as a
full Rich traceback through the provider's own client internals. Two of
them voided a Step 11c measurement cell outright.

Handled here rather than per provider because the outcome was identical
every time: the failure classes differ, the consequence does not.

This module names no provider package. `is_transient` matches on status
codes and exception *class names*, which is what lets it recognise
`APIConnectionError` from openai and httpx's timeouts alike without an
import that `tests/test_no_direct_provider_imports.py` would reject.
"""

from __future__ import annotations

import random

# Worth another attempt: the request never landed, or the far end was
# briefly unable to serve it.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Class names, because the classes live in provider packages this module
# must not import. Substring match: `openai.APIConnectionError` and
# `httpx.ConnectError` both carry the telling word.
_TRANSIENT_NAME_HINTS = (
    "connection",
    "timeout",
    "ratelimit",
    "serviceunavailable",
    "internalserver",
    "apistatus",
    "overloaded",
)


def status_of(error: BaseException) -> int | None:
    """The HTTP status an exception carries, however it carries it.

    Public since OPEN-45, which needs it to name a failure in a trace
    notice WITHOUT using `str(error)` -- a provider error body can echo
    the request back, and trace/render.py escapes but does not redact.
    Public rather than imported under its old underscore, because this
    module is the one place that knows how a provider spells a status
    and a second copy of that knowledge is what `is_transient` exists to
    prevent.
    """
    for attribute in ("status_code", "status", "code", "http_status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value

    # Some providers raise a plain ValueError whose payload is the decoded
    # error body -- this is the exact shape that killed Step 11b's
    # acceptance run 4: ValueError({'message': ..., 'code': 502}).
    for argument in getattr(error, "args", ()):
        if isinstance(argument, dict):
            candidate = argument.get("code") or argument.get("status")
            if isinstance(candidate, int):
                return candidate
        elif isinstance(argument, str):
            for status in _TRANSIENT_STATUS:
                if str(status) in argument:
                    return status
    return None


def is_transient(error: BaseException) -> bool:
    """Would trying this again plausibly work?

    A bad key, a missing model or a malformed request will fail identically
    on every attempt, so retrying them only delays the real message.
    """
    status = status_of(error)
    if status is not None:
        return status in _TRANSIENT_STATUS

    name = type(error).__name__.lower()
    return any(hint in name for hint in _TRANSIENT_NAME_HINTS)


def retry_delays(attempts: int = 3, *, base: float = 1.0) -> list[float]:
    """Exponential backoff with jitter, one entry per wait.

    Jittered so that several agents failing together -- a parent and its
    subagents share one endpoint -- do not retry in lockstep and reproduce
    the overload they are backing off from.
    """
    return [base * (2**index) * random.uniform(0.5, 1.0) for index in range(attempts)]


# What the default message says about the run's output. True of the case
# this class was built for -- a failure at or before the first chunk, which
# is where every 2026-08-17 failure struck -- and false of the mid-stream
# case OPEN-41 added, where the run is further along and files may exist.
_WROTE_NOTHING = "Nothing was written."


class ProviderUnavailable(RuntimeError):
    """A transient provider failure that outlived its retries.

    Carries what a user needs and nothing they do not: which provider,
    how many attempts, and what the far end actually said. The original
    exception stays available as `__cause__` for anyone debugging Rudra
    itself, but it does not reach the terminal.

    `progress` replaces the "Nothing was written." clause. It exists
    because OPEN-41 made this class reachable from a SECOND place -- a
    failure after the stream has already yielded -- where that sentence is
    simply false: the run had begun, and on a later task there are files on
    disk. A parameter rather than a sibling class, so the two paths cannot
    drift apart in wording, in status formatting, or in what they attach.
    """

    def __init__(
        self,
        provider: str,
        attempts: int,
        error: BaseException,
        *,
        progress: str | None = None,
    ) -> None:
        status = status_of(error)
        detail = f"{type(error).__name__}"
        if status is not None:
            detail += f" ({status})"
        super().__init__(
            f"Provider error from {provider} after {attempts} attempt(s): {detail}. "
            f"The model endpoint could not serve the request. {progress or _WROTE_NOTHING} "
            f"If this persists, check your quota and the endpoint's status."
        )
        self.provider = provider
        self.attempts = attempts
        self.error = error
        self.progress = progress


__all__ = ["ProviderUnavailable", "is_transient", "retry_delays", "status_of"]
