"""Planner agent — decides what work the request needs.

Step 9c replaced PLAN.md and current_task.md with the task ledger
(C6.10). The planner declares work through add_tasks and retracts it
through drop_task; it cannot mark anything done, because no tool can --
only loop/engine.py writes that, and only when the gate passes.

The three stage prompts below carry methodology adapted from the
`brainstorming` skill of superpowers (https://github.com/obra/superpowers),
MIT, Copyright (c) 2025 Jesse Vincent -- its scope assessment and question
discipline in _CLARIFY_BODY, its approach-weighing, YAGNI and isolation
guidance in _ARCHITECT_BODY, and its spec self-review in _BREAKDOWN_BODY,
retargeted from a design document to the task ledger.

It is inlined here rather than read at runtime, and that is OPEN-17's fix:
the corpus is written for one agent that clarifies, designs and implements
in a single conversation, so a planner *stage* that reads it tries to run
all four of its steps holding the tools for one. Inlined, the methodology
applies to every planning run on every model instead of when a model
chooses to open a file. The planner therefore indexes no skills at all
(see create_planner_agent); the coder and tester still index the full
corpus, being single agents doing one job.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Literal

from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware
from rich.console import Console
from rich.markup import escape

from rudra.config import get_config
from rudra.context.budget import evict_limit, recall_limit
from rudra.context.middleware import UsageMiddleware
from rudra.facts import facts_block
from rudra.filesystem import project_tree
from rudra.llm import build_model
from rudra.loop.tools import create_ledger_tools, render_ledger
from rudra.middleware import (
    PLANNER_MEMORY_SOURCES,
    DelegationGuardMiddleware,
    FixWriteParamsMiddleware,
    MachinePathMiddleware,
    ModelRetryMiddleware,
    PlannerWriteMiddleware,
    RepeatGuardMiddleware,
    TaskAnchorMiddleware,
    build_memory_middleware,
)
from rudra.permissions import run_with_approvals
from rudra.tools.interaction_tools import create_interaction_tools
from rudra.trace.stream import StreamState, is_rudra_refusal, message_is_error

logger = logging.getLogger(__name__)

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

**Look before you ask.** Read what is already here first. A question whose
answer is in the codebase spends the budget and tells the user you did not
look.

**Settle the size of the request first.** Is this a change to code that
already exists here, or something new? A change to an existing flow needs
its own file read, not a design conversation. Something new needs purpose
and constraints settled before anyone picks a shape.

**If the request is several independent pieces at once** -- a system with
storage AND accounts AND billing AND reporting -- say so and record it.
Do not spend your questions refining the details of something that needs
splitting first; record which piece comes first and why.

**What is worth a fact:** the purpose (what the user is actually trying to
do, which is rarely the literal words), the constraints (versions,
libraries, platforms, things they cannot use), and what "working" means
here -- how you would know this succeeded. Those three carry the run.
Preferences nobody stated are not facts.
"""

_CLARIFY_CAN_ASK = """
Ask only what you cannot infer from the request or the codebase. Batch
related questions into a SINGLE ask_user() call; the user is shown them
one at a time. You have {max_questions} questions for this whole run.

When a question has a set of likely answers, put them in that question's
`options` — with a short `description` each — and set `multi_select` when
more than one may apply. NEVER write the choices into your reply instead:
the user cannot select prose, so a question you only narrate is one that
nobody was ever asked.
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
project it lands in is worse than no decision. Follow the patterns that are
already there. Where existing code has a problem that gets in the way of
this work, fix that as part of it; do not propose refactoring that serves
something else.

**Weigh 2-3 approaches before you record one.** Record the one you chose
and why the others lost -- that reasoning is what stops the coder relitigating
it, and what stops a later run repeating a dead end. One approach considered
is not a decision, it is the first thing you thought of.

**Cut ruthlessly.** Remove anything nobody asked for. Every feature you add
here is one the coder must write, the tester must cover and the gate must
pass. If it is not needed for what the user asked, it is not in the design.

**Split it so each piece can be understood alone.** For every unit you name,
you should be able to say what it does, how it is used, and what it depends
on. If you cannot describe a piece without describing its internals, the
boundary is wrong. A file that would hold several unrelated jobs is doing
too much -- say where it splits.

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
  BAD:  "understand the requirements through clarifying questions"
  BAD:  "propose 2-3 architectural approaches with trade-offs"
  BAD:  "present the detailed design for approval"

The last three are a planning workflow, not work -- they are what the two
stages before you already did, and what you are doing now. They are quoted
from a real run that produced no code: the coder handed the first of them
had no file to write and spent the run searching for directories that do
not exist. If a task does not change a file, it is not a task.

One task may touch several files. But if two tasks would write to the SAME
file, they are ONE task. A coder is given one task and writes every file
that task needs, so the first of them writes the whole file and the rest
are dispatched at a finished file and change nothing. When the layout says
this project is a single file, that file is ONE task -- not one task per
section, per stylesheet or per script. add_tasks will refuse a list that
splits one file up, and say so.

Work that needs tests gets its own task.

Call add_tasks() ONCE with every task you can foresee, then STOP. Do not
add a task whose description is "verify" or "check": the gate does that on
its own.

**Re-read your list before you send it.** Is any task vague enough that two
people would build different things? Do any two contradict each other? Does
any one of them hide three tasks inside it? Is anything on it a placeholder
rather than work? Fix those before calling add_tasks -- the coder gets what
you wrote, not what you meant.

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
    trace: Any = None,
    project_path: Any = None,
    stage_tools: tuple[str, ...] = (),
) -> list:
    """The planner's middleware stack, with both D4 workarounds gated.

    `stage_tools` names what this stage can do INSTEAD of writing, and is the
    only argument here that differs between the three stages. It reaches
    PlannerWriteMiddleware, whose refusal must name a tool the stage actually
    holds -- `_tools_for_stage` gives breakdown no `record_fact`, so a fixed
    sentence naming one would advertise an absent tool (OPEN-15). Empty is
    safe: the refusal then declines to name any tool rather than naming a
    wrong one.

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

    `trace=None` has the same default and the same reason as `usage=None`
    (OPEN-45): compat tests construct this with neither, and a run that
    built no sink must still get its retries. Both go to
    ModelRetryMiddleware, which before OPEN-45 was registered here holding
    nothing to report to.
    """
    middleware: list = [
        FixWriteParamsMiddleware(strip_sandbox_prefixes=compat_sandbox_paths),
        # OPEN-100 option C. `PLANNER_FS_TOOLS` grants no write tool, which
        # is correct and is not the defect -- the defect was that upstream
        # answered the attempt with a tool-list echo, and run d8f742805b9b's
        # planner spent 430.9 s (42% of its model time) generating complete
        # HTML documents for it anyway. A refusal that carries the route is
        # what stops that; option A's bounds only stop it running for ever.
        #
        # After FixWriteParamsMiddleware so the path argument has already
        # been aliased -- `filename`/`path` -> `file_path` -- and the refusal
        # names the file the model meant.
        PlannerWriteMiddleware(
            stage_tools=stage_tools,
            usage=usage,
            trace=trace,
        ),
        # OPEN-41, and the planner is where it was measured: all three of
        # 2026-08-27's dead runs died in a planner stage, on a provider 500
        # that `_stream_with_retry` had already stopped guarding because the
        # stage had emitted a chunk.
        #
        # Not at the front: U.14 keeps FixWriteParamsMiddleware there, and
        # this one has no claim on the seat -- it implements the model-call
        # hooks only and never sees a tool argument. What its position DOES
        # decide is that it sits ahead of the UsageMiddleware appended
        # below, so each attempt is one recorded call with its own duration
        # and the backoff sleep is charged to nobody. Inside the
        # accounting, a twice-retried call would read as one 40-second call
        # that was mostly `asyncio.sleep` -- the number OPEN-40 is about.
        # `trace` and `usage` are what make it audible (OPEN-45). It was
        # registered correctly and reported nothing, so a planner absorbing
        # a third of its requests looked exactly like a slow model -- and
        # those have opposite fixes.
        ModelRetryMiddleware("planner", trace=trace, usage=usage),
        # After the param fixer, so a repaired path is judged as the call it
        # became rather than as the one the model mistyped. The planner is
        # where OPEN-10 was measured: four identical failing read_file calls
        # in a row, and nothing to stop a fifth.
        # `trace` for the reason ModelRetryMiddleware above has one
        # (OPEN-57): without it a refusal is recorded as something the USER
        # said, and nothing anywhere says the guard fired.
        # `project_path` is OPEN-62 6a: the write-belief lives on `usage`
        # for the run, and the planner needs it for the reason a subagent
        # does -- it builds a fresh agent PER STAGE, so its own three
        # stages already cross the boundary this fixes. A belief inherited
        # rather than made is confirmed against the file before it refuses.
        # OPEN-91, and it is registered here for the reason ModelRetry and
        # RepeatGuard are: the planner holds `glob`, `grep`, `ls` and
        # `read_file` (PLANNER_FS_TOOLS below) and no `execute`, which is
        # exactly the tool set that produced run fc543fb2b82f's 36-glob hunt
        # in the coder. Outside the repeat guard, so a hunt whose repeats the
        # guard is deduping still gets the explanation.
        MachinePathMiddleware(
            "planner",
            project_path=project_path,
            has_shell=False,
            trace=trace,
        ),
        RepeatGuardMiddleware(role="planner", usage=usage, trace=trace, project_path=project_path),
        # OPEN-37, and it is OPEN-26 one agent up. `create_deep_agent` below
        # passes no `subagents=`, so deepagents auto-adds its own
        # general-purpose spec (graph.py:750-751 -- the auto-add is skipped
        # only when a supplied spec is literally NAMED `general-purpose`),
        # and any spec at all registers `task` (graph.py:827-828). The
        # auto-added child inherits the PARENT's tools and NOT the parent's
        # middleware, so for the planner that is an ungated agent holding
        # `add_tasks`, `drop_task` and `read_ledger`.
        #
        # Run6 measured what it does with them: the breakdown stage
        # delegated "Run the test suite to see what's actually failing" to a
        # child with no shell, which spent ~30 read_file calls re-reading the
        # four files the planner had just read, failed three `bash` calls,
        # and contributed nothing.
        #
        # Unconditional, and not a `can_delegate` argument: no planner stage
        # may delegate, so there is no configuration where this is True. The
        # alternative -- passing Rudra's own GENERAL_PURPOSE spec to suppress
        # the auto-add -- leaves `task` reachable and only changes who
        # answers, so the turns are still spent. Absence is the enforcement
        # (U.17), as it is for _tools_for_stage and for `skills=None` below.
        DelegationGuardMiddleware(can_delegate=False),
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
        # The same seam, one middleware over (OPEN-70). create_deep_agent
        # builds a MemoryMiddleware from `memory=` carrying upstream's
        # MEMORY_SYSTEM_PROMPT, which spends ~1,268 tokens per call telling
        # this agent to persist what it learns by calling `edit_file` -- a
        # tool PLANNER_FS_TOOLS does not grant it. Passing our own replaces
        # that instance by name and drops the order; see
        # middleware/memory_prompt.py for why the fix could not be a line in
        # AGENTS.md, which is what OPEN-66 tried.
        middleware.append(build_memory_middleware(backend))
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
    trace: Any = None,
    usage: Any = None,
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
        # `facts` reaches the ledger tools for one question only: does
        # anything the earlier stages settled say the deliverable is a
        # single file (OPEN-90)? add_tasks reads the VALUES, never the key
        # names -- the store is deliberately open.
        return create_ledger_tools(ledger, paths.ledger_json, facts=facts, trace=trace, usage=usage)

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
    usage: Any = None,
    memory: Any = None,
    trace: Any = None,
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

    `trace` is the run's TraceSink, and it reaches the middleware stack
    only -- `consult_planner` takes its own, for streaming the turn. Two
    parameters for one object because the two are used at different times:
    this one is frozen into the compiled graph at construction, and that
    one is passed per turn. Both are the SAME sink in a real run
    (agent/main_agent.py), which is what keeps one run to one record.
    Added by OPEN-45, so ModelRetryMiddleware has somewhere to report.
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
        trace=trace,
        usage=usage,
    )

    middleware = build_planner_middleware(
        task,
        compat_task_anchor=cfg.compat.task_anchor,
        compat_sandbox_paths=cfg.compat.sandbox_paths,
        backend=filesystem_backend,
        evict_tokens=evict_limit(cfg, "planner"),
        usage=usage,
        trace=trace,
        project_path=project_path,
        # What this stage may do instead of writing, read off the tools it
        # was actually built with rather than restated -- two lists that name
        # a stage's tools would eventually disagree, which is the whole of
        # _tools_for_stage's own argument.
        stage_tools=tuple(getattr(tool, "name", "") for tool in custom_tools),
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
    return create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        backend=filesystem_backend,
        checkpointer=checkpointer,
        # One spelling, shared with the middleware built above: they must
        # name the same file. Still passed, and must be -- it is what makes
        # upstream build the instance ours replaces, and without it the
        # memory would load into state and never reach the prompt.
        memory=list(PLANNER_MEMORY_SOURCES),
        middleware=middleware,
        interrupt_on=gate.interrupt_on if gate is not None else None,
        # OPEN-17: the planner indexes NO skills, and that is the fix.
        #
        # The corpus is written for one agent that clarifies, designs and
        # implements in a single conversation. Rudra plans with three agents
        # holding disjoint tools, so a stage that reads `brainstorming` tries
        # to run all four of its steps and has tools for one. Measured four
        # times on nvidia/nemotron-3-ultra-550b-a55b, same prompt each time:
        # with the corpus indexed the planner emitted the skill's own
        # checklist as the task list ("Write design doc to
        # docs/superpowers/specs/", "Invoke writing-plans skill"), or no
        # tasks at all; with it absent, seven units of real work. Three
        # escalating attempts to fix this in prompt text all lost to the
        # injected bootstrap's "YOU DO NOT HAVE A CHOICE. YOU MUST USE IT."
        #
        # Nothing is lost: the methodology those skills carry is merged into
        # the three stage prompts above, so it applies to every planning run
        # by default rather than when a model chooses to read a file. The
        # coder and tester still index the full corpus (registry.py) -- they
        # are single agents doing one job, which is what it was written for.
        #
        # Absence is the enforcement, as it is for tools (_tools_for_stage)
        # and for the reviewer's write tools (subagents/build.py).
        skills=None,
    )


PLANNER_HALT_NOTICE = "planner-guard"
"""The `name` on the NOTICE a halted planner stage emits.

Deliberately NOT `"guard"`, which `subagents/runner.py` already owns: a
subagent halt and a planner-stage halt are different events with different
remedies, and a maintainer filtering `debug-<id>.jsonl` must be able to ask
for one without the other. One spelling, as a module constant, because a
second is a second answer to "why is my filter empty" (`CLAUDE.md` §8a).
"""

MAX_PLANNING_CALLS = 4
"""How many times ONE planning tool may be called in a single stage.

Catches re-planning in a loop: a breakdown stage that calls `add_tasks` four
times is rewriting its own plan rather than declaring work. Narrow on
purpose, and kept beside the total below rather than replaced by it -- four
`add_tasks` calls is a defect at call four, long before forty of anything.
"""

MAX_PLANNER_TOOL_CALLS = 40
"""Tool calls ONE planner stage may make, of any kind (OPEN-100).

`subagents/runner.py::MAX_TOTAL_CALLS` is the precedent and its reasoning
carries over unchanged: the other guards catch *failing* and catch
*repeating*, and neither catches spending. Run `d8f742805b9b`'s architect
stage made 18+ calls of which the old guard could count **zero** -- it
watched `add_tasks` and `read_ledger`, and the stage called neither.

40 rather than 80, chosen against observed work and not guessed. Measured
per stage across every archived run, counting `tool_call` records between
the planner `user` records (stages 1-2 only, since a breakdown stage's tail
is contaminated by the subagent calls that share the debug log):

    5775ba1f9855   16, 17          e8aba9aac469    9
    2cde3406f7d6   22, 12          53c00fbdd0cd    9, 10
    d8f742805b9b   14, 20   <- the reported run

**Read that table before believing this bound fixed the reported run: it did
not.** A healthy stage runs to 9-22 calls and the runaway one reached 20, so
*the call counts do not separate them* -- 40 is a runaway ceiling and nothing
more, which is exactly what `MAX_TOTAL_CALLS` is and all the planner had none
of. At the moment the user pressed Ctrl-C that stage stood at 20 of 40 and
was still going; 20 is therefore a lower bound on what it would have spent,
not the length of the stage. What actually addresses this run's pathology is
`PlannerWriteMiddleware` (option C), which stops the generating rather than
the spending.

It is a runaway bound, not a budget -- if real planning work ever approaches
it, raise it rather than teaching people to expect halts.

**Every name counts, `write_file` included.** The line this replaces read
`if name in ("write_file",): planning_tool_calls.clear()`, so the one tool
name that can never be legitimate on a stack whose `PLANNER_FS_TOOLS`
excludes it was the one name that disarmed the guard.
"""


def _stage_time_limit() -> float:
    """Seconds one planner stage may spend, or 0.0 for no bound (OPEN-100).

    Reads `[agent] max_invocation_seconds` -- the SAME key
    `subagents/runner.py::_invocation_limit` reads, deliberately, so a user
    who lowers the bound lowers it everywhere rather than discovering that
    the agent which runs first is the one it never covered.

    A bound in seconds and not only in calls, for OPEN-91's measured reason:
    run `d8f742805b9b` averaged 28.7 s per planner call and spent 140.6 s,
    151.9 s and 138.4 s on three of them, at which rate a 40-call ceiling is
    nineteen minutes. Read as defensively as its sibling: an unreadable value
    falls back to the constant, because the degraded mode of a guard is the
    guard and not its absence.

    **The default is not calibrated to a planner stage and this is known.**
    Per-stage model seconds across the archive:

        healthy    67.2  78.8  82.9  92.7  96.8  132.6  138.7  151.4  249.7  313.8
        reported   476.0 (clarify)   556.0 (architect)

    So 1200 did not fire on the reported run either -- the user pressed
    Ctrl-C at 1092 s of a run whose architect stage stood at 556 s of a
    per-stage 1200 s allowance, three stages deep. **A bound the user beats
    is not yet a bound**, which is `CLAUDE.md` §5a's rule about numbers
    compared against a human's experience. Tightening it is deliberately NOT
    done here: the healthy maximum is 313.8 s on one provider, so any number
    that catches 476 s is within 1.5x of legitimate work, and picking it from
    a single run is what `TODO.md`'s lesson about tuning to one data point
    warns against. It needs healthy-run evidence on a second provider first,
    and is recorded as this item's follow-up.
    """
    from rudra.subagents.runner import MAX_INVOCATION_SECONDS

    try:
        agent_cfg = getattr(get_config(), "agent", None)
    except Exception:  # noqa: BLE001 - a guard must not need a readable config
        return MAX_INVOCATION_SECONDS
    raw = getattr(agent_cfg, "max_invocation_seconds", MAX_INVOCATION_SECONDS)
    try:
        limit = float(raw)
    except (TypeError, ValueError):
        return MAX_INVOCATION_SECONDS
    return limit if limit > 0 else 0.0


def _announce_halt(reason: str, *, console: Console, trace: Any, usage: Any) -> None:
    """Say, once, that Rudra stopped a planner stage (`TODO.md` lesson 5).

    THE one seam all three bounds are announced at, for the reason
    `subagents/runner.py` funnels its own three through one `announce`: a
    guard added later must not be able to arrive without its diagnostic, and
    three call sites is three chances to forget one.

    `_stream_planner_turn`'s return value is discarded by its only caller
    (`consult_planner`), so before this a halted stage and a completed stage
    were indistinguishable to everything downstream -- nothing in
    `usage.json`, nothing in the ledger, no NOTICE. That is `CLAUDE.md` §8a
    failure shape 1 exactly: the number was known at the moment it mattered
    and never reached disk.

    Both records swallow their own failure. A run that did its work must not
    be reported failed because a log line could not be written.
    """
    console.print(f"[bold yellow]!! {escape(reason)}[/bold yellow]")
    try:
        if usage is not None:
            usage.record_planner_halt("planner")
    except Exception:  # noqa: BLE001 - bookkeeping may never end a run
        logger.debug("planner halt not counted", exc_info=True)
    try:
        if trace is not None:
            trace.notice(reason, role="planner", name=PLANNER_HALT_NOTICE)
    except Exception:  # noqa: BLE001 - same rule
        logger.debug("planner halt not announced", exc_info=True)


async def _stream_planner_turn(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    gate: Any,
    console: Console,
    trace: Any = None,
    usage: Any = None,
) -> bool:
    """Stream one planner turn. Returns False if a guard halted it.

    Four bounds, and OPEN-100 added the two that matter most: a ceiling on
    tool calls of ANY name, and one on seconds. The two that were already
    here -- one planning tool called `MAX_PLANNING_CALLS` times, and three
    consecutive tool failures -- are correct for the shapes they target and
    could see nothing of run `d8f742805b9b`, which spent 42% of its model
    time generating HTML documents for a `write_file` tool the planner does
    not have and was ended by a human pressing Ctrl-C. Every halt goes
    through `_announce_halt`, because the return value below is discarded by
    `consult_planner` and a silent guard is `CLAUDE.md` §8a failure shape 1.

    `usage` is optional and is not the same object as `trace`: bookkeeping
    may never end a run, so both are read defensively and neither is
    required for a bound to fire.

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
    total_calls = 0
    started = time.monotonic()
    time_limit = _stage_time_limit()
    _halt = False

    def halt(reason: str) -> None:
        nonlocal _halt
        _announce_halt(reason, console=console, trace=trace, usage=usage)
        _halt = True

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

        # Before the chunk is parsed, so a stage that is spending without
        # producing tool calls is caught too -- which is this item's own
        # shape: three of run `d8f742805b9b`'s model calls emitted 4932,
        # 5540 and 4636 output tokens of HTML across 430.9 s, and only one
        # of the three reached a tool at all. Each bound names itself, for
        # OPEN-91's reason: "40 tool calls" is a loop and "over the 1200s
        # limit" is a slow provider or a loop, and a reader of
        # `debug-<id>.jsonl` has to be able to tell them apart.
        elapsed = time.monotonic() - started
        if time_limit and elapsed >= time_limit:
            halt(
                f"{elapsed:.0f}s in one planner stage, over the {time_limit:.0f}s "
                f"limit -- stopping after {total_calls} tool calls."
            )
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
                    # EVERY name, and `write_file` above all (OPEN-100). This
                    # branch used to read `if name in ("write_file",):
                    # planning_tool_calls.clear()`, so the one call the
                    # planner can never legitimately make was the one that
                    # disarmed the guard -- and the two names it did count
                    # were both absent from the stage that ran away.
                    total_calls += 1
                    if total_calls >= MAX_PLANNER_TOOL_CALLS:
                        halt(
                            f"{total_calls} tool calls in one planner stage, over "
                            f"the {MAX_PLANNER_TOOL_CALLS} limit -- stopping."
                        )
                        break
                    if name in ("add_tasks", "read_ledger"):
                        planning_tool_calls[name] = planning_tool_calls.get(name, 0) + 1
                        if planning_tool_calls[name] >= MAX_PLANNING_CALLS:
                            halt(
                                f"planner loop guard: '{name}' called "
                                f"{planning_tool_calls[name]}x -- stopping."
                            )
                            break
                if _halt:
                    break

            elif msg_type == "ToolMessage":
                # The shared predicate, not a fourth private copy. This
                # guard's own four markers could not see "BLOCKED:", so
                # three consecutive denials never tripped it. It reads the
                # whole message rather than the content since OPEN-16:
                # `status` says whether a tool failed without guessing from
                # text that may simply quote a failure.
                if is_rudra_refusal(msg):
                    # NO EVENT (OPEN-94), the same rule as
                    # subagents/runner.py. The repeat guard is live on this
                    # stack too -- run 2cde3406f7d6 recorded
                    # roles.planner.reads_deduped: 2 -- so the defect was
                    # here as well, and a refusal must neither count as a
                    # failure nor clear a real streak.
                    pass
                elif message_is_error(msg):
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        halt("3 consecutive planner failures -- stopping.")
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
    usage: Any = None,
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
    a worse instruction. `feedback` carries whatever words the consult needs
    -- the user's for a revision, the gate's unowned failure list for
    `stale_failures` (OPEN-23) -- and both are interpolated unchanged.

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
        # `{task.id} cannot be dropped` is load-bearing, not politeness
        # (OPEN-24). run_task has already set this task BLOCKED
        # (loop/engine.py:356,370) before work() consults here, and
        # drop_task refuses every _SETTLED status by construction
        # (loop/tools.py:24,92). This message used to end "or drop_task it",
        # so Rudra was ordering a call that can never succeed -- and a
        # planner issued it three times, correctly obeying its instructions.
        # drop_task stays named because the blocker can make a task that is
        # still PENDING pointless, and retracting those is a real move.
        message = (
            f"Task {task.id} ({task.description}) failed and was given up on:\n\n"
            f"{task.note}\n\n"
            f"{task.id} is already recorded as blocked; it cannot be dropped or "
            "retried. Call add_tasks with a task taking a DIFFERENT approach. If "
            "this blocker also makes other tasks pointless, drop_task those -- "
            "they must still be pending. If neither applies, reply DONE and stop."
        )
    elif reason == "stale_failures":
        # OPEN-23's second half. Every task is finished and the gate is still
        # red, so these failures were never any task's -- no coder was ever
        # handed them, and none ever will be. Deliberately NOT worded like
        # `blocked`: nothing here failed to be done, so asking for "a
        # DIFFERENT approach" would name a first approach that never existed.
        if not feedback.strip():
            msg = "a stale-failure consult needs the failure list; got an empty string"
            raise ValueError(msg)
        message = (
            "Every task is finished, but the test suite is still failing, and "
            "these failures were failing before any remaining task ran -- so no "
            "task owns them:\n\n"
            f"{feedback.strip()}\n\n"
            "Call add_tasks with a task that fixes them, naming the files above. "
            "If they are tests asserting behaviour nothing was ever asked to "
            "build, the task is to build it. If nothing should be done, reply "
            "DONE and stop."
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

    if reason != "initial":
        # OPEN-65. Every re-consult asks about a ledger the planner is not
        # otherwise shown -- and `ledger_empty` ASSERTS its state ("Every
        # task is finished") without carrying it. That is not merely terse,
        # because a stage's consults all run on one thread
        # (`thread_id` below): the model reads the assertion against
        # whatever `read_ledger` last returned it, which in run11 was a
        # snapshot taken one turn after t5 was created and long before it
        # was worked -- t5 [pending]. Contradicted and given nothing to
        # reconcile with, it re-declared t5, reworded, and the run spent
        # 133.1s writing nothing.
        #
        # Measured over run8-run13 by
        # docs/superpowers/plans/2026-08-31-open59-duplicate-rate.py: six
        # `ledger_empty` consults, `read_ledger` called on NONE of them, and
        # the one that added a task added that duplicate. The tool has
        # always been there; being shown is what the coder gets and this
        # did not (OPEN-39 -- an agent that writes into something is shown
        # it, never offered a tool that would fetch it).
        #
        # Before the ask, not after it: this is the state the instructions
        # that follow are about, and `blocked` already opens with the state
        # of the one task it names.
        message = f"The task ledger as it stands:\n\n{render_ledger(ledger)}\n\n{message}"

    # The return value is deliberately still discarded, and OPEN-100 is why
    # that is now acceptable: a halted stage announces itself through
    # `_announce_halt` -- console line, `planner-guard` NOTICE and
    # `roles.planner.planner_halts` -- so the caller no longer learns nothing.
    # Acting on it here would be a second decision about a stopped run, which
    # belongs to the loop and not to a consult.
    await _stream_planner_turn(
        agent,
        message,
        thread_id=f"{session_id}-{stage}",
        gate=gate,
        console=console,
        trace=trace,
        usage=usage,
    )
