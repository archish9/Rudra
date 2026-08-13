"""The agentic loop: a task ledger, a fix loop, and a deterministic gate (Step 9c).

The model decides what work exists and what to do next; Python decides
when a task is done and when to stop. C6.1 wanted the agent to own the
todo list; D9 forbids an LLM deciding termination. Both hold because the
ledger tools cannot write DONE and loop/engine.py is the only thing that
can.
"""

from __future__ import annotations

from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.engine import LoopContext, Outcome, plan, run_loop, run_task, summarise, work
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.tools import create_ledger_tools

__all__ = [
    "Ledger",
    "LoopContext",
    "Outcome",
    "Task",
    "TaskStatus",
    "create_ledger_tools",
    "failure_signature",
    "plan",
    "run_loop",
    "run_task",
    "summarise",
    "tests_produced_no_judgement",
    "work",
]
