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

    assert usage.as_dict()["coder"] == {
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 10,
        "compactions": 0,
        # Added in Step 14b: the recall block's cost, isolated because it
        # otherwise rides invisibly inside input_tokens (spec 4.6).
        "recall_chars": 0,
        "recall_injections": 0,
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
    """Never let accounting be the thing that ends a run."""
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    middleware.wrap_model_call(object(), lambda request: ModelResponse(result=[]))

    assert usage.roles() == ()


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
