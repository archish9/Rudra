"""The batching hint a Vaayu server advertises, read once at startup.

`GET /v1/models` carries `data[0].batching.recommended_client_concurrency`: how
many requests a client should keep in flight. An older or non-Vaayu endpoint
says nothing, and the answer is then 1 -- serial, which is what Rudra did
before the hint existed. A startup probe must never end a run, so every
failure (transport, status, shape, value) reads as 1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from rudra.config.schema import BUILTIN_ROLES
from rudra.llm.providers import PROVIDERS, effective_base_url

SERIAL = 1
"""The fallback: one request at a time."""

MAX_HINT_TIMEOUT = 5.0
"""Startup must not hang on a dead endpoint, whatever the role's own timeout."""


def _models_url(base_url: str) -> str:
    """`/models` under a base that already ends `/v1`, else `/v1/models`."""
    base = base_url.rstrip("/")
    return f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"


def read_client_concurrency(
    base_url: str,
    *,
    timeout: float,
    api_key: str | None = None,
    _transport: httpx.BaseTransport | None = None,
) -> int:
    """Return the server's recommended client concurrency, or 1. Never raises.

    `_transport` is a test seam (an `httpx.MockTransport`); None means a real one.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(transport=_transport, timeout=timeout) as client:
            response = client.get(_models_url(base_url), headers=headers)
            response.raise_for_status()
            value = int(response.json()["data"][0]["batching"]["recommended_client_concurrency"])
    except Exception:  # noqa: BLE001 -- a startup probe never ends a run
        return SERIAL
    return value if value >= 1 else SERIAL


def resolve_endpoint_concurrency(
    cfg: Any,
    *,
    fetch: Callable[..., int] = read_client_concurrency,
) -> dict[str, int]:
    """`{resolved base_url: concurrency}` over the endpoints the roles use.

    The cap is per endpoint, so the hint is read once per distinct resolved
    base_url (first role in BUILTIN_ROLES order wins its timeout and key). A
    provider that does not advertise the hint contributes 1 without a call.
    A role with no resolvable endpoint is skipped. Never raises: an
    unresolvable key is sent as None and `fetch` itself reads failure as 1.
    """
    from rudra.llm.factory import _resolve_api_key

    limits: dict[str, int] = {}
    for role in BUILTIN_ROLES:
        settings = cfg.model_for(role)
        base_url = effective_base_url(settings)
        if not base_url or base_url in limits:
            continue
        entry = PROVIDERS.get(settings.provider)
        if entry is None or not entry.reads_batching_hint:
            limits[base_url] = SERIAL
            continue
        try:
            api_key: str | None = _resolve_api_key(role, settings)
        except Exception:  # noqa: BLE001 -- a hint read never ends a run
            api_key = None
        timeout = min(settings.timeout or MAX_HINT_TIMEOUT, MAX_HINT_TIMEOUT)
        limits[base_url] = fetch(base_url, timeout=timeout, api_key=api_key)
    return limits
