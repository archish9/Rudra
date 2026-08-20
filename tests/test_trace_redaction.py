"""Secrets must not reach the console, the debug log, or 15c's transcript.

A1.95, found on a real credential: the planner read `.env` with
`read_file` -- a legitimate, deliberately ungated read -- and the trace
rendered the result verbatim, so `NVIDIA_API_KEY=nvapi-…` was printed and
written to `.rudra/run/logs/debug.jsonl`, which the troubleshooting docs
tell users to attach to a GitHub issue.

Redaction happens where the EVENT is built, not in the renderer: the debug
consumer writes `event.as_dict()` and never calls `render`, so a renderer
fix would have covered the screen and missed the file.

**What this does not do, stated plainly:** the model still sees the file
it read. This protects the artifacts a human keeps and shares, not the
model's context.
"""

from __future__ import annotations

import pytest

from rudra.trace.redact import REDACTED, redact


@pytest.mark.parametrize(
    "secret",
    [
        "NVIDIA_API_KEY=nvapi-7AoCcc2422Ea3KlWzaMAvPIuRcKAdfNS8Er",
        "OPENAI_API_KEY=sk-proj-abc123def456ghi789",
        "ANTHROPIC_API_KEY='sk-ant-api03-XYZ'",
        'GITHUB_TOKEN="ghp_16C7e42F292c6912E7710c838347Ae178B4a"',
        "DATABASE_PASSWORD=hunter2hunter2",
        "MY_SECRET = topsecretvalue",
        "aws_secret_access_key: wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    ],
)
def test_an_assignment_to_a_secret_shaped_name_is_redacted(secret):
    out = redact(secret)
    assert REDACTED in out
    assert "nvapi-7Ao" not in out
    assert "sk-proj-abc" not in out
    assert "hunter2" not in out
    assert "wJalrXUt" not in out


def test_the_variable_name_survives_so_the_line_still_means_something():
    """Redacting the whole line would make the trace useless for the thing
    it is for: seeing what the agent read."""
    out = redact("NVIDIA_API_KEY=nvapi-secretsecretsecret")
    assert "NVIDIA_API_KEY" in out


@pytest.mark.parametrize(
    "token",
    [
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "curl -H 'Authorization: Bearer sk-abcdefghijklmnop'",
    ],
)
def test_a_bearer_token_is_redacted(token):
    out = redact(token)
    assert REDACTED in out
    assert "eyJhbGciOi" not in out
    assert "sk-abcdefgh" not in out


@pytest.mark.parametrize(
    "blob",
    [
        "the key is sk-proj-1234567890abcdefghijklmn",
        "nvapi-abcdefghijklmnopqrstuvwxyz012345",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUv",
    ],
)
def test_a_bare_vendor_prefixed_key_is_redacted_even_without_an_assignment(blob):
    """A key pasted into prose, or echoed by a tool, has no `NAME=` around
    it. The prefix is the only signal there is."""
    assert REDACTED in redact(blob)


def test_ordinary_text_is_left_completely_alone():
    """A redactor that mangles normal output is one people turn off."""
    for text in (
        "Wrote parser.py",
        "def add(a, b):\n    return a + b",
        "Error: 1 failed, 3 passed",
        "API_VERSION=2024-01-01",
        "MAX_TOKENS=131072",
        "PATH=/usr/bin:/bin",
    ):
        assert redact(text) == text


def test_a_numeric_value_is_not_a_credential():
    """`MAX_TOKENS` and `context_tokens` match the name pattern -- TOKEN is
    a substring of TOKENS -- and are ordinary config a trace should show.
    Rudra's own schema has both."""
    assert redact("MAX_TOKENS=131072") == "MAX_TOKENS=131072"
    assert redact("context_tokens = 512288") == "context_tokens = 512288"
    assert redact("max_output_tokens: 131072") == "max_output_tokens: 131072"


def test_a_short_value_after_a_secret_name_is_still_redacted():
    """`KEY=x` is either a placeholder or a very bad key. Redact both --
    the cost of over-redacting a placeholder is nil."""
    assert REDACTED in redact("API_KEY=x")


def test_redaction_is_idempotent():
    once = redact("NVIDIA_API_KEY=nvapi-secretsecretsecret")
    assert redact(once) == once


def test_multiline_content_redacts_every_line():
    dotenv = (
        "# Local development config\n"
        "NVIDIA_API_KEY=nvapi-aaaaaaaaaaaaaaaaaaaaaaaa\n"
        "RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b\n"
        "OPENAI_API_KEY=sk-bbbbbbbbbbbbbbbbbbbbbbbb\n"
    )
    out = redact(dotenv)
    assert "nvapi-aaa" not in out
    assert "sk-bbb" not in out
    assert "nemotron-3-ultra-550b-a55b" in out, "non-secret config must survive"


def test_none_and_empty_are_safe():
    assert redact("") == ""


# --- the wiring ----------------------------------------------------------


def test_a_read_file_result_carrying_a_key_is_redacted_in_the_event():
    """The A1.95 reproduction, end to end through the stream consumer:
    this is exactly the shape that put a live key in debug.jsonl."""
    from langchain_core.messages import ToolMessage

    from rudra.trace.stream import StreamState, consume

    dotenv = "# Local development config\nNVIDIA_API_KEY=nvapi-aaaaaaaaaaaaaaaaaaaaaaaa\n"
    chunk = ((), {"messages": [ToolMessage(content=dotenv, tool_call_id="1", name="read_file")]})

    event = consume(chunk, StreamState(role="planner"))[0]

    assert "nvapi-aaa" not in event.payload
    assert REDACTED in event.payload


def test_the_debug_log_sees_the_redacted_payload_too():
    """render() is not the only consumer -- the debug consumer serialises
    the event dict directly, which is why redaction is not in render()."""
    from langchain_core.messages import ToolMessage

    from rudra.trace.stream import StreamState, consume

    chunk = (
        (),
        {
            "messages": [
                ToolMessage(content="KEY=sk-zzzzzzzzzzzzzzzz", tool_call_id="1", name="read_file")
            ]
        },
    )

    event = consume(chunk, StreamState(role="planner"))[0]

    assert "sk-zzzz" not in str(event.as_dict())


def test_a_tool_calls_arguments_are_redacted():
    """A model that passes a key as an argument -- `execute` with a curl
    command carrying an Authorization header -- is the same exposure."""
    from langchain_core.messages import AIMessage

    from rudra.trace.stream import StreamState, consume

    call = {
        "name": "execute",
        "args": {"command": "curl -H 'Authorization: Bearer sk-yyyyyyyyyyyy' https://api"},
        "id": "1",
    }
    chunk = ((), {"messages": [AIMessage(content="", tool_calls=[call])]})

    event = consume(chunk, StreamState(role="coder"))[0]

    assert "sk-yyyy" not in event.payload
