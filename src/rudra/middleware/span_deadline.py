"""Stop a span BEFORE the next model call, not after the last one (OPEN-114).

**The defect.** Both time bounds read the clock at the top of their chunk loop
and `break`. A `values` chunk carrying an `AIMessage` with tool calls is yielded
after the model node returns and before the tool node has run, and `break`
closes the async generator langgraph's step loop lives inside -- so the halt
landed on a finished answer and cancelled the step that would have used it.

Run `8f160d92c6da`'s clarify stage waited 733.7 s plus a 218.1 s retry for a
`record_fact project_type` and threw it away; architect did the same with a
549.4 s answer. `facts.json` holds `layout` alone, and **clarify ended with zero
facts having produced one**. Neither stage was looping -- three tool calls each.
On the subagent stack the discarded call is typically `write_file`, the
deliverable itself, and the invocation is then recorded in `ledger.json`
`halts` as though it produced nothing.

**The limit's value was never the defect** and is not touched: 1200 s did its
job as a runaway bound. The check was in the wrong place.

So the clock is read where stopping costs nothing: a `before_model` hook, which
runs after the previous answer's tools ran and before the next call is paid for.
`hook_config(can_jump_to=["end"])` is upstream's mechanism for it
(`langchain/agents/middleware/types.py`, wired in `factory.py`), and Rudra had
no other user of it. Checked against that run: the same halt times (1888 s,
1467 s), both facts kept.

**A span can still overrun its limit by the one model call in flight when the
limit passes.** That was already true -- the old check could only fire when
that call's chunk arrived -- so nothing gets worse, and the answer is now kept.

**It announces nothing.** The halt sentence needs the span's tool-call count
and its model-latency share, which only the span owner holds, so this records
`tripped_at` and the owner says so once the stream ends
(`agent/planner_agent.py::_stream_planner_turn`, `subagents/runner.py`). That
keeps `roles.planner.planner_halts`, the `planner-guard`/`guard` NOTICE,
`ledger.json` `halts` and the nudge suppression on exactly the seams they were
on before.

Registered on BOTH stacks. A `task` delegate's subgraph inherits the
ContextVar, so a parent's deadline also ends a delegate at its next model call
-- intended: an invocation's bound covers what it delegates.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, hook_config

from rudra.context.deadline import SPAN_DEADLINE


class SpanDeadlineMiddleware(AgentMiddleware):
    """End the agent when the active span is out of seconds.

    Inert with no span: `SPAN_DEADLINE` defaults to None, so every agent built
    outside a run -- the compat suites among them -- streams exactly as it did
    before this existed.
    """

    def _verdict(self) -> dict[str, Any] | None:
        deadline = SPAN_DEADLINE.get()
        if deadline is None or not deadline.expired():
            return None
        # Recorded, not announced: the owner of the span builds the sentence.
        deadline.trip()
        return {"jump_to": "end"}

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._verdict()

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        # Both spellings, because `_get_can_jump_to` reads whichever the class
        # overrides and Rudra streams async while its tests build sync graphs.
        return self._verdict()


__all__ = ["SpanDeadlineMiddleware"]
