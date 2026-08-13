"""Run setup for Rudra. The loop itself lives in rudra.loop (Step 9c)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from rudra.config import get_config
from rudra.facts import FactStore, facts_block
from rudra.git.core import auto_branch
from rudra.loop import plan, work
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve, render_plan
from rudra.state import ensure_layout

# How many times a user may send the plan back before Rudra stops
# offering. Someone revising a fourth time wants to change the request,
# not the plan (S10c.4). Deliberately not configurable: an inert config
# key is worse than no key.
MAX_REVISIONS = 3


@dataclass
class AgentContext:
    """Context passed to the agent during execution."""

    project_path: Path
    task: str
    console: Console

    dry_run: bool = False
    verbose: bool = False
    stop_on_error: bool = True

    command: str = "auto"

    planner_model: str = ""
    coder_model: str = ""


@dataclass
class AgentResult:
    """Result of an agent execution."""

    success: bool
    message: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    iterations: int = 0


class RudraAgent:
    """Owns one run's setup and hands the work to the loop (Step 9c).

    The file-by-file orchestration this class used to carry is gone: the
    ledger, the fix loop and the gate live in `rudra.loop`. What remains
    here is assembly -- the branch, the trace header, and the checkpointer
    to close.
    """

    def __init__(
        self,
        context: AgentContext,
        planner_agent,
        session_id: str,
        db_conn,
        loop_context,
        planner_callback,
        ledger,
        gate=None,
        facts=None,
        approve=None,
    ):
        self.context = context
        self.planner_agent = planner_agent
        self.session_id = session_id
        self.console = context.console
        self._db_conn = db_conn
        self._loop_context = loop_context
        self._planner_callback = planner_callback
        # The same Ledger object the planner's tools mutate. A copy would
        # leave the loop with no tasks.
        self._ledger = ledger
        # The permission gate. None streams ungated, which only a caller
        # constructing RudraAgent by hand can produce.
        self.gate = gate
        # The run's fact store, for presenting the plan. The same object
        # the stages recorded into.
        self._facts = facts
        # How approval is obtained. Injected and defaulting to
        # auto-approve, so every existing caller and every test runs
        # without a prompt (S10c.3).
        self._approve = approve or auto_approve
        self.iterations = 0

    async def close(self) -> None:
        if self._db_conn is not None:
            await self._db_conn.close()
            self._db_conn = None

    def _log_always(self, message: str, style: str = "") -> None:
        if style:
            self.console.print(f"[{style}]{message}[/{style}]")
        else:
            self.console.print(message)

    def _status(self, message: str) -> None:
        self.console.print(f"[dim]→ {message}[/dim]")

    def _log_single_message(self, msg: Any, index: int, prefix: str = "") -> None:
        msg_type = type(msg).__name__
        tag = f"[{prefix}] " if prefix else ""

        if msg_type == "AIMessage":
            tool_calls = getattr(msg, "tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "?")
                    args = str(tc.get("args", {}))[:400]
                    self._log_always(
                        f"[bold cyan]{tag}→ [{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args}"
                    )
            else:
                content = str(getattr(msg, "content", ""))[:300].replace("\n", " ")
                self._log_always(f"[bold cyan]{tag}← [{index}] AI[/bold cyan]  {content}")

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
                self._log_always(
                    f"[bold red]{tag}✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
                )
            else:
                self._log_always(f"[green]{tag}✓ [{index}] {tool_name}:[/green] {content}")

        elif msg_type == "HumanMessage":
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim]{tag}[{index}] USER: {content}[/dim]")

        else:
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim]{tag}[{index}] {msg_type}: {content}[/dim]")

    async def run(self) -> AgentResult:
        """Plan, work, verify, fix, and report. The whole run (C6.1)."""
        try:
            if self.context.dry_run:
                # A1.40: this still previews nothing. The flag's behaviour is
                # unchanged by Step 9c; the row moved with the code.
                self._status("Dry run — no files written.")
                return AgentResult(
                    success=True,
                    message="Dry run completed (no files written)",
                    files_created=[],
                    files_modified=[],
                )

            # After the dry-run return, so --dry-run creates nothing; before
            # the planner, so every file the run produces lands on the new
            # branch rather than straddling two.
            _maybe_auto_branch(
                self.context.project_path,
                self.context.task,
                cfg=get_config(),
                gate=self.gate,
                console=self.context.console,
            )

            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")

            ledger = await plan(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=self._ledger,
            )

            decision = await self._settle_plan(ledger)
            if decision is not PlanDecision.APPROVE:
                return self._plan_only_result(ledger, decision)

            return await work(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=ledger,
            )

        except Exception:
            import traceback

            self._log_always("[bold red]\n!! Agent crashed — full traceback:[/bold red]")
            self._log_always(traceback.format_exc())
            raise

    async def _settle_plan(self, ledger) -> PlanDecision:
        """Show the plan and find out whether to run it (C6.9).

        `plan` mode never approves: it presents and stops, which is what
        `--plan` has always claimed to do while denying everything and
        printing nothing. `--auto` and every programmatic caller get
        auto_approve, so they never pause.
        """
        self.console.print()
        self.console.print(render_plan(ledger, self._facts))

        if get_config().permissions.mode == "plan":
            self.console.print("\n[dim]Plan mode — nothing was executed.[/dim]")
            return PlanDecision.CANCEL

        for attempt in range(MAX_REVISIONS + 1):
            answer = self._approve(self.console)
            if answer.decision is not PlanDecision.REVISE:
                return answer.decision
            if attempt == MAX_REVISIONS:
                break
            await self._planner_callback(
                ledger,
                self.context.task,
                stage="breakdown",
                reason="revision",
                feedback=answer.feedback,
            )
            self.console.print()
            self.console.print(render_plan(ledger, self._facts))

        self.console.print(
            f"\n[yellow]Revised {MAX_REVISIONS} times — change the request instead.[/yellow]"
        )
        return PlanDecision.CANCEL

    def _plan_only_result(self, ledger, decision: PlanDecision) -> AgentResult:
        """A run that planned and stopped. Not a failure -- the user chose."""
        counts = ledger.counts()
        return AgentResult(
            success=True,
            message=(
                f"Plan not executed ({decision.value}): {counts['requested']} task(s) declared"
            ),
            files_created=[],
            files_modified=[],
        )


def _maybe_auto_branch(project_path: Path, task: str, *, cfg, gate, console: Console) -> None:
    """Branch before the run, when the user asked for it and it is safe.

    Off by default (Step 8 spec S8.3). Both outcomes are printed: silence
    would leave the user unable to tell whether it ran, which is the
    complaint A1.50 recorded against a setting that accepted a name and then
    quietly did nothing.

    Nothing here can end a run. A branch is a convenience, and a user who
    cannot get one still wants their task done on the branch they are on.
    """
    if not cfg.tools.auto_branch:
        return

    try:
        outcome = auto_branch(project_path, task, gate=gate, console=console, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - a convenience must not end a run
        console.print(f"[yellow]No branch created: {exc}.[/yellow]")
        return

    if outcome.branch is not None:
        console.print(f"[dim]Working on branch {outcome.branch}[/dim]")
    else:
        console.print(f"[yellow]No branch created: {outcome.skipped_reason}.[/yellow]")


def _ensure_agents_md(rudra_dir: Path, facts: Any = None) -> None:
    """Create a starter AGENTS.md if one does not already exist.

    Renders whatever facts exist rather than four fixed fields (C6.8a).
    Still create-once: A1.9 -- this file is never written again -- is real
    and belongs to C7.3, which makes it a living document.
    """
    agents_md = rudra_dir / "AGENTS.md"
    if agents_md.exists():
        return

    block = facts_block(facts).strip()
    stack_section = (
        block if block else "## Project Facts\n(not yet determined — the agent will fill this in)"
    )

    agents_md.write_text(
        f"# Project Memory\n\n"
        f"This file is your persistent memory across sessions.\n"
        f"Update it using edit_file after completing any task.\n\n"
        f"{stack_section}\n\n"
        f"## Project Structure\n(not yet built)\n\n"
        f"## Architecture Notes\n(none yet)\n\n"
        f"## Session Log\n(no sessions yet)\n",
        encoding="utf-8",
    )


def build_backend(cfg, project_path: Path):
    """The backend for one run: shell on the default, artifacts on a route.

    A composite from the start per D13 — Step 11 adds a "/skills/" route to
    `routes` and changes nothing else. Retrofitting the composite later
    would rewire every agent constructor.

    `artifacts_root` matters more than it looks. It defaults to "/", i.e.
    the backend root, i.e. the user's project — so deepagents' oversized
    tool-result eviction and its summarization middleware would both write
    into the repo Rudra is working on (TODO.md A1.45). Routing it into
    .rudra/run/artifacts/ puts both in D15's volatile subtree.

    `execute` only works on a SandboxBackendProtocol. A plain
    FilesystemBackend registers the tool and errors when it is called, which
    U.17 measured — so `[tools] shell = false` genuinely removes the
    capability rather than merely discouraging it.
    """
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.backends.local_shell import LocalShellBackend

    from rudra.permissions.env import scrubbed_env
    from rudra.state.paths import rudra_paths

    paths = rudra_paths(project_path)

    if cfg.tools.shell:
        # env= rather than inherit_env=True: the latter hands the model's
        # shell every API key the user exported, and anything the agent
        # prints goes to the provider. See TODO.md A1.44 and C1.5.
        default = LocalShellBackend(
            root_dir=str(project_path),
            virtual_mode=True,
            env=scrubbed_env(cfg),
        )
    else:
        default = FilesystemBackend(root_dir=str(project_path), virtual_mode=True)

    return CompositeBackend(
        default=default,
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )


async def create_main_agent(
    project_path: Path,
    task: str,
    command: str = "build",
    console: Optional[Console] = None,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> RudraAgent:
    """Factory function — creates the planner + stores coder config for orchestration."""
    console = console or Console()
    project_path = project_path.resolve()
    cfg = get_config()

    context = AgentContext(
        project_path=project_path,
        task=task,
        console=console,
        dry_run=dry_run,
        verbose=verbose,
        command=command,
        planner_model=cfg.model_for("planner").model,
        coder_model=cfg.model_for("coder").model,
        **kwargs,
    )

    import uuid

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from rudra.compat.deepagents_path import install_path_normalizer
    from rudra.permissions import build_gate, stdin_is_interactive

    paths = ensure_layout(project_path)

    # Durable: what previous runs established about this project is still
    # true (D15). An absent or corrupt file loads empty rather than raising.
    facts = FactStore.load(paths.facts_json)

    # A user exists only when a terminal does and the run is not
    # unattended. `plan` mode keeps asking -- that is the one mode where
    # clarification is the entire point.
    interactive = cfg.permissions.mode != "auto" and stdin_is_interactive()

    # No plan path: PLAN.md went with the checklist in Step 9c, so the
    # normalizer's planned-filename hint has nothing to read (A1.65).
    install_path_normalizer(project_path)

    filesystem_backend = build_backend(cfg, project_path)

    # Shell and the permission layer are constructed together, and neither
    # is optional. Section E hard gate 2: LocalShellBackend must never ship
    # without the gate in the same step.
    gate = build_gate(cfg, project_path)

    # AGENTS.md is durable and stays at the .rudra/ root (D15 / §0.7). Its
    # tech-stack section is rendered from the facts, and tech_stack.md is
    # gone: facts reach every agent through their prompts now (S10a.7).
    _ensure_agents_md(paths.root, facts)

    checkpoints_db = str(paths.checkpoints_db)
    db_conn = await aiosqlite.connect(checkpoints_db)
    checkpointer = AsyncSqliteSaver(conn=db_conn)
    await checkpointer.setup()

    session_id = uuid.uuid4().hex[:12]

    from rudra.agent.planner_agent import STAGES, consult_planner, create_planner_agent
    from rudra.loop import Ledger, LoopContext
    from rudra.subagents import SubagentContext

    subagent_context = SubagentContext(
        project_path=project_path,
        backend=filesystem_backend,
        gate=gate,
        console=console,
        cfg=cfg,
        checkpointer=checkpointer,
        session_id=session_id,
        facts=facts,
    )
    loop_context = LoopContext(
        subagents=subagent_context,
        project_path=project_path,
        console=console,
        cfg=cfg,
        paths=paths,
    )

    # One Ledger, shared by reference: the planner's tools mutate it and the
    # loop reads it back. Two objects would leave the loop with no tasks.
    ledger = Ledger()
    # One agent per stage (S10b.1). Construction is cheap -- build_model
    # makes no network call (llm/factory.py:64-68) and create_deep_agent
    # only compiles a graph -- and building all three up front keeps the
    # callback a lookup rather than a factory.
    #
    # Every stage shares the SAME ledger and fact store: those are the
    # only channel between stages, and a copy would leave the coder with
    # an architecture nobody recorded.
    planners = {
        stage: create_planner_agent(
            task=task,
            project_path=project_path,
            filesystem_backend=filesystem_backend,
            checkpointer=checkpointer,
            console=console,
            gate=gate,
            ledger=ledger,
            paths=paths,
            facts=facts,
            interactive=interactive,
            stage=stage,
        )
        for stage in STAGES
    }

    async def planner_callback(
        run_ledger, request, *, stage, reason="initial", task=None, feedback=""
    ):
        # `feedback` carries a plan revision's wording (C6.9). Without it
        # a revision reaches consult_planner empty and raises.
        await consult_planner(
            planners[stage],
            run_ledger,
            request,
            stage=stage,
            reason=reason,
            task=task,
            feedback=feedback,
            gate=gate,
            console=console,
            session_id=session_id,
        )

    return RudraAgent(
        context=context,
        # The breakdown stage: the one that outlives planning, since it is
        # the only stage re-entered on a block (S10b.3).
        planner_agent=planners["breakdown"],
        session_id=session_id,
        db_conn=db_conn,
        loop_context=loop_context,
        planner_callback=planner_callback,
        ledger=ledger,
        gate=gate,
        facts=facts,
        # Only prompt when a human can answer. `interactive` is the same
        # flag that decides whether ask_user is registered (S10a.5).
        approve=ask_approval if interactive else auto_approve,
    )
