"""Run setup for Rudra. The loop itself lives in rudra.loop (Step 9c)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from rudra.config import get_config
from rudra.facts import FactStore, facts_block
from rudra.git.core import auto_branch
from rudra.llm.retry import ProviderUnavailable
from rudra.loop import plan, work
from rudra.loop.plan_view import PlanDecision, ask_approval, auto_approve, render_plan
from rudra.state import ensure_layout

# How many times a user may send the plan back before Rudra stops
# offering. Someone revising a fourth time wants to change the request,
# not the plan (S10c.4). Deliberately not configurable: an inert config
# key is worse than no key.
MAX_REVISIONS = 3

# The artifacts route is fixed; skill routes depend on which sources a run
# resolves (Step 11c), so the full set is computed by route_prefixes().
ARTIFACTS_PREFIX = "/artifacts/"


def route_prefixes(sources=()) -> tuple[str, ...]:
    """Every prefix the backend mounts, for the path normalizer.

    One function because two things need this and must not disagree:
    build_backend mounts these, and install_path_normalizer must be told to
    leave them alone. A route the normalizer does not know about gets its
    path trimmed to the last two segments, so the model can list the files
    and never read one (A1.79). A test asserts the two agree.
    """
    from rudra.skills.sources import routes_for

    return (ARTIFACTS_PREFIX, *routes_for(sources))


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
    # The run's token tally (C7.5). None when nothing recorded one, which
    # is every programmatic caller that builds an AgentResult by hand.
    usage: Any = None


class ResumeRefused(Exception):
    """`--continue` cannot proceed, and the message says why.

    Refusing beats guessing here: every failure mode is one where doing
    the work anyway produces something the user did not ask for.
    """


def check_resumable(ledger_path: Path, prompt: str | None):
    """Load the ledger a resume would work, or refuse with a reason.

    Pure apart from the read, so the CLI can call it before building an
    agent and before any model is constructed.
    """
    from rudra.loop.ledger import Ledger

    if not ledger_path.exists():
        raise ResumeRefused("no previous run to continue — .rudra/run/ledger.json does not exist.")

    ledger = Ledger.load(ledger_path)

    if prompt and ledger.request and prompt.strip() != ledger.request.strip():
        raise ResumeRefused(
            "that is a different request from the one this plan was built for.\n"
            f"  planned for: {ledger.request}\n"
            f"  you asked:   {prompt}\n"
            "Run it without --continue to plan afresh, or drop the prompt to "
            "continue the original."
        )

    if not ledger.resumable():
        counts = ledger.counts()
        if counts["blocked"]:
            raise ResumeRefused(
                f"nothing pending — {counts['done']} done, {counts['blocked']} blocked. "
                "A blocked task failed the same way twice, so continuing would "
                "repeat it; change the request instead."
            )
        raise ResumeRefused(
            f"nothing pending — {counts['done']} of {counts['requested']} task(s) "
            "finished. There is nothing left to continue."
        )

    return ledger


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
        resume: bool = False,
        mcp=None,
        transcript=None,
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
        # The run's MCP client, or None. Held so close() can release it and
        # so a caller can see what was configured without re-reading disk.
        self.mcp = mcp
        # The run's TranscriptWriter, or None. Held so close() can flush
        # and release it: an unflushed handle keeps the file locked on
        # Windows, and a record nobody closed is one somebody finds empty.
        self._transcript = transcript
        # A resume works the ledger already on disk and never plans (C7.2).
        self.resume = resume
        self.iterations = 0

    async def close(self) -> None:
        if self._transcript is not None:
            self._transcript.close()
        if self.mcp is not None:
            await self.mcp.aclose()
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

            if self.resume:
                # plan() saves an empty ledger at engine.py:420, so resuming
                # must skip it entirely rather than filter afterwards.
                counts = self._ledger.counts()
                self._log_always(
                    f"[dim]Resuming: {counts['done']} done · "
                    f"{len(self._ledger.resumable())} pending · "
                    f"{counts['blocked']} blocked[/dim]"
                )
                decision = await self._settle_plan(self._ledger)
                if decision is not PlanDecision.APPROVE:
                    return self._plan_only_result(self._ledger, decision)
                self._record_plan_memory()
                return await work(
                    self.context.task,
                    context=self._loop_context,
                    planner=self._planner_callback,
                    ledger=self._ledger,
                )

            ledger = await plan(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=self._ledger,
            )

            decision = await self._settle_plan(ledger)
            if decision is not PlanDecision.APPROVE:
                return self._plan_only_result(ledger, decision)
            self._record_plan_memory()

            return await work(
                self.context.task,
                context=self._loop_context,
                planner=self._planner_callback,
                ledger=ledger,
            )

        except ProviderUnavailable as error:
            # A1.39: the provider failed, not Rudra. A stack dump through
            # the vendor's client internals tells the user nothing they can
            # act on, and this is the *expected* failure for anyone on a
            # free tier -- Documentation/08-project-status.md says so.
            self._log_always(f"[bold red]\n{error}[/bold red]")
            return AgentResult(
                success=False,
                message=str(error),
                files_created=[],
                files_modified=[],
            )

        except Exception:
            import traceback

            self._log_always("[bold red]\n!! Agent crashed — full traceback:[/bold red]")
            self._log_always(traceback.format_exc())
            raise

    def _record_plan_memory(self) -> None:
        """File the approved plan's facts in long-term memory (C8.3).

        After approval, never before: a plan the user cancelled is not a
        decision this project made. Called on the resume path too, where
        the facts are the ones a previous run established -- writing them
        again is free, because drawer ids are content-addressed and an
        identical fact is one drawer however often it is filed.
        """
        from rudra.loop.engine import record_plan_memory

        # getattr, not attribute access: a caller constructing RudraAgent by
        # hand can pass any object as loop_context, and several tests do.
        # The same reason record_task_memory reads its store this way.
        record_plan_memory(
            getattr(self._loop_context, "memory", None), self._facts, self._ledger.tasks
        )

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


def build_backend(cfg, project_path: Path, sources=()):
    """The backend for one run: shell on the default, artifacts and skills on routes.

    A composite from the start per D13, and Step 11b proved that decision
    out: mounting the rendered skill corpus cost one entry in `routes` and
    changed nothing else. Retrofitting the composite later would have
    rewired every agent constructor.

    `skills_root` is None when skills are switched off (`[skills] enabled
    = []`), in which case no route is mounted at all rather than an empty
    one being served for nothing.

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

    routes = {
        ARTIFACTS_PREFIX: FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
    }
    # Read-only in practice: nothing writes to a skill route, and the agents
    # given skills have no reason to. virtual_mode is what lets the composite
    # strip the prefix (filesystem.py:132), and execute still delegates to
    # `default` (composite.py:774), so shell stays rooted at the project.
    for source in sources:
        routes[source.route] = FilesystemBackend(root_dir=str(source.local_path), virtual_mode=True)

    return CompositeBackend(
        default=default,
        routes=routes,
        artifacts_root="/artifacts",
    )


def build_memory_store(project_path: Path, cfg: Any, console: Console) -> Any:
    """One MemoryStore for the run, or None if it cannot be built.

    TaxonomyError is caught here rather than by `degrades`, because it is
    raised by the *constructor* -- there is no store yet to degrade. A
    project directory whose name yields no usable wing costs the run its
    memory, never the run itself (C8.6), and says so rather than going
    quiet (S14.2).

    The recorded failure is reset here, not in `summarise`: it is
    process-global, and the REPL builds a fresh agent per input (A1.89).
    Without this, one broken turn would have every later turn report a
    failure over a palace that is fine.
    """
    from rudra.memory.degrade import reset_failures
    from rudra.memory.store import MemoryStore
    from rudra.memory.taxonomy import TaxonomyError

    reset_failures()
    try:
        return MemoryStore(project_path, backend=cfg.memory.backend)
    except TaxonomyError as exc:
        console.print(f"[yellow]Long-term memory is off for this run: {exc}[/yellow]")
        return None


async def create_main_agent(
    project_path: Path,
    task: str,
    command: str = "build",
    console: Optional[Console] = None,
    dry_run: bool = False,
    verbose: Optional[bool] = None,
    debug: bool = False,
    resume: bool = False,
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
        verbose=bool(verbose),
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
    from rudra.skills.sources import resolve_sources

    paths = ensure_layout(project_path)

    # Durable: what previous runs established about this project is still
    # true (D15). An absent or corrupt file loads empty rather than raising.
    facts = FactStore.load(paths.facts_json)

    # A user exists only when a terminal does and the run is not
    # unattended. `plan` mode keeps asking -- that is the one mode where
    # clarification is the entire point.
    interactive = cfg.permissions.mode != "auto" and stdin_is_interactive()

    # Skills are optional by configuration: `enabled = []` means no cache is
    # built at all, rather than an empty one rendered and mounted for
    # nothing. An unwritable cache root falls back to a per-run temp copy
    # (S11b.2) so the agent reasons the same way on a locked-down box as on
    # a laptop -- the run is told, and nothing else changes.
    skill_cache = None
    if cfg.skills.enabled:
        from rudra.skills.cache import ensure_cache
        from rudra.skills.registry import BUNDLES

        skill_cache = ensure_cache(BUNDLES, frozenset(cfg.skills.enabled))
        if skill_cache.fell_back:
            console.print(
                "[dim]Skills cache is not writable; using a temporary copy for this run.[/dim]"
            )

    # Resolved once per run and used three times: to mount the routes, to
    # tell the normalizer which prefixes are real, and to order the skill
    # index. One source of truth means they cannot disagree (A1.79).
    sources = resolve_sources(project_path, skill_cache)
    skills_sources = tuple(source.index_path for source in sources) or None

    # After the sources are known, because the prefixes it must leave alone
    # depend on them. No plan path: PLAN.md went with the checklist in Step
    # 9c, so the normalizer's planned-filename hint has nothing to read
    # (A1.65). Route prefixes are real mount points, not hallucinated
    # absolute paths (A1.79).
    install_path_normalizer(project_path, route_prefixes=route_prefixes(sources))
    filesystem_backend = build_backend(cfg, project_path, sources)

    # Shell and the permission layer are constructed together, and neither
    # is optional. Section E hard gate 2: LocalShellBackend must never ship
    # without the gate in the same step.
    gate = build_gate(cfg, project_path)

    # MCP is opt-in and no server ships enabled (S13.4). A malformed
    # .mcp.json warns and disables MCP rather than failing the run: D19
    # requires Rudra to work identically with no server present, and an
    # unreadable server file is a special case of absent.
    mcp_client = None
    if cfg.mcp.enabled:
        from rudra.mcp import McpClient, McpConfigError, mcp_json_path, read_mcp_json

        try:
            entries = read_mcp_json(mcp_json_path(project_path))
        except McpConfigError as exc:
            console.print(f"[yellow]Warning:[/yellow] MCP disabled — {exc}")
            entries = ()
        entries = tuple(entry for entry in entries if entry.name not in cfg.mcp.disabled_servers)
        if entries:
            # Constructing starts no process; the first call does (C4.4).
            mcp_client = McpClient(entries, timeout=cfg.mcp.timeout, console=console)

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
    from rudra.context.usage import RunUsage
    from rudra.loop import Ledger, LoopContext
    from rudra.subagents import SubagentContext
    from rudra.trace.sink import TraceSink, console_consumer, resolve_level
    from rudra.trace.transcript import TranscriptWriter, prune_transcripts, transcript_path

    # One per run, shared by reference: the planner stages and every
    # subagent record into the same object, and the panel reads it once.
    usage = RunUsage()

    # One sink per run, shared by reference for the reason the gate, the
    # FactStore and RunUsage are. The level is three-state because the CLI
    # flag is (A1.15): --verbose wins, --no-verbose means errors only, and
    # an absent flag consults [agent] verbose.
    #
    # Until Step 15a nothing consumed that flag at all (A1.90) and the
    # subagents printed nothing, so a run was silent exactly while the
    # coder was working.
    trace_level = resolve_level(verbose, cfg.agent.verbose)
    trace = TraceSink(level=trace_level)
    trace.add(console_consumer(console, trace_level))

    # --debug adds a second consumer on the SAME events (C9.7), so the
    # file and the screen cannot disagree about what happened -- only
    # about how much of it was drawn. A log that cannot be opened is
    # reported as None and skipped, never raised: bookkeeping must not end
    # a run (loop/engine.py:501-515).
    # The run's record (C9.5). NOT behind a flag, unlike --debug: the
    # point of a record is that it exists when somebody wants it, which is
    # always after the fact. Safe to write by default only because A1.95's
    # redaction happens where the event is built, so what reaches this
    # writer is already clean.
    #
    # Pruned once here rather than per event: retention is a per-run
    # decision, and doing it per event would stat the directory thousands
    # of times to reach the same answer.
    prune_transcripts(paths.transcripts)
    transcript = TranscriptWriter(transcript_path(paths, session_id))
    trace.add(transcript)

    if debug:
        from rudra.trace.debug import configure_debug_logging, debug_consumer

        if configure_debug_logging(paths.logs / "debug.jsonl", enabled=True) is not None:
            trace.add(debug_consumer())
        else:
            console.print("[yellow]--debug: could not open .rudra/run/logs/debug.jsonl[/yellow]")

    # One store, shared by reference between the subagents and the loop --
    # the rule the gate, the FactStore and the Ledger all follow. Two
    # MemoryStores would hold two ChromaDB handles on one palace.
    memory_store = build_memory_store(project_path, cfg, console)

    subagent_context = SubagentContext(
        project_path=project_path,
        backend=filesystem_backend,
        gate=gate,
        console=console,
        cfg=cfg,
        checkpointer=checkpointer,
        session_id=session_id,
        facts=facts,
        skills_sources=skills_sources,
        usage=usage,
        mcp=mcp_client,
        memory=memory_store,
        trace=trace,
    )
    loop_context = LoopContext(
        subagents=subagent_context,
        project_path=project_path,
        console=console,
        cfg=cfg,
        paths=paths,
        usage=usage,
        memory=memory_store,
    )

    # One Ledger, shared by reference: the planner's tools mutate it and the
    # loop reads it back. Two objects would leave the loop with no tasks.
    #
    # A resume loads what the last run left (C7.2). The CLI has already
    # called check_resumable, so this file exists and has pending work --
    # loading again here rather than passing the object in keeps
    # create_main_agent constructible without one.
    ledger = Ledger.load(paths.ledger_json) if resume else Ledger()
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
            skills_sources=skills_sources,
            skills_cache_root=skill_cache.root if skill_cache else None,
            usage=usage,
            # C8.4 names "before planning" as a retrieval trigger. The same
            # store the loop and the subagents hold -- one run, one palace.
            memory=memory_store,
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
            trace=trace,
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
        resume=resume,
        mcp=mcp_client,
        transcript=transcript,
    )
