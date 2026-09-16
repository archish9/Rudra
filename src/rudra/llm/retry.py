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

import email.utils
import math
import random
import time

# Worth another attempt: the request never landed, or the far end was
# briefly unable to serve it. Every OTHER status from 500 to 599 is too --
# see `is_transient` -- and these server errors stay listed only because
# `status_of` scans an error's message for exactly this set.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Longest a server-directed wait may hold a retry (OPEN-115). openai's own
# `MAX_RETRY_AFTER_DELAY` is this value; anthropic's is 60. A daily-quota
# 429 can name an hour, and a run should give up on that rather than sleep.
RETRY_AFTER_CAP_SECONDS = 120.0

# 404 is deliberately NOT in the set above, and is retryable only with
# evidence -- see `is_transient`. Measured 2026-08-31 against
# `https://integrate.api.nvidia.com/v1`, four probes one after another with
# the same key and the same body: `404`, then `200` for the SAME model id,
# which `GET /v1/models` lists among 83. RUN #7's first attempt
# (`8c4e949eeccf`) died on one of those after 12 model calls had already
# been served on that spec, and reached the terminal as 850 lines of vendor
# frames -- OPEN-61. A first-call 404 is still a missing model, which is
# what the docstring below says and what this set does not change.
_SERVED_TRANSIENT_STATUS = frozenset({404})

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


def is_transient(error: BaseException, *, served: bool = False) -> bool:
    """Would trying this again plausibly work?

    A bad key, a missing model or a malformed request will fail identically
    on every attempt, so retrying them only delays the real message.

    `served` is the caller saying "this endpoint has already answered a
    call for this model in this run" (OPEN-61). It widens the answer by
    exactly one status: a 404 cannot mean "no such model" once the model
    has served a call, so it is a flap and worth another attempt -- while
    the first call of a run, where a typo in a model name is the likelier
    cause, is judged exactly as before. Evidence rather than a guess, which
    is the `CLAUDE.md` §1.8 rule applied to a provider instead of an OS:
    a caller that has no evidence passes nothing and gets the old answer.

    Since OPEN-115 this is the ONLY retry policy a model call has: every
    client SDK's own retries are switched off (`llm/providers.py`), so what
    that layer retried and this did not would have become fatal. Two rules
    were missing and are here for that parity. Every status from 500 to 599
    is transient, as openai and anthropic both treat it -- anthropic's
    overload is 529. And the endpoint's own `x-should-retry` header decides
    before the status does, `served` included: it is the one party that
    knows.
    """
    directive = _header(error, "x-should-retry")
    if directive == "true":
        return True
    if directive == "false":
        return False

    status = status_of(error)
    if status is not None:
        if served and status in _SERVED_TRANSIENT_STATUS:
            return True
        return status in _TRANSIENT_STATUS or 500 <= status < 600

    name = type(error).__name__.lower()
    return any(hint in name for hint in _TRANSIENT_NAME_HINTS)


def _header(error: BaseException, name: str) -> str | None:
    """One response header off a provider error, or None.

    Duck-typed for the reason `status_of` is: the error classes live in
    provider packages this module must not import. openai's and anthropic's
    status errors carry the httpx response as `.response`, whose headers
    are case-insensitive; a transport error carries none.
    """
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:  # noqa: BLE001 -- an odd headers object is no header
        return None
    return value if isinstance(value, str) else None


def retry_after_of(error: BaseException) -> float | None:
    """Seconds the endpoint asked us to wait before retrying, or None.

    Read the way openai's client reads it (`_parse_retry_after_header`):
    the non-standard `retry-after-ms` first, then `retry-after` as seconds,
    then as an HTTP date. A value that asks for no real wait -- zero,
    negative, not finite, unparseable -- is None, so the caller's own
    backoff applies.
    """
    seconds: float | None = None
    milliseconds = _header(error, "retry-after-ms")
    if milliseconds is not None:
        try:
            seconds = float(milliseconds) / 1000
        except ValueError:
            seconds = None
    if seconds is None:
        value = _header(error, "retry-after")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                stamp = email.utils.parsedate_to_datetime(value)
            except (TypeError, ValueError, IndexError):
                return None
            seconds = stamp.timestamp() - time.time()
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


def retry_wait(error: BaseException, backoff: float) -> float:
    """How long to sleep before re-issuing a call that raised `error`.

    The backoff, unless the endpoint asked for longer -- then what it
    asked, up to `RETRY_AFTER_CAP_SECONDS`. Never shorter than the backoff:
    a server saying "0.1 s" does not make several agents failing together
    retry in lockstep.

    This is what makes switching the SDK retries off safe for a 429
    (OPEN-115): the SDKs waited out `Retry-After`, and the jittered 1/2/4 s
    of `retry_delays` alone would spend the whole budget inside the window
    the endpoint named and end in an exhaustion.
    """
    asked = retry_after_of(error)
    if asked is None:
        return backoff
    return max(backoff, min(asked, RETRY_AFTER_CAP_SECONDS))


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

# Why the mid-stream case shows no attempt count (OPEN-83). It is not that
# the count is wrong -- it means tries SPENT, and mid-stream spends none --
# it is that a number reading "1" beside a dead provider says "Rudra gave
# up early" to everyone who has not read approval.py. Run `689f0ea263be`
# ended on that sentence with 26 tasks queued and the user's question was
# "whats that?", so the sentence now answers it.
_NOT_RETRIED = (
    "the failure struck after the run had begun streaming -- which Rudra "
    "does not retry, since replaying a partly-consumed run would repeat "
    "work already done"
)


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
        # The count is printed only where it is a fact about effort. On the
        # mid-stream path `attempts` is always 1 -- the loop breaks out on
        # its first pass once a chunk is out -- so the two facts, "what the
        # provider did" and "how hard Rudra tried", must not share one
        # number (OPEN-83 §5(a)).
        if progress is None:
            opening = f"Provider error from {provider} after {attempts} attempt(s): {detail}."
            served = f"The model endpoint could not serve the request. {_WROTE_NOTHING}"
        else:
            opening = f"Provider error from {provider}: {detail}."
            served = (
                f"The model endpoint could not serve the request, and {_NOT_RETRIED}. {progress}"
            )
        super().__init__(
            f"{opening} {served} If this persists, check your quota and the endpoint's status."
        )
        self.provider = provider
        self.attempts = attempts
        self.error = error
        self.progress = progress


__all__ = [
    "RETRY_AFTER_CAP_SECONDS",
    "ProviderUnavailable",
    "is_transient",
    "retry_after_of",
    "retry_delays",
    "retry_wait",
    "status_of",
]
