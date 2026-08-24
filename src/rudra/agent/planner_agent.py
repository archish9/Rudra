"""Planner agent — decides what work the request needs.

Step 9c replaced PLAN.md and current_task.md with the task ledger
(C6.10). The planner declares work through add_tasks and retracts it
through drop_task; it cannot mark anything done, because no tool can --
only loop/engine.py writes that, and only when the gate passes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware
from rich.console import Console

from rudra.config import get_config
from rudra.context.budget import evict_limit, recall_limit
from rudra.context.middleware import UsageMiddleware
from rudra.facts import facts_block
from rudra.filesystem import project_tree
from rudra.llm import build_model
from rudra.loop.tools import create_ledger_tools
from rudra.middleware import (
    FixWriteParamsMiddleware,
    RepeatGuardMiddleware,
    TaskAnchorMiddleware,
)
from rudra.permissions import run_with_approvals
from rudra.tools.interaction_tools import create_interaction_tools
from rudra.trace.stream import StreamState, looks_like_error

_COMMON_HEADER = """You are a senior software architect and planning agent for Rudra.

You never write project code. A separate coder does that.

## WHERE YOU ARE

"/" is the project root. Paths are virtual: "/src/app.py" means that file
inside THIS project, and there is no filesystem outside it. "/home/user",
"/workspace" and "/tmp" are not special -- they resolve inside the project
like any other name, and will simply not exist.

To find out what is here, call ls("/") or read the PROJECT STRUCTURE below.
Never guess a path from what a machine usually looks like.
"""

_CANNOT_FINISH = """
## WHAT YOU CANNOT DO

You cannot mark anything done. A deterministic verification gate decides
that — it parses the code, type checks it, runs the tests, and scans for
placeholders. Do not claim anything is complete.
"""

_CLARIFY_BODY = """
## YOUR STAGE: ESTABLISH THE FACTS

This is the first of three stages. Yours is to settle what the work
depends on, before anyone decides how to build it or what to build.

Record every fact you establish with record_fact(), and say WHY you
believe it. The architect, the coder, the tester and the reviewer all
read those facts; one you keep to yourself is one they do not have.
"""

_CLARIFY_CAN_ASK = """
Ask only what you cannot infer from the request or the codebase. Batch
related questions into a SINGLE ask_user() call — one key per question.
You have {max_questions} questions for this whole run.
"""

_CLARIFY_UNATTENDED = """
This run is unattended: there is nobody to ask. Infer what you need from
the request and the codebase, and record each inference.
"""

_ARCHITECT_BODY = """
## YOUR STAGE: DECIDE HOW IT WILL BE BUILT

The facts above are settled. Yours is to decide the shape of the work,
before it is broken into tasks.

Decide and record, each with record_fact() and a WHY:
  - layout: which file holds what, and why that split
  - boundaries: what each module is responsible for
  - error handling: how failures are surfaced
  - tests: what is worth testing and at which level

Read the existing code first if there is any — a decision that fights the
project it lands in is worse than no decision.

Keep each one short and concrete. "src/parser.rs holds parsing, src/main.rs
holds the CLI" is a decision; three paragraphs about separation of concerns
is not. You cannot ask the user anything at this stage.
"""

_BREAKDOWN_BODY = """
## YOUR STAGE: BREAK IT INTO WORK

The facts above are settled, including the layout. Yours is to turn them
into tasks.

A task is a unit of WORK, described in plain language. Not a filename.

  GOOD: "write a CSV parser that handles quoted commas"
  GOOD: "write tests for the parser"
  GOOD: "add a --format flag to the CLI"
  BAD:  "parser.py"
  BAD:  "create the project structure"

One task may touch several files. Work that needs tests gets its own task.

Call add_tasks() ONCE with every task you can foresee, then STOP. Do not
add a task whose description is "verify" or "check": the gate does that on
its own.

You will be consulted again if a task fails or if the work runs out. When
that happens, add a task taking a DIFFERENT approach, or drop_task() one
that turned out to be unnecessary.
"""


def build_planner_prompt(
    task: str,
    project_path: Path,
    facts: Any = None,
    *,
    stage: str = "breakdown",
    can_ask: bool = True,
    max_questions: int = 5,
    memory: Any = None,
    recall_tokens: int | None = None,
    usage: Any = None,
) -> str:
    """The system prompt for one planning stage (S10b.1).

    Each stage's prompt names only the tools that stage actually has.
    Naming a tool the model cannot call buys a dead call and a confused
    retry -- the same reasoning _tools_for follows when it raises on an
    unknown tool rather than dropping it (subagents/build.py:64-70).
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)

    prompt = f"""{_COMMON_HEADER}
## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    block = facts_block(facts)
    if block:
        prompt += f"\n{block}"

    # C8.4 names "before planning" as a retrieval trigger by name, and the
    # planner is where prior decisions are worth most: it decides what work
    # exists. Beside the facts block, because the two answer the same kind
    # of question -- what is already settled here.
    if memory is not None:
        from rudra.memory.render import recall_block

        recalled = recall_block(memory.search(task, limit=8), recall_tokens)
        if recalled:
            prompt += f"\n{recalled}"
            # Billed here, where the search already happened. The caller
            # used to re-render this block to measure it, on the stated
            # grounds that "the search is already cached by the store's
            # open collection" -- it is not: _open() caches the collection
            # HANDLE, and every search re-runs a ChromaDB/ONNX MiniLM
            # embedding. create_main_agent builds all three stages up
            # front, so a run paid 6 embeddings instead of 3, synchronously
            # (CR-C8).
            if usage is not None:
                usage.record_recall("planner", len(recalled))

    prompt += f"""
## REQUEST
{task}
"""

    if stage == "clarify":
        prompt += _CLARIFY_BODY
        prompt += (
            _CLARIFY_CAN_ASK.format(max_questions=max_questions) if can_ask else _CLARIFY_UNATTENDED
        )
    elif stage == "architect":
        prompt += _ARCHITECT_BODY
    else:
        prompt += _BREAKDOWN_BODY

    prompt += _CANNOT_FINISH
    return prompt


def build_planner_middleware(
    task: str,
    compat_task_anchor: bool = False,
    compat_sandbox_paths: bool = False,
    backend: Any = None,
    evict_tokens: int | None = None,
    usage: Any = None,
) -> list:
    """The planner's middleware stack, with both D4 workarounds gated.

    FixWriteParamsMiddleware is first so it cleans tool args before anything
    else sees them. The planner previously got fence-stripping from
    OverwriteFilesystemBackend, which U.3 deletes — see TODO.md U.14.

    TaskAnchorMiddleware is opt-in per D4 (`[compat] task_anchor`): it was
    built for qwen3:14b losing the task mid-run, and the minimum model is
    now 32B (D6).

    `backend` is what makes the FilesystemMiddleware constructible here, and
    constructing it here is the only way to set the eviction threshold:
    create_deep_agent exposes no parameter for it and builds its own
    instance otherwise (graph.py:820), so before Step 12a the planner took
    the 20 000 default unconditionally (A1.47). Passing our own replaces
    that instance by name rather than duplicating it (graph.py:215-232) --
    asserted in tests/test_deepagents_contract.py.

    No `tools=` argument: it defaults to None, meaning every tool, which is
    exactly what the planner has today. This function changes one number.

    `evict_tokens=None` **omits** the argument rather than passing None,
    because the two differ: the constructor defaults to 20 000, but every
    consumer guards with `if not self._tool_token_limit_before_evict`
    (filesystem.py:2738, :3147, :3464), so an explicit None would switch
    eviction off entirely.

    `backend=None` keeps the old shape for every caller that has no backend
    to give — the compat tests among them.
    """
    middleware: list = [
        FixWriteParamsMiddleware(strip_sandbox_prefixes=compat_sandbox_paths),
        # After the param fixer, so a repaired path is judged as the call it
        # became rather than as the one the model mistyped. The planner is
        # where OPEN-10 was measured: four identical failing read_file calls
        # in a row, and nothing to stop a fifth.
        RepeatGuardMiddleware(),
    ]
    if backend is not None:
        evict = {} if evict_tokens is None else {"tool_token_limit_before_evict": evict_tokens}
        # Read-only, explicitly. `tools=None` means EVERY filesystem tool --
        # measured against the installed deepagents: delete, edit_file,
        # execute, glob, grep, ls, read_file, write_file -- on the full
        # CompositeBackend(default=LocalShellBackend), so `execute` is
        # functional, not the inert stub. Under --auto the mode default is
        # `allow`, so all three planner stages could write into the user's
        # project BEFORE the approval gate ran, contradicting plan()'s
        # "touches nothing" docstring and _tools_for_stage's "absence is the
        # enforcement". The planner prompt only ever asks it to read
        # (CR-C2).
        middleware.append(FilesystemMiddleware(backend=backend, tools=PLANNER_FS_TOOLS, **evict))
    if usage is not None:
        middleware.append(UsageMiddleware("planner", usage))
    if compat_task_anchor:
        middleware.append(TaskAnchorMiddleware(task))
    return middleware


# The three stages, in the order run_loop runs them: C6.7's clarify ->
# architect -> task breakdown.
STAGES: tuple[str, ...] = ("clarify", "architect", "breakdown")


def _tools_for_stage(
    stage: str,
    *,
    ledger: Any,
    facts: Any,
    paths: Any,
    console: Console,
    cfg: Any,
    interactive: bool,
) -> list:
    """The tools one stage may call, and no others (S10b.1).

    Absence is the enforcement. A prompt telling the model to clarify
    before planning is a hint -- Step 7's acceptance run watched a model
    denied on write_file reach for `echo > /abs/path` instead -- so the
    breakdown stage simply has no way to ask a question, and the clarify
    stage no way to declare work.

    The single assembly point, for the reason subagents/build.py is one:
    two paths that decide a tool list will eventually disagree.
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)

    if stage == "breakdown":
        # No record_fact and no ask_user: by now the facts are settled,
        # and re-opening them mid-plan is what C6.8 exists to prevent.
        return create_ledger_tools(ledger, paths.ledger_json)

    interaction = create_interaction_tools(
        console,
        facts,
        paths.facts_json,
        max_questions=cfg.agent.max_questions,
        interactive=interactive and stage == "clarify",
    )
    # The architect reasons; it does not interrogate. Filtering rather
    # than calling a second factory keeps one construction path, so the
    # question budget cannot fork.
    if stage == "architect":
        return [tool for tool in interaction if tool.name != "ask_user"]
    return interaction


BOOTSTRAP_SKILL = "using-superpowers"


def bootstrap_text(cache_root: Path | None) -> str | None:
    """The rendered bootstrap skill, or None when it is not there.

    Read from the *cache*, never from the package: the rendered copy has
    its cross-references rewritten to readable paths and its Platform
    Adaptation list pointing at references/rudra-tools.md. A packaged copy
    would send the model after a plugin namespace Rudra does not have.

    Skills are inert without this (C5.3). The index gives the model nine
    names and descriptions; this is what tells it to act on them.

    Planner stages only. `using-superpowers/SKILL.md:5-7` opens with
    <SUBAGENT-STOP> -- "if you were dispatched as a subagent to execute a
    specific task, ignore this skill" -- so injecting it into the coder
    would spend ~780 tokens telling it to disregard what it just read.
    """
    if cache_root is None:
        return None
    path = cache_root / "library" / "superpowers" / BOOTSTRAP_SKILL / "SKILL.md"
    try:
        return _strip_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _strip_frontmatter(text: str) -> str:
    """Drop the leading YAML block a SKILL.md carries.

    Frontmatter is metadata for the skill loader -- `name` and
    `description` are what SkillsMiddleware puts in the index. Injecting it
    verbatim would drop a YAML document into the middle of a system prompt
    and tell the model its own instructions are "Use when starting any
    conversation", which is addressed to the index reader, not to it.
    """
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) == 3 else text


# What the planner may do to the filesystem: look, never touch. Named here
# rather than inlined so a reader of the module sees the boundary without
# reading the middleware wiring (CR-C2).
# Typed with deepagents' full literal set rather than the four names, because
# `list` is invariant: a list[Literal["ls", ...4 names]] is not a
# list[Literal[...8 names]] as far as the type checker is concerned.
PLANNER_FS_TOOLS: list[
    Literal["ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"]
] = [
    "ls",
    "read_file",
    "glob",
    "grep",
]


def create_planner_agent(
    task: str,
    project_path: Path,
    filesystem_backend,
    checkpointer,
    console: Console,
    gate=None,
    ledger=None,
    paths=None,
    facts=None,
    interactive: bool = True,
    stage: str = "breakdown",
    skills_sources: tuple[str, ...] | None = None,
    skills_cache_root: Path | None = None,
    usage: Any = None,
    memory: Any = None,
):
    """Create one stage of the planner (S10b.1).

    `ledger` and `facts` must be the SAME objects the loop reads: the
    stages mutate them in place, and a copy would leave the engine with no
    tasks and the coder with no facts. They are also the only channel
    between stages -- each stage gets its own thread, so nothing carries
    over except what was recorded.

    `interactive` False means nobody can answer, so ask_user is never
    registered (S10a.5). The prompt is built to match, so the model is
    never told to call a tool it does not have.

    `stage` defaults to "breakdown" because that is what every pre-10b
    caller expected: declare the work.
    """
    from rudra.facts import FactStore
    from rudra.loop.ledger import Ledger
    from rudra.state.paths import rudra_paths

    model = build_model("planner")
    cfg = get_config()
    ledger = ledger if ledger is not None else Ledger()
    facts = facts if facts is not None else FactStore()
    paths = paths if paths is not None else rudra_paths(project_path)

    # The ledger replaced PLAN.md and current_task.md (C6.10); the fact
    # store replaced project.json's four fields (C6.8a). git and testing
    # tools are gone from the planner: the loop runs the gate itself, and
    # the tester subagent writes tests (S9c.5). Which of the rest this
    # stage sees is _tools_for_stage's decision, and only its (S10b.1).
    custom_tools = _tools_for_stage(
        stage,
        ledger=ledger,
        facts=facts,
        paths=paths,
        console=console,
        cfg=cfg,
        interactive=interactive,
    )

    middleware = build_planner_middleware(
        task,
        compat_task_anchor=cfg.compat.task_anchor,
        compat_sandbox_paths=cfg.compat.sandbox_paths,
        backend=filesystem_backend,
        evict_tokens=evict_limit(cfg, "planner"),
        usage=usage,
    )
    if gate is not None:
        # First in the list: a denied call must be stopped before any other
        # middleware rewrites its arguments.
        middleware.insert(0, gate.middleware)

    # permissions= is deliberately absent. It raises NotImplementedError on
    # any execute-capable backend, which is every backend Rudra now builds.
    # See TODO.md U.7.
    system_prompt = build_planner_prompt(
        task,
        project_path,
        facts,
        # A1.78: omitted until 2026-08-17, so every stage silently took the
        # default and ran on the breakdown prompt. Tools stayed correctly
        # scoped, which is what hid it -- the clarify stage was *told* to
        # call add_tasks while holding only record_fact and ask_user.
        stage=stage,
        can_ask=interactive and stage == "clarify" and cfg.agent.max_questions > 0,
        max_questions=cfg.agent.max_questions,
        memory=memory,
        recall_tokens=recall_limit(cfg, "planner"),
        usage=usage,
    )
    # C5.3: the index alone is inert. This is the instruction block that
    # makes the model reach for a skill, and it chains onward -- it tells
    # the model to read its harness's reference file, which is Rudra's
    # tool mapping, which explains that no tool can mark work done.
    bootstrap = bootstrap_text(skills_cache_root)
    if bootstrap:
        system_prompt = f"{system_prompt}\n\n---\n\n{bootstrap}"

    return create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        backend=filesystem_backend,
        checkpointer=checkpointer,
        memory=[".rudra/AGENTS.md"],
        middleware=middleware,
        interrupt_on=gate.interrupt_on if gate is not None else None,
        # None, not [], when skills are off: an empty list still installs
        # SkillsMiddleware and spends its ~464 tokens of boilerplate on an
        # index with nothing in it.
        skills=list(skills_sources) if skills_sources else None,
    )


async def _stream_planner_turn(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    gate: Any,
    console: Console,
    trace: Any = None,
) -> bool:
    """Stream one planner turn. Returns False if a guard halted it.

    Lifted from RudraAgent._stream_planner in Step 9c: the class's loop
    is deleted and this is its only remaining caller. The guard logic is
    unchanged.

    A1.20's remaining half -- one `processed` counter across namespaces --
    is closed here in Step 15a: positions are keyed by namespace, exactly
    as subagents/runner.py now does it. Printing moved to the shared
    renderer at the same time, so the planner and the subagents cannot
    drift apart again the way their error-marker lists had.

    `trace` is optional because consult_planner's callers built no sink
    before Step 15a and its tests still do not.
    """
    lg_config = {"configurable": {"thread_id": thread_id}}
    # One counter per subgraph namespace, not one for the turn (A1.20).
    seen: dict[tuple[str, ...], int] = {}
    state = StreamState(role="planner")
    consecutive_failures = 0
    planning_tool_calls: dict[str, int] = {}
    MAX_PLANNING_CALLS = 4
    _halt = False

    # run_with_approvals yields exactly what astream yields, so the parse
    # loop below is unchanged. It reads interrupts from get_state after
    # the stream drains, because __interrupt__ never appears in "values"
    # chunks and changing stream_mode would change the chunk shape this
    # loop depends on.
    async for chunk in run_with_approvals(
        agent,
        {"messages": [{"role": "user", "content": message}]},
        lg_config,
        gate,
        console,
        trace=trace,
        stream_tokens=get_config().agent.stream_tokens,
        role="planner",
    ):
        if _halt:
            break

        namespace, event = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
        msgs = event.get("messages", [])

        if trace is not None:
            # Before the guards read it, and before any `break`: an event
            # the guard stops on is exactly the one worth seeing. The
            # namespace rides on every event now, so the old
            # "(planner subagent ...)" line is gone.
            trace.feed(chunk, state)

        where = tuple(namespace or ())
        processed = seen.get(where, 0)
        while processed < len(msgs):
            msg = msgs[processed]
            msg_type = type(msg).__name__

            if msg_type == "AIMessage":
                for tc in getattr(msg, "tool_calls", []):
                    name = tc.get("name", "")
                    if name in ("write_file",):
                        planning_tool_calls.clear()
                    elif name in ("add_tasks", "read_ledger"):
                        planning_tool_calls[name] = planning_tool_calls.get(name, 0) + 1
                        if planning_tool_calls[name] >= MAX_PLANNING_CALLS:
                            console.print(
                                f"[bold yellow]!! Planner loop guard: '{name}' called "
                                f"{planning_tool_calls[name]}x — stopping.[/bold yellow]"
                            )
                            _halt = True
                            break
                if _halt:
                    break

            elif msg_type == "ToolMessage":
                # The shared list, not a fourth private copy. This guard's
                # own four markers could not see "BLOCKED:", so three
                # consecutive denials never tripped it.
                if looks_like_error(str(getattr(msg, "content", ""))):
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        console.print(
                            "[bold yellow]!! 3 consecutive planner failures — stopping.[/bold yellow]"
                        )
                        _halt = True
                else:
                    consecutive_failures = 0

            if _halt:
                break
            processed += 1
        seen[where] = processed

    return not _halt


_STAGE_MESSAGES = {
    "clarify": (
        "Establish what this work depends on, and record each fact with "
        "why you believe it. The request is:\n\n{request}"
    ),
    "architect": (
        "Decide how this will be built — layout, boundaries, error "
        "handling, tests — and record each decision with its reason. The "
        "request is:\n\n{request}"
    ),
    "breakdown": "Break this request into tasks: {request}",
}


async def consult_planner(
    agent: Any,
    ledger: Any,
    request: str,
    *,
    stage: str,
    reason: str = "initial",
    task: Any = None,
    feedback: str = "",
    gate: Any,
    console: Console,
    session_id: str,
    trace: Any = None,
) -> None:
    """Ask one planning stage to do its job. It mutates state via tools.

    Three stages run once each, in order, before any coder runs. Only
    `breakdown` is ever re-entered -- on a blocked task, an empty ledger,
    or a user revising the plan (S10b.3, C6.9). Clarify and architect are
    not: re-opening the questions after code exists spends a model call
    churning decisions the coder has already built on, and after Step 10c
    it would re-litigate facts the user has already approved.

    A revision carries the user's words **verbatim**, for the reason the
    gate's blocker goes back unparaphrased: a summary of an instruction is
    a worse instruction.

    Each stage has its own thread, so the only thing that carries between
    them is what was recorded -- which is the point (S10b.1).
    """
    if stage not in STAGES:
        known = ", ".join(STAGES)
        msg = f"unknown planner stage {stage!r}; valid stages: {known}"
        raise ValueError(msg)
    if reason != "initial" and stage != "breakdown":
        msg = f"stage {stage!r} runs once and cannot be re-entered (reason {reason!r})"
        raise ValueError(msg)

    if reason == "initial":
        message = _STAGE_MESSAGES[stage].format(request=request)
    elif reason == "ledger_empty":
        message = (
            "Every task is finished. Is anything missing before we stop? "
            "Call add_tasks if so; otherwise reply DONE and stop."
        )
    elif reason == "blocked" and task is not None:
        message = (
            f"Task {task.id} ({task.description}) failed and was given up on:\n\n"
            f"{task.note}\n\n"
            "Add a task taking a DIFFERENT approach, or drop_task it if it is "
            "not worth doing. If neither, reply DONE and stop."
        )
    elif reason == "revision":
        if not feedback.strip():
            msg = "a revision needs the user's feedback; got an empty string"
            raise ValueError(msg)
        message = (
            "The user reviewed your plan and asked for this change:\n\n"
            f"{feedback.strip()}\n\n"
            "Adjust the task list with add_tasks and drop_task to match. "
            "Change only what they asked about; leave the rest alone."
        )
    else:  # pragma: no cover - guarded by the caller
        raise ValueError(f"unknown consult reason {reason!r}")

    await _stream_planner_turn(
        agent,
        message,
        thread_id=f"{session_id}-{stage}",
        gate=gate,
        console=console,
        trace=trace,
    )
