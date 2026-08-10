"""Rudra CLI - Autonomous Coding Agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from rudra import __version__
from rudra.agent import AgentResult, create_main_agent
from rudra.config import get_config
from rudra.filesystem import project_tree
from rudra.state import ProjectConfigManager, ProjectContext

# Create the Typer app
app = typer.Typer(
    name="rudra",
    help="Rudra - Autonomous Coding Agent CLI",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)

models_app = typer.Typer(help="Inspect and test configured models.")
app.add_typer(models_app, name="models")

console = Console()


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER_LINES = [
    r" ██████╗  ██╗   ██╗ ██████╗  ██████╗   █████╗ ",
    r" ██╔══██╗ ██║   ██║ ██╔══██╗ ██╔══██╗ ██╔══██╗",
    r" ██████╔╝ ██║   ██║ ██║  ██║ ██████╔╝ ███████║",
    r" ██╔══██╗ ██║   ██║ ██║  ██║ ██╔══██║ ██╔══██║",
    r" ██║  ██║ ╚██████╔╝ ██████╔╝ ██║  ██║ ██║  ██║",
    r" ╚═╝  ╚═╝  ╚═════╝  ╚═════╝  ╚═╝  ╚═╝ ╚═╝  ╚═╝",
]

# Gradient colours cycling through the banner lines (bright yellow-orange → deep orange → red-orange)
GRADIENT = [
    "bold color(214)",
    "bold color(208)",
    "bold color(202)",
    "bold color(196)",
    "bold color(202)",
    "bold color(208)",
]


def print_banner() -> None:
    """Print the Rudra branded launch banner, Claude Code-style."""
    console.print()

    # ASCII art with per-line colour gradient
    for i, line in enumerate(BANNER_LINES):
        colour = GRADIENT[i % len(GRADIENT)]
        console.print(f"[{colour}]{line}[/{colour}]")

    console.print()

    # Sub-title row
    console.print(
        "  [bold white]Rudra (रुद्र) — The fierce, storm-like form of Shiva; the howler/roarer[/bold white]  "
    )
    console.print(
        "  [bold italic white]The roaring storm that hammers and purifies code [/bold italic white]  "
    )

    console.print()
    console.print()

    console.print("  [bold color(202)]Archish built Rudra (रुद्र) with ❤️ [/bold color(202)]  ")
    console.print()

    console.print(
        f"  [dim]v{__version__}[/dim]  "
        f"  [dim]·[/dim]  "
        f"  [dim]Type [/dim][bold cyan]/help[/bold cyan][dim] for commands[/dim]"
    )

    console.print()

    # Hint strip (like Claude Code's one-liner tips)
    console.print(
        "  [dim]✦ Just describe what you want — Rudra figures out the rest  ·  /exit to quit[/dim]"
    )

    console.print()


# ---------------------------------------------------------------------------
# Blinking REPL prompt (prompt_toolkit preferred, rich fallback)
# ---------------------------------------------------------------------------


def _make_prompt_toolkit_session():
    """Create a prompt_toolkit PromptSession with a blinking cursor."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        style = Style.from_dict(
            {
                "prompt": "ansibrightcyan bold",
                "": "ansiwhite",
            }
        )

        bindings = KeyBindings()

        @bindings.add("escape", "escape", "escape")
        def _(event):
            event.app.exit(result="/exit")

        session = PromptSession(
            style=style,
            mouse_support=False,
            key_bindings=bindings,
        )
        return session
    except ImportError:
        return None


async def _prompt_input(session, prompt_text: str) -> str:
    """Read one line from the user, using prompt_toolkit if available.

    Uses prompt_async() so it cooperates with the already-running asyncio loop
    instead of trying to nest a second asyncio.run() call inside it.
    """
    if session is not None:
        from prompt_toolkit.formatted_text import HTML

        # prompt_async is awaitable — safe to call inside a running event loop
        return await session.prompt_async(HTML(f"<prompt>{prompt_text}</prompt> "))
    # Fallback: rich Prompt (blocking) — run it in a thread executor so we
    # don't block the event loop
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: Prompt.ask(f"[bold cyan]{prompt_text}[/bold cyan]")
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print(f"[bold]Rudra[/bold] v{__version__}")
        raise typer.Exit()


def get_project_path(project_dir: Optional[Path]) -> Path:
    """Get the project path, defaulting to current directory."""
    if project_dir:
        return project_dir.resolve()
    return Path.cwd()


def load_project_context(project_path: Path) -> ProjectContext:
    """Load saved project context from .rudra/project.json."""
    return ProjectConfigManager(project_path).load()


# ---------------------------------------------------------------------------
# Main entry-point / interactive REPL
# ---------------------------------------------------------------------------


@models_app.command("test")
def models_test(
    role: Optional[str] = typer.Option(
        None, "--role", help="Probe one role only (default: planner and coder)"
    ),
) -> None:
    """Verify every configured model is reachable and can call tools.

    deepagents requires tool calling for every agent, and reachability does
    not imply it — hence the separate Tools stage. See TODO.md C1.6.
    """
    from rudra.llm.probe import ROLES_TO_PROBE, probe_role

    roles = (role,) if role else ROLES_TO_PROBE

    table = Table(title="Model check", header_style="bold")
    for column in ("Role", "Provider", "Model", "Construct", "Reach", "Tools", "Ctx"):
        table.add_column(column, overflow="fold")

    failed = False
    for name in roles:
        result = probe_role(name)
        failed = failed or not result.ok
        style = "green" if result.ok else "red"
        table.add_row(
            result.role,
            result.provider,
            result.model,
            result.construct,
            result.reach,
            result.tools,
            str(result.context_tokens) if result.context_tokens else "-",
            style=style,
        )

    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.callback(
    invoke_without_command=True,
    # The task prompt arrives through ctx.args rather than as a declared
    # Argument. A positional parameter on an invoke_without_command callback
    # is consumed by the callback before click ever tries to resolve a
    # command name, which made every subcommand unreachable: `rudra models`
    # bound "models" to the prompt and launched an agent run for a task
    # called "models". Collecting extras instead lets click match real
    # command names first and leaves everything else as the prompt.
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def main(
    ctx: typer.Context,
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files"),
    verbose: Optional[bool] = typer.Option(
        None, "--verbose/--no-verbose", "-V", help="Show detailed output"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Rudra - Autonomous Coding Agent CLI.

    Run a single task:   rudra "create a hello world script"
    Start the REPL:      rudra
    Check your models:   rudra models test
    """
    # A subcommand was explicitly given — let it handle everything
    if ctx.invoked_subcommand is not None:
        return

    # Everything click did not consume as an option or a command name is the
    # task. Joined rather than indexed so an unquoted `rudra build a flask
    # app` behaves the same as the quoted form.
    prompt: Optional[str] = " ".join(ctx.args).strip() or None

    # project_path is resolved FIRST, and the Config is seeded with it
    # unconditionally, because get_config() caches for the rest of the
    # process — no later call can correct which .env was read. Seeding
    # inside the `if verbose is None` block below would skip it whenever
    # --verbose or --no-verbose is passed, leaving the first downstream
    # get_config() to fall back to the cwd. See TODO.md A5.2.
    project_path = get_project_path(project_dir)
    cfg = get_config(project_path)

    # A default of `config.agent.verbose` here would be evaluated when this
    # module is imported, which is the A1.15 defect. Three-state instead:
    # absent -> consult config, --verbose -> True, --no-verbose -> False.
    if verbose is None:
        verbose = cfg.agent.verbose

    project_context = load_project_context(project_path)

    if prompt:
        # ── Single-shot task mode ──────────────────────────────────────────
        print_banner()
        console.print(
            Panel(
                f"[bold]{prompt}[/bold]\n"
                f"[dim]Path:[/dim] {project_path}\n"
                f"[dim]Planner:[/dim] {get_config().model_for('planner').model}  "
                f"[dim]│  Coder:[/dim] {get_config().model_for('coder').model}",
                title="⚡ Task",
                border_style="bright_cyan",
            )
        )

        async def _run() -> AgentResult:
            agent = await create_main_agent(
                project_path=project_path,
                task=prompt,
                project_context=project_context,
                command="auto",
                console=console,
                dry_run=dry_run,
                verbose=verbose,
            )
            try:
                return await agent.run()
            finally:
                await agent.close()

        result: AgentResult = asyncio.run(_run())

        if result.success:
            console.print(
                Panel(
                    f"[green]✓[/green] {result.message}\n\n"
                    f"Files created:  {len(result.files_created)}\n"
                    f"Files modified: {len(result.files_modified)}\n"
                    f"Iterations:     {result.iterations}",
                    title="✅ Complete",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    f"[red]✗[/red] {result.message}",
                    title="❌ Error",
                    border_style="red",
                )
            )
            raise typer.Exit(1)

    else:
        # ── Interactive REPL ───────────────────────────────────────────────
        print_banner()

        console.print(f"  [dim]Working directory:[/dim] [bold white]{project_path}[/bold white]\n")

        pt_session = _make_prompt_toolkit_session()

        async def _repl_session():
            while True:
                try:
                    user_input = await _prompt_input(pt_session, "rudra ❯")
                    cmd = user_input.strip().lower()

                    if cmd in ("/exit", "/quit", "exit", "quit"):
                        console.print("\n[dim]  Goodbye — happy coding! 👋[/dim]\n")
                        break
                    elif cmd == "/help":
                        _print_help()
                        continue
                    elif cmd == "/tree":
                        # markup=False: filenames are data, not Rich markup.
                        # A file named `[draft].md` would otherwise be parsed
                        # as a style tag and vanish from the listing.
                        console.print(project_tree(project_path), markup=False)
                        continue
                    elif cmd.startswith("/"):
                        console.print(
                            f"  [yellow]Unknown command:[/yellow] {cmd}  [dim](type /help)[/dim]"
                        )
                        continue

                    if not user_input.strip():
                        continue

                    console.print(
                        Panel(
                            f"[bold]{user_input}[/bold]\n"
                            f"[dim]Path:[/dim] {project_path}\n"
                            f"[dim]Planner:[/dim] {get_config().model_for('planner').model}  "
                            f"[dim]│  Coder:[/dim] {get_config().model_for('coder').model}",
                            title="⚡ Task",
                            border_style="bright_cyan",
                        )
                    )

                    agent = await create_main_agent(
                        project_path=project_path,
                        task=user_input,
                        project_context=project_context,
                        command="auto",
                        console=console,
                        dry_run=dry_run,
                        verbose=verbose,
                    )
                    try:
                        result = await agent.run()
                    finally:
                        await agent.close()

                    if result.success:
                        console.print(
                            Panel(
                                f"[green]✓[/green] {result.message}\n\n"
                                f"Files created:  {len(result.files_created)}\n"
                                f"Files modified: {len(result.files_modified)}",
                                title="✅ Complete",
                                border_style="green",
                            )
                        )
                    else:
                        console.print(
                            Panel(
                                f"[red]✗[/red] {result.message}",
                                title="❌ Error",
                                border_style="red",
                            )
                        )

                except KeyboardInterrupt:
                    console.print("\n  [dim]Interrupted — type /exit to quit[/dim]")
                except EOFError:
                    console.print("\n[dim]  Goodbye! 👋[/dim]\n")
                    break

        asyncio.run(_repl_session())


def _print_help() -> None:
    """Print the in-REPL help table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("cmd", style="bold cyan", no_wrap=True)
    table.add_column("desc", style="dim white")

    rows = [
        ("/help", "Show this help message"),
        ("/tree", "Print the project file tree"),
        ("/exit", "Quit Rudra"),
        ("", ""),
        ("Examples:", ""),
        ('"Build a FastAPI app with JWT"', "Creates a new project"),
        ('"Fix the login bug in auth.py"', "Debugs and fixes issues"),
        ('"Review my code for security"', "Analyzes without modifying"),
        ('"Add CORS middleware to main.py"', "Targeted file edit"),
    ]
    for cmd, desc in rows:
        table.add_row(cmd, desc)

    console.print()
    console.print(
        Panel(table, title="[bold cyan]Rudra Help[/bold cyan]", border_style="cyan", padding=(1, 2))
    )
    console.print()


# Entry point
if __name__ == "__main__":
    app()
