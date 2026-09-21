"""OPEN-137: `--stream` must not switch off the token counts a run records.

Live run `e1a57a3e3791` was the first ever launched with `--stream`, and all
152 of its in-graph `model_call` records -- and `usage.json`, for every role --
read `input_tokens: null`, on a model and provider that reported tokens in
every earlier run. `stream_tokens` asks langgraph for `messages` mode, which
makes every model call STREAM; langchain_openai sends
`stream_options.include_usage` only when `stream_usage` is on, and it leaves
that off whenever a `base_url` is set -- which `openai_compatible` always is.
No usage chunk, no `usage_metadata`, and `UsageMiddleware` records None.

These tests send real requests to a local server through the real
`build_model`, the real stream funnel and the real `UsageMiddleware`. The
server behaves as OpenAI's and vLLM's do: it sends a usage chunk only when the
request asks for one, so a missing `stream_options` is a missing count here
exactly as it was live.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent

from rudra.config.loader import build_config
from rudra.context.middleware import UsageMiddleware
from rudra.context.usage import RunUsage
from rudra.llm import build_model
from rudra.permissions.approval import run_with_approvals

_INPUT, _OUTPUT = 18, 27


def _chat_events(body: dict[str, Any]) -> list[dict[str, Any]]:
    base = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m"}
    events: list[dict[str, Any]] = [
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "hi"}, "finish_reason": None}
            ],
        },
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if (body.get("stream_options") or {}).get("include_usage"):
        usage = {
            "prompt_tokens": _INPUT,
            "completion_tokens": _OUTPUT,
            "total_tokens": _INPUT + _OUTPUT,
        }
        events.append({**base, "choices": [], "usage": usage})
    return events


def _chat_completion() -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": _INPUT,
            "completion_tokens": _OUTPUT,
            "total_tokens": _INPUT + _OUTPUT,
        },
    }


def _responses_events() -> list[dict[str, Any]]:
    """OpenAI's Responses API, which reports usage on `response.completed`
    whether or not anything asked -- the `openai` provider's path."""
    message = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "hi", "annotations": []}],
    }
    response = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-4o",
        "status": "completed",
        "output": [message],
        "usage": {
            "input_tokens": _INPUT,
            "output_tokens": _OUTPUT,
            "total_tokens": _INPUT + _OUTPUT,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    return [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**response, "status": "in_progress", "output": [], "usage": None},
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {**message, "status": "in_progress", "content": []},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 2,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "hi",
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 3,
            "output_index": 0,
            "item": message,
        },
        {"type": "response.completed", "sequence_number": 4, "response": response},
    ]


class _Endpoint:
    """Serves chat completions and responses, streamed or not, and keeps
    every request body it was sent."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        endpoint = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
                endpoint.requests.append((self.path, body))
                if self.path.endswith("/responses") and body.get("stream"):
                    payload = "".join(f"data: {json.dumps(e)}\n\n" for e in _responses_events())
                    self._send("text/event-stream", payload)
                elif self.path.endswith("/responses"):
                    self._send("application/json", json.dumps(_responses_events()[-1]["response"]))
                elif body.get("stream"):
                    events = _chat_events(body)
                    payload = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
                    self._send("text/event-stream", payload + "data: [DONE]\n\n")
                else:
                    self._send("application/json", json.dumps(_chat_completion()))

            def _send(self, content_type: str, payload: str) -> None:
                data = payload.encode()
                self.send_response(200)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]


@pytest.fixture
def endpoint() -> Iterator[_Endpoint]:
    served = _Endpoint()
    thread = threading.Thread(target=served.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.server.shutdown()
        served.server.server_close()


_MODELS = {"openai_compatible": "m", "openai": "gpt-4o"}


def _model_for(provider: str, port: int, root: Path):
    """A role configured the way a user configures one: `.rudra/config.toml`."""
    (root / ".rudra").mkdir()
    (root / ".rudra" / "config.toml").write_text(
        "[model.default]\n"
        f'provider = "{provider}"\n'
        f'model = "{_MODELS[provider]}"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n'
        'api_key = "not-a-real-key"\n'
        "timeout = 5\n",
        encoding="utf-8",
    )
    return build_model("default", build_config(project_root=root))


async def _run(model: Any, *, stream_tokens: bool) -> RunUsage:
    """One model call, through the funnel every planner stage and subagent
    streams through, recorded by the middleware that writes `usage.json`."""
    usage = RunUsage()
    agent = create_agent(model, tools=[], middleware=[UsageMiddleware("coder", usage)])
    inputs = {"messages": [("user", "hi")]}
    async for _ in run_with_approvals(agent, inputs, {}, None, None, stream_tokens=stream_tokens):
        pass
    return usage


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", sorted(_MODELS))
@pytest.mark.parametrize("stream_tokens", [False, True], ids=["values", "stream"])
async def test_a_run_records_its_tokens_whether_or_not_it_streams(
    provider: str, stream_tokens: bool, endpoint: _Endpoint, tmp_path: Path
) -> None:
    """The regression pin. `openai_compatible` + `stream` read None/None
    before OPEN-137 -- run `e1a57a3e3791`'s whole `usage.json`."""
    usage = await _run(_model_for(provider, endpoint.port, tmp_path), stream_tokens=stream_tokens)

    tally = usage.per_role["coder"]
    assert (tally.input_tokens, tally.output_tokens) == (_INPUT, _OUTPUT)


@pytest.mark.asyncio
async def test_a_streamed_chat_completion_asks_for_its_usage(
    endpoint: _Endpoint, tmp_path: Path
) -> None:
    """What the fix changes on the wire, and the only thing."""
    await _run(_model_for("openai_compatible", endpoint.port, tmp_path), stream_tokens=True)

    [(path, body)] = endpoint.requests
    assert path.endswith("/chat/completions")
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_a_call_that_does_not_stream_sends_no_stream_options(
    endpoint: _Endpoint, tmp_path: Path
) -> None:
    """`stream_options` only exists on a streamed request, so a run without
    `--stream` -- every default run -- sends exactly what it sent before."""
    await _run(_model_for("openai_compatible", endpoint.port, tmp_path), stream_tokens=False)

    [(_path, body)] = endpoint.requests
    assert not body.get("stream")
    assert "stream_options" not in body


@pytest.mark.asyncio
async def test_the_responses_api_is_sent_no_chat_completions_option(
    endpoint: _Endpoint, tmp_path: Path
) -> None:
    """The `openai` provider streams through `/responses`, which reports usage
    unasked and has no `include_usage`; `stream_usage` must not leak into it."""
    await _run(_model_for("openai", endpoint.port, tmp_path), stream_tokens=True)

    [(path, body)] = endpoint.requests
    assert path.endswith("/responses")
    assert "include_usage" not in json.dumps(body)
