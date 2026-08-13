"""Planner agent — decides what work the request needs.

Step 9c replaced PLAN.md and current_task.md with the task ledger
(C6.10). The planner declares work through add_tasks and retracts it
through drop_task; it cannot mark anything done, because no tool can --
only loop/engine.py writes that, and only when the gate passes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from rich.console import Console

from rudra.config import get_config
from rudra.facts import facts_block
from rudra.filesystem import project_tree
from rudra.llm import build_model
from rudra.loop.tools import create_ledger_tools
from rudra.middleware import (
    FixWriteParamsMiddleware,
    TaskAnchorMiddleware,
)
from rudra.permissions import run_with_approvals
from rudra.tools.interaction_tools import create_interaction_tools


def build_planner_prompt(
    task: str,
    project_path: Path,
    facts: Any = None,
    *,
    can_ask: bool = True,
    max_questions: int = 5,
) -> str:
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: decide WHAT WORK the request needs. You never write project code.

## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    block = facts_block(facts)
    if block:
        base += f"\n{block}"

    base += f"""
## REQUEST
{task}

## WHAT A TASK IS

A task is a unit of WORK, described in plain language. Not a filename.

  GOOD: "write a CSV parser that handles quoted commas"
  GOOD: "write tests for the parser"
  GOOD: "add a --format flag to the CLI"
  BAD:  "parser.py"
  BAD:  "create the project structure"

One task may touch several files. Work that needs tests gets its own task.

## ESTABLISHING FACTS
"""

    if can_ask:
        base += f"""
Ask only what you cannot infer from the request or the codebase. Batch
related questions into a SINGLE ask_user() call — one key per question.
You have {max_questions} questions for this whole run.
"""
    else:
        base += """
This run is unattended: there is nobody to ask. Infer what you need from
the request and the codebase.
"""

    base += """
Record every fact you establish — whether you inferred it or the user
answered it — with record_fact(), and say WHY you believe it. The coder,
the tester and the reviewer all read those facts; one you keep to
yourself is one they do not have.

## WORKFLOW

1. Call read_file() on anything you need to understand the project
2. Establish and record the facts the work depends on
3. Call add_tasks() ONCE with every task you can foresee
4. STOP

You will be consulted again if a task fails or if the work runs out. When
that happens, add a task taking a DIFFERENT approach, or drop_task() one
that turned out to be unnecessary.

## WHAT YOU CANNOT DO

You cannot mark anything done. A deterministic verification gate decides
that — it parses the code, type checks it, runs the tests, and scans for
placeholders. Do not claim a task is complete, and do not add a task whose
description is "verify" or "check": that already happens on its own.

- NEVER call write_file() on project source files — the coder handles that
"""
    return base


def build_planner_middleware(
    task: str,
    compat_task_anchor: bool = False,
    compat_sandbox_paths: bool = False,
) -> list:
    """The planner's middleware stack, with both D4 workarounds gated.

    FixWriteParamsMiddleware is first so it cleans tool args before anything
    else sees them. The planner previously got fence-stripping from
    OverwriteFilesystemBackend, which U.3 deletes — see TODO.md U.14.

    TaskAnchorMiddleware is opt-in per D4 (`[compat] task_anchor`): it was
    built for qwen3:14b losing the task mid-run, and the minimum model is
    now 32B (D6).
    """
    middleware: list = [FixWriteParamsMiddleware(strip_sandbox_prefixes=compat_sandbox_paths)]
    if compat_task_anchor:
        middleware.append(TaskAnchorMiddleware(task))
    return middleware


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
):
    """Create the planner deep agent.

    `ledger` and `facts` must be the SAME objects the loop reads: the
    planner's tools mutate them in place, and a copy would leave the
    engine with no tasks and the coder with no facts.

    `interactive` False means nobody can answer, so ask_user is never
    registered (S10a.5). The prompt is built to match, so the model is
    never told to call a tool it does not have.
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
    # the tester subagent writes tests (S9c.5).
    custom_tools = create_ledger_tools(ledger, paths.ledger_json) + create_interaction_tools(
        console,
        facts,
        paths.facts_json,
        max_questions=cfg.agent.max_questions,
        interactive=interactive,
    )

    middleware = build_planner_middleware(
        task,
        compat_task_anchor=cfg.compat.task_anchor,
        compat_sandbox_paths=cfg.compat.sandbox_paths,
    )
    if gate is not None:
        # First in the list: a denied call must be stopped before any other
        # middleware rewrites its arguments.
        middleware.insert(0, gate.middleware)

    # permissions= is deliberately absent. It raises NotImplementedError on
    # any execute-capable backend, which is every backend Rudra now builds.
    # See TODO.md U.7.
    return create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=build_planner_prompt(
            task,
            project_path,
            facts,
            can_ask=interactive and cfg.agent.max_questions > 0,
            max_questions=cfg.agent.max_questions,
        ),
        backend=filesystem_backend,
        checkpointer=checkpointer,
        memory=[".rudra/AGENTS.md"],
        middleware=middleware,
        interrupt_on=gate.interrupt_on if gate is not None else None,
    )


def _log_message(console: Console, msg: Any, index: int, prefix: str = "planner") -> None:
    msg_type = type(msg).__name__
    tag = f"[{prefix}] " if prefix else ""

    if msg_type == "AIMessage":
        tool_calls = getattr(msg, "tool_calls", [])
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "?")
                args = str(tc.get("args", {}))[:400]
                console.print(
                    f"[bold cyan]{tag}→ [{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args}"
                )
        else:
            content = str(getattr(msg, "content", ""))[:300].replace("\n", " ")
            console.print(f"[bold cyan]{tag}← [{index}] AI[/bold cyan]  {content}")

    elif msg_type == "ToolMessage":
        content = str(getattr(msg, "content", ""))
        tool_name = getattr(msg, "name", "?")
        first_line = content.split("\n")[0] if content else ""
        is_error = (
            first_line.startswith("Error:")
            or first_line.startswith("Cannot write to")
            or "Error:" in first_line
            or "Traceback" in first_line
            or "Errno" in first_line
            or "not a valid tool" in content
            or "Input should be a valid string" in content
            or "BLOCKED:" in first_line
        )
        if is_error:
            console.print(
                f"[bold red]{tag}✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
            )
        else:
            console.print(f"[green]{tag}✓ [{index}] {tool_name}:[/green] {content}")

    elif msg_type == "HumanMessage":
        content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
        console.print(f"[dim]{tag}[{index}] USER: {content}[/dim]")

    else:
        content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
        console.print(f"[dim]{tag}[{index}] {msg_type}: {content}[/dim]")


async def _stream_planner_turn(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    gate: Any,
    console: Console,
) -> bool:
    """Stream one planner turn. Returns False if a guard halted it.

    Lifted from RudraAgent._stream_planner in Step 9c: the class's loop
    is deleted and this is its only remaining caller. The guard logic is
    unchanged. It still carries A1.20's remaining half -- one `processed`
    counter across namespaces -- which is re-pointed to C9.1.
    """
    lg_config = {"configurable": {"thread_id": thread_id}}
    processed = 0
    consecutive_failures = 0
    planning_tool_calls: dict[str, int] = {}
    MAX_PLANNING_CALLS = 4
    _halt = False

    # run_with_approvals yields exactly what astream yields, so the parse
    # loop below is unchanged. It reads interrupts from get_state after
    # the stream drains, because __interrupt__ never appears in "values"
    # chunks and changing stream_mode would change the chunk shape this
    # loop depends on -- the loop that still carries A1.20's hole.
    async for chunk in run_with_approvals(
        agent,
        {"messages": [{"role": "user", "content": message}]},
        lg_config,
        gate,
        console,
    ):
        if _halt:
            break

        namespace, event = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
        msgs = event.get("messages", [])

        while processed < len(msgs):
            msg = msgs[processed]
            msg_type = type(msg).__name__

            if namespace:
                console.print(f"[dim](planner subagent {':'.join(namespace)})[/dim]")

            _log_message(console, msg, processed + 1)

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
                content = str(getattr(msg, "content", ""))
                first_line = content.split("\n")[0] if content else ""
                is_error = (
                    first_line.startswith("Error:")
                    or first_line.startswith("Cannot write to")
                    or "Error:" in first_line
                    or "Traceback" in first_line
                    or "Errno" in first_line
                )
                if is_error:
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

    return not _halt


async def consult_planner(
    agent: Any,
    ledger: Any,
    request: str,
    *,
    reason: str,
    task: Any = None,
    gate: Any,
    console: Console,
    session_id: str,
) -> None:
    """Ask the planner to add or drop tasks. It mutates the ledger via tools.

    Three reasons, three messages. There is deliberately no consult after
    an ordinary success -- that is what bounds the loop's model-call cost.
    """
    if reason == "initial":
        message = f"Break this request into tasks: {request}"
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
    else:  # pragma: no cover - guarded by the caller
        raise ValueError(f"unknown consult reason {reason!r}")

    await _stream_planner_turn(
        agent, message, thread_id=f"{session_id}-planner", gate=gate, console=console
    )
