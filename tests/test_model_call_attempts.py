"""OPEN-115: one logical model call, and how many HTTP requests it really makes.

Two retry layers used to nest. The client SDK retried inside every attempt
`ModelRetryMiddleware` made, so one call against a failing endpoint was 12
requests on openai, openai_compatible and anthropic and 24 on google --
measured with exactly this server -- while `usage.json` counted the
middleware's 4. Run `a04f89bd2ed6` shows the shape live: one coder call, seven
`HTTP/1.1 500` lines, `retries: 2`.

These tests send real requests to a local server through the real
`build_model` and the real middleware. Nothing is mocked but the sleeps.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rudra.config.loader import build_config
from rudra.context.usage import RunUsage
from rudra.llm import build_model
from rudra.llm.retry import ProviderUnavailable
from rudra.middleware.model_retry import ModelRetryMiddleware


class _FailingEndpoint:
    """Answers every POST with `status`, and counts them."""

    def __init__(self) -> None:
        self.status = 500
        self.posts = 0
        endpoint = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length") or 0))
                endpoint.posts += 1
                body = json.dumps(
                    {"error": {"message": "boom", "code": endpoint.status, "status": "INTERNAL"}}
                ).encode()
                self.send_response(endpoint.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]


@pytest.fixture
def endpoint() -> Iterator[_FailingEndpoint]:
    served = _FailingEndpoint()
    thread = threading.Thread(target=served.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.server.shutdown()
        served.server.server_close()


@pytest.fixture
def no_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every backoff, Rudra's and any SDK's, returns at once."""

    async def _instant(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(asyncio, "sleep", _instant)


_MODELS = {
    "openai_compatible": "m",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
    "google": "gemini-2.0-flash",
}


def _model_for(provider: str, port: int, root: Path):
    """A role configured the way a user configures one: `.rudra/config.toml`."""
    base_url = f"http://127.0.0.1:{port}" + ("/v1" if provider.startswith("openai") else "")
    (root / ".rudra").mkdir()
    (root / ".rudra" / "config.toml").write_text(
        "[model.default]\n"
        f'provider = "{provider}"\n'
        f'model = "{_MODELS[provider]}"\n'
        f'base_url = "{base_url}"\n'
        'api_key = "not-a-real-key"\n'
        "timeout = 5\n",
        encoding="utf-8",
    )
    return build_model("default", build_config(project_root=root))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", sorted(_MODELS))
async def test_one_failing_call_is_four_requests_and_all_four_are_counted(
    provider: str, endpoint: _FailingEndpoint, no_sleeps: None, tmp_path: Path
) -> None:
    """The regression pin: 12 (24 on google) before OPEN-115, 4 after.

    4 is the middleware's budget -- one attempt and three retries -- so every
    request the endpoint saw is one `usage.json` can account for:
    `retries + exhaustions == posts`.
    """
    model = _model_for(provider, endpoint.port, tmp_path)
    usage = RunUsage()

    with pytest.raises(ProviderUnavailable):
        await ModelRetryMiddleware("default", usage=usage).awrap_model_call("hi", model.ainvoke)

    tally = usage.per_role["default"]
    assert endpoint.posts == 4
    assert tally.retries == 3
    assert tally.exhaustions == 1


@pytest.mark.asyncio
async def test_an_anthropic_overload_is_still_retried_once_the_sdk_is_not(
    endpoint: _FailingEndpoint, no_sleeps: None, tmp_path: Path
) -> None:
    """529 is Anthropic's overload. Only its SDK retried it before, 3 times,
    and Rudra refused it outright -- switching the SDK off without widening
    `is_transient` would have made an overloaded API fail on first contact.
    """
    endpoint.status = 529
    model = _model_for("anthropic", endpoint.port, tmp_path)
    usage = RunUsage()

    with pytest.raises(ProviderUnavailable):
        await ModelRetryMiddleware("default", usage=usage).awrap_model_call("hi", model.ainvoke)

    assert endpoint.posts == 4
    assert usage.per_role["default"].retries == 3


# --- the third layer, which must stay out of it -----------------------------


@pytest.mark.asyncio
# Both, because the ordering this pins held for `values` and NOT for a list
# stream_mode until OPEN-124: `_tagged` dropped every `(namespace, mode, data)`
# chunk, nothing was yielded, and the graph was re-run -- 16 model calls.
@pytest.mark.parametrize("stream_tokens", [False, True])
async def test_the_stream_retry_never_re_runs_a_graph_whose_model_call_failed(
    no_sleeps: None, stream_tokens: bool
) -> None:
    """`permissions/approval.py::_stream_with_retry` retries a whole graph, but
    only while nothing has been yielded -- and in `values` mode the input state
    is yielded BEFORE the first model call. So an exhausted model call reaches
    it with `yielded` already True, and is raised rather than re-run.

    That ordering is the ONLY thing holding it: `is_transient` reads `(500)`
    out of `ProviderUnavailable`'s own message and calls it transient. If
    langgraph ever yields nothing before the model node, this becomes 16
    model calls per failure, and 48 requests with a client SDK retrying.
    The plan left this "observed, not proven"; this makes it proven.
    """
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    from rudra.llm.retry import is_transient
    from rudra.permissions.approval import run_with_approvals

    class InternalServerError(Exception):
        status_code = 500

    calls: list[int] = []

    class AlwaysFailing(GenericFakeChatModel):
        async def _agenerate(self, *args: object, **kwargs: object):
            calls.append(1)
            raise InternalServerError("Error code: 500")

        async def _astream(self, *args: object, **kwargs: object):
            # Taken instead of `_agenerate` when tokens are streamed.
            calls.append(1)
            raise InternalServerError("Error code: 500")
            yield  # pragma: no cover -- makes this an async generator

        def bind_tools(self, tools: object, **kwargs: object):
            return self

    agent = create_agent(
        AlwaysFailing(messages=iter([])),
        tools=[],
        middleware=[ModelRetryMiddleware("planner")],
    )
    chunks = 0

    with pytest.raises(ProviderUnavailable) as excinfo:
        async for _chunk in run_with_approvals(
            agent,
            {"messages": [("user", "hi")]},
            {},
            None,
            None,
            stream_tokens=stream_tokens,
        ):
            chunks += 1

    assert chunks >= 1
    assert len(calls) == 4
    # The premise that makes the ordering load-bearing, stated so a change
    # to either half shows up here.
    assert is_transient(excinfo.value.__cause__ or excinfo.value) is True
