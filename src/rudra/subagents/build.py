"""Turning a spec into a running agent. The only place that assembles one.

Two consumers share this module so they cannot disagree about what a
subagent is:

  build_agent()       -> a compiled deep agent, invoked directly by
                         runner.py and, later, by Step 9c's loop
  to_subagent_spec()  -> a deepagents SubAgent dict, for a parent that
                         delegates through the `task` tool

Both route through _tools_for and _middleware_for, and a parity test
asserts they produce identical names for every registry entry. Without
that, the delegating path could quietly lose the gate -- which is exactly
what deepagents' own default does, since it never propagates the parent's
`middleware=` to a subagent (graph.py:666-703).
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.filesystem import FilesystemMiddleware

from rudra.llm import build_model
from rudra.middleware import FixWriteParamsMiddleware
from rudra.subagents.spec import FS_TOOL_NAMES, RudraSubagent
from rudra.tools.git_tools import create_git_tools
from rudra.tools.testing_tools import create_testing_tools

# Every factory that can supply a Rudra tool. Called once each per build;
# the spec then selects by tool name. Names rather than callables because
# the factories need per-run arguments a frozen spec cannot hold.
_TOOL_FACTORIES = (create_git_tools, create_testing_tools)


def _model_for(spec: RudraSubagent, cfg: Any = None) -> Any:
    """The chat model for this subagent's role.

    build_model falls back to `default` for an unknown role
    (llm/factory.py:71), so a spec naming a role the user has not
    configured still runs rather than failing the build.
    """
    return build_model(spec.role, cfg)


def _tools_for(spec: RudraSubagent, context: Any) -> list:
    """The Rudra tools this subagent asked for, in declaration order.

    An unknown name raises rather than being dropped: a subagent silently
    missing the one tool its prompt tells it to call would loop asking for
    something that is not there.
    """
    if not spec.rudra_tools:
        return []

    available: dict[str, Any] = {}
    for factory in _TOOL_FACTORIES:
        for tool in factory(
            context.project_path,
            gate=context.gate,
            console=context.console,
            cfg=context.cfg,
        ):
            available[tool.name] = tool

    missing = [name for name in spec.rudra_tools if name not in available]
    if missing:
        known = ", ".join(sorted(available))
        msg = f"subagent '{spec.name}' asks for unknown tool(s) {missing}; available: {known}"
        raise ValueError(msg)

    return [available[name] for name in spec.rudra_tools]


def _middleware_for(spec: RudraSubagent, context: Any) -> list:
    """The middleware stack: gate first, then argument repair, then filesystem.

    The FilesystemMiddleware is constructed here rather than inherited so
    the tool list is scoped to the spec. Passing one in `middleware=`
    replaces the default by name, in place, on both the main-agent path
    (graph.py:882) and the subagent path (graph.py:221-228) -- which is
    what makes the reviewer structurally unable to write: the tools are
    never registered, so there is nothing to deny (U.17).

    TaskAnchorMiddleware is deliberately absent. It is a D4 compat shim for
    qwen3:14b losing the task mid-run, default off since C1.8, and a
    subagent's prompt is already narrow enough that re-anchoring it would
    only spend tokens.
    """
    unknown = [name for name in spec.fs_tools if name not in FS_TOOL_NAMES]
    if unknown:
        known = ", ".join(sorted(FS_TOOL_NAMES))
        msg = (
            f"subagent '{spec.name}' asks for unknown filesystem tool(s) {unknown}; valid: {known}"
        )
        raise ValueError(msg)

    middleware: list[Any] = [
        FixWriteParamsMiddleware(strip_sandbox_prefixes=context.cfg.compat.sandbox_paths),
        FilesystemMiddleware(backend=context.backend, tools=list(spec.fs_tools)),
    ]
    if context.gate is not None:
        # First: a denied call must be stopped before anything rewrites its
        # arguments (planner_agent.py:126-128).
        middleware.insert(0, context.gate.middleware)
    return middleware


def build_agent(spec: RudraSubagent, context: Any) -> Any:
    """A compiled deep agent for this spec, ready to invoke directly.

    The same create_deep_agent call create_coder_agent already makes
    (coder_agent.py:90), so the subagent inherits upstream's middleware
    stack rather than a hand-assembled copy of it.

    `permissions=` is deliberately absent: it raises NotImplementedError on
    any execute-capable backend, which is every backend Rudra builds
    (TODO.md U.7).
    """
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=_model_for(spec, context.cfg),
        tools=_tools_for(spec, context),
        system_prompt=spec.system_prompt,
        backend=context.backend,
        checkpointer=context.checkpointer,
        middleware=_middleware_for(spec, context),
        interrupt_on=context.gate.interrupt_on if context.gate is not None else None,
    )


def to_subagent_spec(spec: RudraSubagent, context: Any) -> dict:
    """A deepagents SubAgent dict, for a parent that delegates via `task`.

    Nothing in 9b passes this to create_deep_agent -- the parent that
    should delegate is Step 9c's. It exists now so the delegating path is
    built from the same assembly as the direct one and tested alongside it,
    rather than being written later against a different understanding.

    `model` and `tools` are always set because create_sub_agent raises
    without them (subagents.py:358-363).
    """
    return {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
        "model": _model_for(spec, context.cfg),
        "tools": _tools_for(spec, context),
        "middleware": _middleware_for(spec, context),
        "interrupt_on": context.gate.interrupt_on if context.gate is not None else None,
    }


__all__ = ["build_agent", "to_subagent_spec"]
