"""The startup read of Vaayu's `/v1/models` batching hint, and its serial fallback."""

from __future__ import annotations

import httpx
import pytest

from rudra.llm.batching import read_client_concurrency
from rudra.llm.providers import PROVIDERS

URL = "http://localhost:8000/v1"


def _models_payload(n):
    return {
        "data": [
            {
                "id": "m",
                "batching": {"recommended_client_concurrency": n, "helps": True, "reason": "dense"},
            }
        ]
    }


def ok(payload, status=200):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload))


def raising(exc):
    def handler(request):
        raise exc

    return httpx.MockTransport(handler)


def test_reads_three():
    assert read_client_concurrency(URL, timeout=2, _transport=ok(_models_payload(3))) == 3


def test_reads_one():
    assert read_client_concurrency(URL, timeout=2, _transport=ok(_models_payload(1))) == 1


def test_empty_data_falls_back():
    assert read_client_concurrency(URL, timeout=2, _transport=ok({"data": []})) == 1


def test_missing_batching_key():
    assert read_client_concurrency(URL, timeout=2, _transport=ok({"data": [{"id": "m"}]})) == 1


@pytest.mark.parametrize("n", [0, -2])
def test_zero_and_negative_clamp(n):
    assert read_client_concurrency(URL, timeout=2, _transport=ok(_models_payload(n))) == 1


@pytest.mark.parametrize(("raw", "want"), [(2.0, 2), ("3", 3)])
def test_float_and_string_coerce(raw, want):
    assert read_client_concurrency(URL, timeout=2, _transport=ok(_models_payload(raw))) == want


def test_non_200_falls_back():
    assert read_client_concurrency(URL, timeout=2, _transport=ok(_models_payload(3), 503)) == 1


def test_connection_error_falls_back():
    transport = raising(httpx.ConnectError("refused"))
    assert read_client_concurrency(URL, timeout=2, _transport=transport) == 1


def test_timeout_falls_back():
    transport = raising(httpx.TimeoutException("slow"))
    assert read_client_concurrency(URL, timeout=2, _transport=transport) == 1


@pytest.mark.parametrize(
    ("base", "want"),
    [
        ("http://h:8000/v1", "http://h:8000/v1/models"),
        ("http://h:8000/v1/", "http://h:8000/v1/models"),
        ("http://h:8000", "http://h:8000/v1/models"),
    ],
)
def test_url_shape(base, want):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=_models_payload(2))

    read_client_concurrency(base, timeout=2, _transport=httpx.MockTransport(handler))
    assert seen == [want]


def test_bearer_only_when_key_given():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=_models_payload(2))

    transport = httpx.MockTransport(handler)
    read_client_concurrency(URL, timeout=2, api_key="k", _transport=transport)
    read_client_concurrency(URL, timeout=2, _transport=transport)
    assert seen == ["Bearer k", None]


def test_provider_flags():
    flagged = {name for name, entry in PROVIDERS.items() if entry.reads_batching_hint}
    assert flagged == {"vaayu", "openai_compatible", "openai"}
