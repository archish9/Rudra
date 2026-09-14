"""UsageMiddleware reads usage_metadata off the response (Step 12b, C7.5).

Both the sync and async hooks exist because Rudra streams (astream, so
awrap fires) while every other middleware in the stack implements both --
a middleware that covers one is a silent hole on whichever path it missed.
"""

from __future__ import annotations

import asyncio

from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from rudra.context.middleware import UsageMiddleware
from rudra.context.usage import RunUsage


def _response(usage_metadata=None):
    return ModelResponse(result=[AIMessage(content="hi", usage_metadata=usage_metadata)])


def _meta(input_tokens, output_tokens):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def test_sync_hook_records_the_reported_counts():
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    result = middleware.wrap_model_call(object(), lambda request: _response(_meta(100, 10)))

    recorded = usage.as_dict()["coder"]
    # seconds is measured, not fixed -- checked separately below.
    assert recorded.pop("seconds") >= 0.0
    assert recorded == {
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 10,
        "compactions": 0,
        # Added by OPEN-45, and zero here for the reason it must be: this
        # middleware never sees a retry. ModelRetryMiddleware sits OUTSIDE
        # it by design, so each attempt arrives here as its own call.
        "retries": 0,
        # Added by OPEN-46 (reopened), and zero here for the same reason
        # `retries` is: the give-up is raised by ModelRetryMiddleware, which
        # sits outside this one, so an exhausted call arrives here as four
        # ordinary attempts and nothing else.
        "exhaustions": 0,
        # Added in Step 14b: the recall block's cost, isolated because it
        # otherwise rides invisibly inside input_tokens (spec 4.6).
        "recall_chars": 0,
        "recall_injections": 0,
        # Added by OPEN-39, and zero here for the same reason: the project
        # listing is injected at prompt assembly, never at call time.
        "tree_chars": 0,
        "tree_injections": 0,
        # Added by OPEN-39 Phase 2, and zero here for the third instance of
        # the same reason: a deduped read is a TOOL call the repeat guard
        # answered, and this middleware wraps model calls.
        "reads_deduped": 0,
        # Added by OPEN-60, and zero here for the fourth instance of the same
        # reason: a skipped write is a TOOL call the repeat guard refused, and
        # this middleware wraps model calls.
        "writes_skipped": 0,
        "writes_skipped_chars": 0,
        # Added by OPEN-92, and zero here for the fifth instance of the same
        # reason: a repaired edit is a TOOL call GutterIndentMiddleware
        # rewrote, and this middleware wraps model calls.
        "edits_reindented": 0,
        "plans_refused": 0,
        "content_paths_flagged": 0,
        # Added by OPEN-97, and zero here for the sixth instance of the same
        # reason: a refused prose write is a TOOL call FixWriteParams-
        # Middleware answered, and this middleware wraps model calls.
        "writes_rejected_as_prose": 0,
        # Added by OPEN-99, zero for that same reason once more: a refused
        # test write is a TOOL call TestExtensionMiddleware answered, and
        # this middleware wraps model calls.
        "test_writes_rejected": 0,
        "planner_halts": 0,
        "planner_writes_refused": 0,
        # Added by OPEN-103, zero for the same reason as OPEN-99's line: a
        # routed call is a TOOL call ToolRouteMiddleware answered.
        "tool_routes_answered": 0,
    }
    assert result.result[0].content == "hi"


def test_async_hook_records_the_reported_counts():
    usage = RunUsage()
    middleware = UsageMiddleware("planner", usage)

    async def handler(request):
        return _response(_meta(7, 1))

    asyncio.run(middleware.awrap_model_call(object(), handler))

    assert usage.as_dict()["planner"]["input_tokens"] == 7


def test_a_response_without_usage_metadata_counts_the_call_only():
    """A local endpoint may report nothing. That is one call, not zero."""
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    middleware.wrap_model_call(object(), lambda request: _response(None))

    assert usage.as_dict()["coder"]["calls"] == 1
    assert usage.as_dict()["coder"]["input_tokens"] is None


def test_a_response_with_no_messages_is_survived():
    """Never let accounting be the thing that ends a run.

    Step 15a changed what this records, not whether it survives: an empty
    response has no token counts, but the call still took time, and a
    provider that returns nothing slowly is exactly what somebody would be
    trying to diagnose. The role now appears with both counts unreported.
    """
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    middleware.wrap_model_call(object(), lambda request: ModelResponse(result=[]))

    assert usage.roles() == ("coder",)
    assert usage.as_dict()["coder"]["input_tokens"] is None
    assert usage.as_dict()["coder"]["output_tokens"] is None


def test_the_response_is_returned_unchanged():
    """This middleware observes. It must never rewrite what the model said."""
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)
    response = _response(_meta(1, 1))

    assert middleware.wrap_model_call(object(), lambda request: response) is response


def test_compact_conversation_calls_are_counted():
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    class Request:
        tool_call = {"name": "compact_conversation", "args": {}}

    middleware.wrap_tool_call(Request(), lambda request: "compacted")

    assert usage.as_dict()["coder"]["compactions"] == 1


def test_other_tool_calls_are_not_counted_as_compactions():
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    class Request:
        tool_call = {"name": "write_file", "args": {}}

    middleware.wrap_tool_call(Request(), lambda request: "written")

    assert usage.roles() == ()


def test_the_middleware_records_wall_clock():
    import time

    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    def slow(request):
        time.sleep(0.01)
        return _response(_meta(1, 1))

    middleware.wrap_model_call(object(), slow)

    assert usage.as_dict()["coder"]["seconds"] >= 0.01


def test_the_async_hook_records_wall_clock_too():
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    async def slow(request):
        await asyncio.sleep(0.01)
        return _response(_meta(1, 1))

    asyncio.run(middleware.awrap_model_call(object(), slow))

    assert usage.as_dict()["coder"]["seconds"] >= 0.01


def test_a_failing_model_call_still_records_the_time_it_burned():
    import time

    """A call that raised still cost the user the wait, and a run that dies
    slowly is exactly when somebody wants to know where the time went."""
    import pytest

    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    def boom(request):
        time.sleep(0.01)  # as_dict rounds to ms; an instant raise is 0.0
        raise RuntimeError("provider is down")

    with pytest.raises(RuntimeError):
        middleware.wrap_model_call(object(), boom)

    assert usage.as_dict()["coder"]["seconds"] >= 0.01


# --- what a failed call SAYS (OPEN-109) -------------------------------------


def _capture_records():
    """The debug log's own formatter over an in-memory handler.

    Formatted rather than inspected, because the thing under test is what
    reaches the FILE: `error_detail` is built here and `traceback` is added
    by `_JsonLines`, and a test that read `record.event` would see only the
    first half.
    """
    import io
    import json
    import logging

    from rudra.trace.debug import _JsonLines

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(_JsonLines())
    logger = logging.getLogger("rudra")
    previous = logger.handlers[:]
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    def read():
        handler.flush()
        return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]

    def restore():
        logger.handlers = previous

    return read, restore


def test_a_failed_model_call_records_what_the_provider_said():
    """`type(exc).__name__` alone made a 400, a 401 and a timeout one word.

    Measured before the fix: run f845b496a2aa's complete record held 149
    model_call lines and zero tracebacks, so the one field a maintainer
    reads was in hand at the moment of failure and discarded -- CLAUDE.md
    §8a failure shape 1.
    """
    import pytest

    from rudra.context.middleware import UsageMiddleware
    from rudra.context.usage import RunUsage

    read, restore = _capture_records()
    try:

        def boom(request):
            raise RuntimeError("400 model 'qwen3:32b' not found")

        with pytest.raises(RuntimeError):
            UsageMiddleware("coder", RunUsage()).wrap_model_call(object(), boom)
        records = read()
    finally:
        restore()

    line = next(record for record in records if record.get("kind") == "model_call")
    assert line["ok"] is False
    assert line["error"] == "RuntimeError"
    assert "qwen3:32b" in line["error_detail"]
    assert "not found" in line["traceback"]


def test_what_the_provider_said_is_redacted_in_both_places():
    """A provider's exception carries whatever it was given -- including a
    URL with a key in it -- and this file is attached to public issues.

    The traceback is the half that was raw: `error_detail` goes through
    `redact` where it is built, and `_JsonLines` formatted the exception
    itself straight into the record beside it.
    """
    import pytest

    from rudra.context.middleware import UsageMiddleware
    from rudra.context.usage import RunUsage

    read, restore = _capture_records()
    try:

        def boom(request):
            raise RuntimeError("401 unauthorized: api_key=sk-abcdefgh12345")

        with pytest.raises(RuntimeError):
            UsageMiddleware("coder", RunUsage()).wrap_model_call(object(), boom)
        records = read()
    finally:
        restore()

    line = next(record for record in records if record.get("kind") == "model_call")
    assert "sk-abcdefgh12345" not in line["error_detail"]
    assert "sk-abcdefgh12345" not in line["traceback"]
    assert "<redacted>" in line["traceback"]


def test_a_successful_call_carries_no_error_detail_and_no_traceback():
    """The common line stays the size it was: this is one line per model
    call in a file with uncapped payloads."""
    from rudra.context.middleware import UsageMiddleware
    from rudra.context.usage import RunUsage

    read, restore = _capture_records()
    try:
        UsageMiddleware("coder", RunUsage()).wrap_model_call(
            object(), lambda request: _response(_meta(1, 1))
        )
        records = read()
    finally:
        restore()

    line = next(record for record in records if record.get("kind") == "model_call")
    assert line["error_detail"] == ""
    assert "traceback" not in line


def test_the_exception_text_is_bounded():
    """A provider error can carry a whole response body."""
    import pytest

    from rudra.context.middleware import ERROR_CHARS, UsageMiddleware
    from rudra.context.usage import RunUsage

    read, restore = _capture_records()
    try:

        def boom(request):
            raise RuntimeError("x" * (ERROR_CHARS * 3))

        with pytest.raises(RuntimeError):
            UsageMiddleware("coder", RunUsage()).wrap_model_call(object(), boom)
        records = read()
    finally:
        restore()

    line = next(record for record in records if record.get("kind") == "model_call")
    assert len(line["error_detail"]) == ERROR_CHARS
