"""OPEN-139: a streamed call is bounded by the role's `timeout`, like any other.

langchain_openai ends an async streamed call after `stream_chunk_timeout`
seconds without a parsed chunk -- 120 by default, the wait for the first chunk
included -- and that is not httpx's read timeout, which Rudra's `timeout` sets
and which an SSE keepalive resets. Run 654a00c7f546's coder hit it once, 5
chunks in. Under `--stream` it was a call's real bound, set by nothing in
Rudra: 120 s where the role said 300, and on a server that sends keepalives
the only bound a stalled stream had.

These tests send real requests to a local server through the real
`build_model`. The server holds its first chunk back behind SSE keepalives, so
httpx's read timeout never fires and only the chunk bound can.
`LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S` stands in for upstream's 120 s
default, which no test can wait out.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rudra.config.loader import build_config
from rudra.llm import build_model

_ENV = "LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S"
_HOLD = 1.5  # seconds of keepalives before the first chunk


class _SlowFirstChunk:
    """Streams one short completion after `_HOLD` seconds of keepalives."""

    def __init__(self) -> None:
        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("transfer-encoding", "chunked")
                self.end_headers()
                base = {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m"}
                choices = (
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "hi"},
                        "finish_reason": None,
                    },
                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                )
                try:
                    until = time.monotonic() + _HOLD
                    while time.monotonic() < until:
                        self._chunk(": keepalive\n\n")
                        time.sleep(0.2)
                    for choice in choices:
                        self._chunk(f"data: {json.dumps({**base, 'choices': [choice]})}\n\n")
                    self._chunk("data: [DONE]\n\n")
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client gave up first -- the case under test

            def _chunk(self, text: str) -> None:
                data = text.encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]


@pytest.fixture
def endpoint() -> Iterator[_SlowFirstChunk]:
    served = _SlowFirstChunk()
    thread = threading.Thread(target=served.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.server.shutdown()
        served.server.server_close()


def _model_for(port: int, root: Path, *, timeout: int):
    """A role configured the way a user configures one: `.rudra/config.toml`."""
    (root / ".rudra").mkdir()
    (root / ".rudra" / "config.toml").write_text(
        "[model.default]\n"
        'provider = "openai_compatible"\n'
        'model = "m"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\n'
        'api_key = "not-a-real-key"\n'
        f"timeout = {timeout}\n",
        encoding="utf-8",
    )
    return build_model("default", build_config(project_root=root))


async def _stream(model) -> str:
    return "".join([str(chunk.content) async for chunk in model.astream("hi")])


@pytest.mark.asyncio
async def test_a_first_chunk_within_the_roles_timeout_is_waited_for(
    endpoint: _SlowFirstChunk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression pin: a slow first token -- a local model's long prefill
    -- inside the role's timeout. Upstream's default (here 1 s) ended it."""
    monkeypatch.setenv(_ENV, "1")
    model = _model_for(endpoint.port, tmp_path, timeout=3)

    assert await _stream(model) == "hi"


@pytest.mark.asyncio
async def test_a_stall_past_the_roles_timeout_ends_the_call_despite_keepalives(
    endpoint: _SlowFirstChunk, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keepalives reset httpx's read timeout, so the chunk bound is the only
    thing that ends this call -- and it is the role's number, not 120 s."""
    monkeypatch.delenv(_ENV, raising=False)
    model = _model_for(endpoint.port, tmp_path, timeout=1)

    with pytest.raises(TimeoutError, match=r"No streaming chunk received for 1\.0s"):
        await _stream(model)
