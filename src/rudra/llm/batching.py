"""The batching hint a Vaayu server advertises, read once at startup.

`GET /v1/models` carries `data[0].batching.recommended_client_concurrency`: how
many requests a client should keep in flight. An older or non-Vaayu endpoint
says nothing, and the answer is then 1 -- serial, which is what Rudra did
before the hint existed. A startup probe must never end a run, so every
failure (transport, status, shape, value) reads as 1.
"""

from __future__ import annotations

import httpx

SERIAL = 1
"""The fallback: one request at a time."""


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
