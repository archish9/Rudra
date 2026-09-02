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
from rudra.middleware import (
    DelegationGuardMiddleware,
    ExecuteGuardMiddleware,
    FixWriteParamsMiddleware,
    ModelRetryMiddleware,
    RepeatGuardMiddleware,
)
from rudra.subagents.registry import PROJECT_PATH_TOKEN
from rudra.subagents.spec import FS_TOOL_NAMES, RudraSubagent
from rudra.tools.git_tools import create_git_tools
from rudra.tools.memory_tools import create_memory_tools
from rudra.tools.testing_tools import create_testing_tools

# Every factory that can supply a Rudra tool. Called once each per build;
# the spec then selects by tool name. Names rather than callables because
# the factories need per-run arguments a frozen spec cannot hold.
_TOOL_FACTORIES = (create_git_tools, create_testing_tools, create_memory_tools)

# How many paths the PROJECT FILES block may list. Below project_tree's own
# 300 default on purpose: 300 paths is roughly 1,800 tokens paid on every
# call the coder makes -- 133 of them in run8 -- and the cap is the only
# thing that keeps this block off a large repository's every call. It is a
# constant rather than a config key because an inert key is worse than no
# key (CLAUDE.md 6); `tree_chars` in usage.json is what revises it.
TREE_MAX_ENTRIES = 150


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
    # OPEN-81. The path contract states where the project is, and the root
    # is a per-run fact while a spec is built at import -- so it carries a
    # placeholder and this renders it, for the same reason the facts block
    # is built here rather than baked in.
    #
    # `str.replace` rather than `.format`: formatting the whole prompt
    # would make every future brace in it -- a JSON example, a dict
    # literal, an f-string in a code sample -- a KeyError in prompt
    # assembly. Only the specs whose rules ask for the anchor read
    # project_path, so a stand-in context without one still builds a
    # reviewer.
    prompt = spec.system_prompt
    if PROJECT_PATH_TOKEN in prompt:
        prompt = prompt.replace(PROJECT_PATH_TOKEN, str(context.project_path))
    parts = [prompt]
    block = facts_block(getattr(context, "facts", None))
    if block:
        parts.append(block)

    # What is on disk, after what was decided and before what was
    # remembered. Rendered here rather than baked into the spec for the
    # reason the facts block is -- build_agent runs once per invocation --
    # and deliberately NOT cached across invocations: these agents write
    # the files they are being shown, so a stale listing is a correctness
    # bug rather than a performance trade (OPEN-39).
    if spec.wants_tree:
        from rudra.filesystem import as_virtual_paths, project_tree

        # Rendered in the spelling `ls` and `glob` ANSWER in, not the
        # relative one project_tree emits (OPEN-81). The model was being
        # shown three spellings of one path -- relative here, `/`-prefixed
        # in every tool result, and "must be absolute" in the tool schemas
        # -- and it resolved the conflict with a root of its own invention.
        # This is the only one of the three Rudra controls, so it is the
        # one that moves.
        listing = as_virtual_paths(project_tree(context.project_path, max_entries=TREE_MAX_ENTRIES))
        parts.append(
            "## PROJECT FILES\n\n"
            "Every file in this project, current as of this call. You do not "
            "need to `ls` or `glob` to find out what exists.\n\n"
            f"```\n{listing}\n```"
        )
        # What it cost, for the reason the recall block's cost is recorded:
        # this block is re-sent on every call whether or not it is read, so
        # it buys model calls with prompt tokens and only a number says
        # which side won.
        usage = getattr(context, "usage", None)
        if usage is not None:
            usage.record_tree(spec.role, len(parts[-1]))

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
        # OPEN-41. Outermost of the MODEL-call wrappers, which is the only
        # thing its position decides -- it implements the model-call hooks
        # only and never sees a tool argument, so it has no claim on the
        # front seat U.14 keeps for the param fixer. Ahead of the
        # UsageMiddleware appended below: each ATTEMPT is then one recorded
        # call with its own duration, and the backoff sleep is charged to
        # nobody. Inside the accounting, a twice-retried call would read as
        # one 40-second call that was mostly asyncio.sleep -- precisely the
        # number OPEN-40 exists to make trustworthy.
        # `trace` and `usage` are what make it audible (OPEN-45): it was
        # registered on every spec and reported nothing, so a coder
        # absorbing a third of its requests read as a slow model. getattr
        # for the reason `facts` is read that way (line 90) -- the context
        # is duck-typed and callers outside a full run build stand-ins.
        ModelRetryMiddleware(
            spec.role,
            trace=getattr(context, "trace", None),
            usage=getattr(context, "usage", None),
        ),
        # After the param fixer, so a repaired path is judged as the call it
        # became rather than as the one the model mistyped -- otherwise two
        # spellings of one path count as two different calls (OPEN-10).
        RepeatGuardMiddleware(
            # OPEN-39 Phase 2. The guard answers repeated reads itself, and
            # a call that never happens leaves no mark on tokens, seconds
            # or tool results -- so without this the saving is unmeasurable
            # and the next session re-runs the ledger's re-read script by
            # hand, which is the third time that would have happened.
            role=spec.role,
            usage=getattr(context, "usage", None),
            # OPEN-57. The counter above says a refusal happened; only this
            # says WHICH call was refused, and it is what stops the refusal
            # reaching the trace as a line the user typed. Same getattr and
            # the same reason as ModelRetryMiddleware's above.
            trace=getattr(context, "trace", None),
            # OPEN-62 6a. The write-belief now outlives this invocation, on
            # `usage`, because every task builds a new one of these
            # (`runner.py:263`) and run13 spent two whole coder invocations
            # re-emitting files an earlier task had written. A belief it did
            # not make itself refuses nothing until the file on disk agrees,
            # and this is the path that makes that check possible.
            project_path=getattr(context, "project_path", None),
        ),
        # Before the FilesystemMiddleware that BUILDS the execute tool, which
        # is only where it has to sit in the list -- the description swap
        # happens per model call, on whatever tools the request carries, so
        # construction order does not reach it. Placed here because a reader
        # following the stack top to bottom meets the three argument-and-
        # prompt repairs together (OPEN-25).
        ExecuteGuardMiddleware(),
        # The gated general-purpose spec below stays passed to
        # create_deep_agent -- it is what suppresses deepagents' ungated one
        # (OPEN-14) -- so `task` is registered for every agent whether the
        # spec wants it or not. This withholds it from the model's view
        # (OPEN-26).
        DelegationGuardMiddleware(can_delegate=spec.can_delegate),
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


def _interrupt_on_for(spec: RudraSubagent, context: Any, tools: list) -> dict | None:
    """The gate's interrupt map, narrowed to tools this subagent holds.

    `build_interrupt_on` returns an entry for every name in MUTATING_TOOLS
    and knows nothing about any spec (permissions/interrupts.py:36-44); the
    whole map used to be handed to every subagent. deepagents'
    HumanInTheLoopMiddleware matches on the tool-call NAME in the assistant
    message, which happens before the tool node discovers the name is not
    registered -- so the coder, which has no `execute`, still raised a full
    approval panel for `execute pwd`, took the user's answer, wrote their
    "Always" grant to the audit log, and only then failed with "execute is
    not a valid tool" (OPEN-15).

    Filtering here rather than in `build_interrupt_on` because the engine's
    map is correct: it describes what the GATE covers. What a given agent
    can call is the spec's business, and this is where the two meet.

    Read from the built tool list rather than from `spec.rudra_tools`, so an
    MCP-holding spec keeps its `call_mcp_tool` entry and one that resolved
    no servers does not.
    """
    if context.gate is None:
        return None
    granted = set(spec.fs_tools) | {tool.name for tool in tools}
    return {name: cfg for name, cfg in context.gate.interrupt_on.items() if name in granted}


_SUPPRESSION_PROMPT = (
    "This subagent is registered to suppress deepagents' ungated "
    "general-purpose auto-add (OPEN-14) and is unreachable: "
    "DelegationGuardMiddleware withholds `task` from the agent that carries "
    "it.\n\nReaching this prompt means delegation was enabled without "
    "re-rendering it -- see subagents/build.py::_nested_subagents."
)
"""What stands in for a rendered prompt no model can reach (OPEN-58).

Self-reporting on purpose. If a model ever reads this, the capability branch
in `_nested_subagents` was made true without the spec being re-rendered, and
the text says where to look rather than leaving a mute stub.
"""


def _nested_subagents(context: Any, *, can_delegate: bool) -> list[dict]:
    """The gated `general-purpose` spec every subagent must carry (OPEN-14).

    `create_deep_agent` auto-adds its OWN `general-purpose` subagent unless
    a spec with that name is supplied (graph.py:745-751), and the one it
    adds is built from a fresh middleware stack: a `FilesystemMiddleware`
    with no `tools=` restriction -- so write_file, edit_file and execute --
    plus summarization and PatchToolCalls (graph.py:752-760). Only
    middleware whose `.name` matches a default slot is inherited from the
    parent (graph.py:776-778), and Rudra's gate reports
    `RudraPermissionMiddleware`, which matches none. So the deny middleware,
    the repeat guard and the param fixer were all absent from it.

    `interrupt_on` IS inherited (graph.py:807-811), which is why `ask` mode
    still prompted and this was not worse than it was. Under `--auto` the
    engine answers `allow`, no prompt is raised, and the deny middleware --
    `permissions.deny`, the `git-dir` and `catastrophic-command` floor, the
    `auto-shell` block from A1.49 -- was simply not in the stack.

    `registry.py:196-199` already documents this mechanism and defends
    against it for the MAIN agent. This is the same defence for the subagent
    path, which never had it: every Rudra subagent holds `task` (its own
    error messages list it) and nothing said where that led.

    **`can_delegate` is the parent's, and it decides whether the prompt is
    rendered at all (OPEN-58).** Passing the spec is the suppression; it is not
    a delegation Rudra wants. `DelegationGuardMiddleware` strips `task` from
    every model call of a spec that does not grant delegation -- which is every
    shipped spec (`spec.py:97`, `test_no_shipped_subagent_may_delegate`) -- and
    deepagents builds `SubAgentMiddleware` with no `system_prompt=`
    (`graph.py:829`), so the name never reaches a prompt either. The rendered
    prompt was therefore unreachable, and it cost a ChromaDB query on every
    coder, tester and reviewer invocation: twelve in run11, and 13,068 of that
    run's 23,519 recall characters -- 56% -- billed to a prompt no model saw.

    **OPEN-54 did not cause that.** Before it closed, `recall_limit` returned
    None on an undeclared window, `recall_block` returned "", and `if block:`
    was false, so the search ran and nothing was recorded. It made this visible
    and made it cost; there is no 2026-08-30 change to this file to look for.

    Keyed on the capability rather than on `GENERAL_PURPOSE`'s name because a
    name check is the special case the next never-invoked spec would not
    inherit (OPEN-17), and because a capability branch is testable on any
    machine (CLAUDE.md 1.8) -- both halves of this one are.
    """
    from rudra.subagents.registry import GENERAL_PURPOSE

    return [to_subagent_spec(GENERAL_PURPOSE, context, render_prompt=can_delegate)]


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
    tools = _tools_for(spec, context)
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=_prompt_for(spec, context, task),
        backend=context.backend,
        checkpointer=context.checkpointer or InMemorySaver(),
        middleware=_middleware_for(spec, context, model),
        interrupt_on=_interrupt_on_for(spec, context, tools),
        skills=_skills_for(spec, context),
        # Not "delegation Rudra wants" -- suppression of delegation Rudra
        # did not choose. See _nested_subagents (OPEN-14).
        subagents=_nested_subagents(context, can_delegate=spec.can_delegate),
    )


def to_subagent_spec(spec: RudraSubagent, context: Any, *, render_prompt: bool = True) -> dict:
    """A deepagents SubAgent dict, for a parent that delegates via `task`.

    Nothing in 9b passes this to create_deep_agent -- the parent that
    should delegate is Step 9c's. It exists now so the delegating path is
    built from the same assembly as the direct one and tested alongside it,
    rather than being written later against a different understanding.

    `model` and `tools` are always set because create_sub_agent raises
    without them (subagents.py:358-363).

    `render_prompt=False` substitutes `_SUPPRESSION_PROMPT` for the rendered
    one and changes nothing else -- same name, description, model, tools,
    middleware and interrupts. It is for a spec supplied only so upstream
    skips its own auto-add, whose prompt no model can reach (OPEN-58). The
    prompt is the only part worth skipping: `_model_for` and `_tools_for`
    make no network call (CLAUDE.md 5), and the spec is compiled EAGERLY for
    every registered subagent whether or not it is ever delegated to
    (subagents.py:451), so its tools are what keep that compiled agent gated.
    """
    _spec_model = _model_for(spec, context.cfg)
    _spec_tools = _tools_for(spec, context)
    return {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": _prompt_for(spec, context) if render_prompt else _SUPPRESSION_PROMPT,
        "model": _spec_model,
        "tools": _spec_tools,
        "middleware": _middleware_for(spec, context, _spec_model),
        "interrupt_on": _interrupt_on_for(spec, context, _spec_tools),
        # Only when the spec wants them: create_sub_agent reads this key
        # (graph.py:676-678), and the delegated path must grant exactly
        # what the direct one does.
        **({"skills": skills} if (skills := _skills_for(spec, context)) else {}),
    }


__all__ = ["_prompt_for", "build_agent", "to_subagent_spec"]
