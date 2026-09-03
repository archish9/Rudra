"""What a run cost, written where a bug report can carry it (OPEN-87/88/89).

Filed from run `fc543fb2b82f` -- "create a simple single html page", 2.03
hours, of which 6,089 s was model latency and 6.0 s was every tool call in
the run added together. None of those numbers was stated in any file. They
were derived by differencing `ts` across adjacent lines of a 441-line debug
log with a throwaway parser, which is not something a user filing a bug will
do, and Rudra ships as an open-source project whose whole triage story is
"send me your logs folder".

Three holes, one deliverable:

* a model call's duration existed only inside `RoleUsage.seconds`, a
  per-role TOTAL that reached disk once, at run end (OPEN-87)
* `usage.json` was written from `close()` and from `work()`'s last
  statement -- both ENDS -- so a run somebody is complaining about, which
  is by definition one that has not finished, had none (OPEN-88)
* `run_subagent` counted an invocation's tool calls for the runaway bound
  and dropped the number at every `return` (OPEN-89)

These tests pin the records themselves, not their prose: a reader is told
to grep `"kind": "model_call"`, so the kind strings are asserted literally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from rudra.context.middleware import MODEL_CALL_KIND, UsageMiddleware, log_model_call
from rudra.context.usage import RunUsage
from rudra.loop.engine import flush_usage
from rudra.subagents import runner as runner_module
from rudra.subagents.runner import SUBAGENT_KIND, TOP_TOOLS, log_invocation
from rudra.trace.debug import configure_debug_logging


@pytest.fixture
def captured(tmp_path):
    """The real debug-log handler, so these assert the shipped route.

    A caplog fixture would prove the logger was called and nothing about
    what `debug-<id>.jsonl` actually holds -- and the promotion of `event`
    to the top level of the object is `_JsonLines.format`'s doing, not the
    caller's. That promotion is the entire reason these records are
    greppable rather than a string somebody has to parse out of a message.
    """
    path = Path(tmp_path) / "debug-test.jsonl"
    handler = configure_debug_logging(path, enabled=True)
    assert handler is not None
    yield path
    handler.flush()
    logging.getLogger("rudra").removeHandler(handler)
    handler.close()


def _lines(path: Path, kind: str) -> list[dict]:
    return [
        record
        for record in (json.loads(line) for line in path.read_text().splitlines() if line.strip())
        if record.get("kind") == kind
    ]


def _meta(input_tokens: int, output_tokens: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _response(usage_metadata=None):
    return ModelResponse(result=[AIMessage(content="hi", usage_metadata=usage_metadata)])


# --------------------------------------------------------------------------
# OPEN-87 -- one line per model call
# --------------------------------------------------------------------------


def test_a_model_call_writes_its_own_cost_to_the_debug_log(captured):
    usage = RunUsage()
    middleware = UsageMiddleware("coder", usage)

    middleware.wrap_model_call(
        object(),
        lambda request: _response(_meta(15012, 180)),
    )
    logging.getLogger("rudra").handlers[0].flush()

    (record,) = _lines(captured, MODEL_CALL_KIND)
    assert record["role"] == "coder"
    assert record["input_tokens"] == 15012
    assert record["output_tokens"] == 180
    assert record["ok"] is True
    assert record["seconds"] >= 0.0
    # OPEN-77's rule: every line in this file carries a wall clock, so a
    # reader can place it in a day without summing stage durations.
    assert "ts" in record


def test_the_async_path_logs_too():
    """Rudra streams, so `awrap_model_call` is the path that actually runs.

    The sync hook exists because every other middleware in the stack
    implements both; a record on one path only is a silent hole on the
    path production takes.
    """
    usage = RunUsage()
    middleware = UsageMiddleware("planner", usage)
    seen: list[dict] = []
    logger = logging.getLogger("rudra.context.usage")

    class _Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.event)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:

        async def handle(request):
            return _response(_meta(7, 1))

        asyncio.run(middleware.awrap_model_call(object(), handle))
    finally:
        logger.removeHandler(handler)

    assert [record["kind"] for record in seen] == [MODEL_CALL_KIND]
    assert seen[0]["role"] == "planner"


def test_a_call_that_raised_is_logged_as_a_failure_and_still_raises(captured):
    """The call most worth naming: it cost the wait and bought nothing.

    `RoleUsage` cannot tell it apart from a successful call that reported
    no tokens -- run `fc543fb2b82f` took four provider 500s and the log
    carried the retry notices with no cost attached to either the failure
    or the re-issue.
    """
    middleware = UsageMiddleware("coder", RunUsage())

    def boom(request):
        raise RuntimeError("InternalServerError")

    with pytest.raises(RuntimeError):
        middleware.wrap_model_call(object(), boom)
    logging.getLogger("rudra").handlers[0].flush()

    (record,) = _lines(captured, MODEL_CALL_KIND)
    assert record["ok"] is False
    assert record["error"] == "RuntimeError"
    assert record["seconds"] >= 0.0


def test_logging_a_model_call_never_raises():
    """Accounting must not be the thing that ends a run.

    `write_usage_log`'s rule (loop/engine.py), applied to the two records
    added by these items. A value json cannot serialise is the realistic
    way this breaks, since the tokens come from a provider's metadata.
    """

    class Unserialisable:
        pass

    log_model_call("coder", 1.0, input_tokens=Unserialisable())  # type: ignore[arg-type]
    log_invocation("coder", seconds=1.0, total_calls=1, tools={"glob": Unserialisable()}, ok=True)  # type: ignore[dict-item]


# --------------------------------------------------------------------------
# OPEN-89 -- one line per subagent invocation, with its tool mix
# --------------------------------------------------------------------------


def test_an_invocation_summary_carries_the_tool_histogram(captured):
    """The half that carries the diagnosis rather than the symptom.

    "55 calls" says t7 was busy. `glob: 41` says it was hunting the
    filesystem for an interpreter it cannot use (OPEN-91).
    """
    log_invocation(
        "coder",
        seconds=2704.297,
        total_calls=55,
        tools={"glob": 41, "read_file": 11, "grep": 2, "remember": 1},
        ok=False,
        halted="'edit_file' on 'index.html' repeated 3x -- stopping",
    )
    logging.getLogger("rudra").handlers[0].flush()

    (record,) = _lines(captured, SUBAGENT_KIND)
    assert record["role"] == "coder"
    assert record["tool_calls"] == 55
    assert record["seconds"] == 2704.3
    assert record["ok"] is False
    assert "repeated 3x" in record["halted"]
    # Busiest first, so the shape of the invocation is the first thing read.
    assert list(record["tools"]) == ["glob", "read_file", "grep", "remember"]


def test_the_histogram_is_capped_busiest_first():
    """A cap, not the whole tally: the point is the SHAPE of an invocation,
    and a long tail of ones adds bytes without adding that."""
    seen: list[dict] = []
    logger = logging.getLogger("rudra.subagents.runner")

    class _Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.event)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        tools = {f"tool{index}": index for index in range(TOP_TOOLS + 5)}
        log_invocation("coder", seconds=1.0, total_calls=sum(tools.values()), tools=tools, ok=True)
    finally:
        logger.removeHandler(handler)

    listed = seen[0]["tools"]
    assert len(listed) == TOP_TOOLS
    counts = list(listed.values())
    assert counts == sorted(counts, reverse=True)
    # total_calls is the true total and is NOT the sum of the listed tools.
    assert seen[0]["tool_calls"] == sum(tools.values())


# --------------------------------------------------------------------------
# OPEN-88 -- usage.json exists while the run is still going
# --------------------------------------------------------------------------


class _Paths:
    def __init__(self, logs):
        self.logs = logs


class _Context:
    def __init__(self, usage, paths):
        self.usage = usage
        self.paths = paths


def test_the_tally_is_written_before_the_run_ends(tmp_path):
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=2, seconds=38.7)
    logs = Path(tmp_path) / "logs"

    flush_usage(_Context(usage, _Paths(logs)))

    written = json.loads((logs / "usage.json").read_text())
    assert written["roles"]["coder"]["calls"] == 1
    assert written["roles"]["coder"]["seconds"] == 38.7
    # OPEN-53's block, and the number a slow-run report is actually about.
    assert "wall_seconds" in written["run"]


def test_flushing_again_overwrites_rather_than_appends(tmp_path):
    """Called per ATTEMPT, so it runs many times in a long task. It must
    leave one document, not a growing pile of them."""
    usage = RunUsage()
    logs = Path(tmp_path) / "logs"
    context = _Context(usage, _Paths(logs))

    usage.record("coder", input_tokens=1, output_tokens=1, seconds=1.0)
    flush_usage(context)
    usage.record("coder", input_tokens=1, output_tokens=1, seconds=1.0)
    flush_usage(context)

    assert json.loads((logs / "usage.json").read_text())["roles"]["coder"]["calls"] == 2


@pytest.mark.parametrize(
    "context",
    [
        _Context(None, _Paths("unused")),
        _Context(RunUsage(), None),
        _Context(RunUsage(), _Paths(None)),
        object(),
    ],
)
def test_flushing_is_a_no_op_when_the_run_has_no_tally(context):
    """Read defensively for the reason `_write_usage_log` is: several tests
    build a LoopContext with no usage and no paths, and an observability
    call must not be what makes those unbuildable."""
    flush_usage(context)


# --------------------------------------------------------------------------
# OPEN-89 -- the wiring, not just the writer
# --------------------------------------------------------------------------


class _Records(logging.Handler):
    """Collects the promoted `event` dict off each record."""

    def __init__(self):
        super().__init__()
        self.seen: list[dict] = []

    def emit(self, record):
        self.seen.append(getattr(record, "event", {}))


@pytest.fixture
def invocations():
    logger = logging.getLogger("rudra.subagents.runner")
    handler = _Records()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler.seen
    logger.removeHandler(handler)


async def test_run_subagent_logs_what_the_invocation_spent(monkeypatch, invocations, tmp_path):
    """The tally is kept for the runaway bound; before OPEN-89 it was
    dropped at `return` and the per-invocation table existed in no file."""
    from tests.test_subagents_runner import ai, call, make_context, stream_of

    monkeypatch.setattr(runner_module, "build_agent", lambda spec, context, task="": object())
    messages = [
        ai("looking", [call("glob", pattern="/usr/bin/python*")]),
        ai("still looking", [call("glob", pattern="/usr/bin/python3")]),
        ai("reading", [call("read_file", file_path="/index.html")]),
        ai("done"),
    ]
    monkeypatch.setattr(runner_module, "run_with_approvals", stream_of({"messages": messages}))

    result = await runner_module.run_subagent("coder", "go", context=make_context(tmp_path))

    assert result.ok is True
    (record,) = [entry for entry in invocations if entry.get("kind") == SUBAGENT_KIND]
    assert record["role"] == "coder"
    assert record["tool_calls"] == 3
    assert record["tools"] == {"glob": 2, "read_file": 1}
    assert record["ok"] is True
    assert record["seconds"] >= 0.0


async def test_a_build_failure_is_logged_too(monkeypatch, invocations, tmp_path):
    """Started before `build_agent`, not after: a spec that fails to
    assemble still cost the user the wait, and an invocation missing from
    the log is the shape this item was filed on."""
    from tests.test_subagents_runner import make_context

    def boom(spec, context, task=""):
        raise RuntimeError("no model")

    monkeypatch.setattr(runner_module, "build_agent", boom)

    result = await runner_module.run_subagent("coder", "go", context=make_context(tmp_path))

    assert result.ok is False
    (record,) = [entry for entry in invocations if entry.get("kind") == SUBAGENT_KIND]
    assert record["ok"] is False
    assert record["error"] == "no model"
    assert record["tool_calls"] == 0


async def test_a_halted_invocation_names_the_guard_that_stopped_it(
    monkeypatch, invocations, tmp_path
):
    from tests.test_subagents_runner import ai, call, make_context, stream_of

    monkeypatch.setattr(runner_module, "build_agent", lambda spec, context, task="": object())
    repeated = [ai("x", [call("write_file", file_path="/a.py", content="same")]) for _ in range(3)]
    monkeypatch.setattr(runner_module, "run_with_approvals", stream_of({"messages": repeated}))

    result = await runner_module.run_subagent("coder", "go", context=make_context(tmp_path))

    assert result.ok is False
    (record,) = [entry for entry in invocations if entry.get("kind") == SUBAGENT_KIND]
    assert record["ok"] is False
    assert "repeated 3x" in record["halted"]
    # The halt is a fact about the invocation, and so is what it spent
    # getting there. Both, or the log answers "it stopped" and not "why".
    assert record["tool_calls"] == 3
