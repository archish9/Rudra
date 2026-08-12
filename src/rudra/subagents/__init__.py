"""Rudra's subagents: coder, tester, reviewer, general-purpose (Step 9b).

Invoked deterministically from Python by Step 9c's loop, not chosen by a
model. `to_subagent_spec` exists for a parent that should delegate through
the `task` tool, and is built from the same assembly so the two paths
cannot disagree about what a subagent is.
"""

from __future__ import annotations

from rudra.subagents.build import build_agent, to_subagent_spec
from rudra.subagents.registry import REGISTRY
from rudra.subagents.runner import SubagentContext, SubagentResult, run_subagent
from rudra.subagents.spec import RudraSubagent

__all__ = [
    "REGISTRY",
    "RudraSubagent",
    "SubagentContext",
    "SubagentResult",
    "build_agent",
    "run_subagent",
    "to_subagent_spec",
]
