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

from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware

from rudra.context.budget import evict_kwargs, execute_kwargs
from rudra.facts import facts_block
from rudra.llm import build_model
from rudra.middleware import FixWriteParamsMiddleware, RepeatGuardMiddleware
from rudra.subagents.spec import FS_TOOL_NAMES, RudraSubagent
from rudra.tools.git_tools import create_git_tools
from rudra.tools.memory_tools import create_memory_tools
from rudra.tools.testing_tools import create_testing_tools

# Every factory that can supply a Rudra tool. Called once each per build;
# the spec then selects by tool name. Names rather than callables because
# the factories need per-run arguments a frozen spec cannot hold.
_TOOL_FACTORIES = (create_git_tools, create_testing_tools, create_memory_tools)


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
    selected: list[Any] = []
    if not spec.rudra_tools:
        selected.extend(_mcp_tools_for(spec, context))
        return selected

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

    selected = [available[name] for name in spec.rudra_tools]
    selected.extend(_mcp_tools_for(spec, context))
    return selected


def _mcp_tools_for(spec: RudraSubagent, context: Any) -> list:
    """The MCP meta-tools this subagent may hold, or nothing.

    Read with getattr for the reason `facts` is: the context is duck-typed
    `Any`, and callers outside a full run build minimal stand-ins.

    The reviewer's restriction is applied here, at registration, not in a
    prompt: an id outside `[mcp] readonly` is neither listed nor callable,
    so it cannot be named at all (S13.5).
    """
    client = getattr(context, "mcp", None)
    mcp_cfg = getattr(getattr(context, "cfg", None), "mcp", None)
    if client is None or not spec.wants_mcp or mcp_cfg is None or not mcp_cfg.enabled:
        return []

    from rudra.mcp.tools import create_mcp_tools

    allow = mcp_cfg.readonly if spec.mcp_readonly else mcp_cfg.allow
    return create_mcp_tools(client, allow=allow, deny=mcp_cfg.deny)


def _prompt_for(spec: RudraSubagent, context: Any, task: str = "") -> str:
    """The spec's prompt, plus whatever this project has established.

    Rendered here rather than baked into the spec because build_agent runs
    once per invocation (runner.py:124): a fact recorded during task 1
    reaches task 2's coder with no plumbing. Shared with to_subagent_spec
    for the reason _tools_for and _middleware_for are shared -- the
    delegating path must not quietly differ from the direct one.

    Before this existed, facts reached the planner's prompt alone
    (planner_agent.py:42), so a project whose facts said Rust had a coder
    that was never told.
    """
    parts = [spec.system_prompt]
    block = facts_block(getattr(context, "facts", None))
    if block:
        parts.append(block)
    # A model never told a server exists will never call list_mcp_tools, so
    # this block is what makes the meta-tool design reachable. Fixed size,
    # and only for the specs that actually hold the tools.
    # Recalled memories, budgeted off the same context_tokens that drives
    # summarization and eviction. Built here rather than baked into the
    # spec for the reason the facts block is: build_agent runs per
    # invocation, so a memory recorded during task 1 reaches task 2's
    # coder with no plumbing.
    store = getattr(context, "memory", None)
    if store is not None and spec.wants_memory:
        from rudra.context.budget import recall_limit
        from rudra.memory.render import recall_block

        # The task text, which is what the recall is supposed to be about.
        # This read `getattr(context, "task", "")`, and SubagentContext has
        # no `task` field -- so the query was always the literal string
        # "coder" / "tester" / "general-purpose", and every task in every
        # run got the same 8 entries nearest the *word* "coder" while
        # usage.record_recall billed them as recall spend (CR-C3).
        query = task.strip() or spec.name
        block = recall_block(store.search(query, limit=8), recall_limit(context.cfg, spec.role))
        if block:
            parts.append(block)
            # What it cost, so RECALL_FRACTION can be revised with evidence
            # rather than argument (spec 4.6). It rides inside input_tokens
            # on every call, so nothing else can isolate it.
            usage = getattr(context, "usage", None)
            if usage is not None:
                usage.record_recall(spec.role, len(block))

    client = getattr(context, "mcp", None)
    if client is not None and spec.wants_mcp:
        from rudra.mcp import mcp_catalog_block

        catalog = mcp_catalog_block(client.servers)
        if catalog:
            parts.append(catalog)
    return "\n\n".join(parts)


def _middleware_for(spec: RudraSubagent, context: Any, model: Any) -> list:
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
        # After the param fixer, so a repaired path is judged as the call it
        # became rather than as the one the model mistyped -- otherwise two
        # spellings of one path count as two different calls (OPEN-10).
        RepeatGuardMiddleware(),
        FilesystemMiddleware(
            backend=context.backend,
            tools=list(spec.fs_tools),
            # A1.47: without this every subagent takes deepagents' 20 000
            # default, which is a third of a 32B window (D6) for one
            # failing-pytest transcript. Splatted, not passed as a value:
            # an explicit None disables eviction outright rather than
            # restoring the default (see evict_kwargs).
            **evict_kwargs(context.cfg, spec.role),
            # CR-F2: otherwise a command hangs for upstream's 3600s default
            # while `[tools] test_timeout` says 600.
            **execute_kwargs(context.cfg),
        ),
    ]
    if getattr(context, "usage", None) is not None:
        from rudra.context.middleware import UsageMiddleware

        middleware.append(UsageMiddleware(spec.role, context.usage))

    if spec.wants_compaction:
        from deepagents.middleware.summarization import create_summarization_tool_middleware

        # Registers the tool layer only. The engine it calls into is the
        # SummarizationMiddleware create_deep_agent already adds; they
        # interoperate through the shared `_summarization_event` state key,
        # so nothing else needs registering. Its own eligibility gate
        # refuses to compact below ~50% of the auto-trigger, so an eager
        # model cannot compact a nearly-empty context.
        #
        # `model` is passed in rather than resolved here: both callers
        # already resolve it for create_deep_agent, and resolving a second
        # time would build two model objects per agent. It is required
        # rather than defaulted because a default silently drops this
        # middleware from any stack whose caller forgot -- which the parity
        # test caught the moment it was written that way. It is also the one
        # middleware needing a real BaseChatModel: the upstream factory
        # type-checks it (summarization.py:1663).
        middleware.append(create_summarization_tool_middleware(model, context.backend))

    if context.gate is not None:
        # First: a denied call must be stopped before anything rewrites its
        # arguments (planner_agent.py:126-128).
        middleware.insert(0, context.gate.middleware)
    return middleware


def _skills_for(spec: RudraSubagent, context: Any) -> list[str] | None:
    """The skill sources this subagent may index, or None for no skills.

    None rather than []: an empty list still installs SkillsMiddleware and
    spends its ~464 tokens of boilerplate on an index with nothing in it.

    Absence is the enforcement here as it is for tools -- the reviewer is
    not told to ignore the library, it simply never sees one.

    Read with getattr for the reason `facts` is (line 90): the context is
    duck-typed `Any`, and callers outside a full run build minimal stand-ins.
    """
    sources = getattr(context, "skills_sources", None)
    if not spec.wants_skills or not sources:
        return None
    return list(sources)


def build_agent(spec: RudraSubagent, context: Any, task: str = "") -> Any:
    """A compiled deep agent for this spec, ready to invoke directly.

    Goes through create_deep_agent so the subagent inherits upstream's
    middleware stack rather than a hand-assembled copy of it. Imported at
    module level so a test can record the call (Step 9c).

    `permissions=` is deliberately absent: it raises NotImplementedError on
    any execute-capable backend, which is every backend Rudra builds
    (TODO.md U.7).

    A checkpointer is always supplied, falling back to an in-memory one.
    `interrupt_on` becomes a HumanInTheLoopMiddleware, which requires one
    (subagents.py:70) -- without it every invocation ends in
    `No checkpointer set` and discards work the subagent already did
    (A1.62). Dropping `interrupt_on` instead would be a security
    downgrade: approvals would vanish from inside subagents under `ask`.
    In-memory is the right default rather than a compromise, because a
    subagent's thread is per-invocation and never resumed (A1.2); a caller
    that wants persistence passes its own.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    model = _model_for(spec, context.cfg)
    return create_deep_agent(
        model=model,
        tools=_tools_for(spec, context),
        system_prompt=_prompt_for(spec, context, task),
        backend=context.backend,
        checkpointer=context.checkpointer or InMemorySaver(),
        middleware=_middleware_for(spec, context, model),
        interrupt_on=context.gate.interrupt_on if context.gate is not None else None,
        skills=_skills_for(spec, context),
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
    _spec_model = _model_for(spec, context.cfg)
    return {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": _prompt_for(spec, context),
        "model": _spec_model,
        "tools": _tools_for(spec, context),
        "middleware": _middleware_for(spec, context, _spec_model),
        "interrupt_on": context.gate.interrupt_on if context.gate is not None else None,
        # Only when the spec wants them: create_sub_agent reads this key
        # (graph.py:676-678), and the delegated path must grant exactly
        # what the direct one does.
        **({"skills": skills} if (skills := _skills_for(spec, context)) else {}),
    }


__all__ = ["_prompt_for", "build_agent", "to_subagent_spec"]
