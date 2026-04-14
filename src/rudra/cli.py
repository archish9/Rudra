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
from rudra.agent import create_main_agent, AgentResult
from rudra.config import config
from rudra.filesystem import VirtualFileSystem, FileSyncManager, SyncMode
from rudra.state import ProjectConfigManager, ProjectContext

# Create the Typer app
app = typer.Typer(
    name="rudra",
    help="Rudra - Autonomous Coding Agent CLI",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)

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
        f"  [bold white]Rudra (रुद्र) — The fierce, storm-like form of Shiva; the howler/roarer[/bold white]  "        
    )
    console.print()

    console.print(
        f"  [bold italic color(214)]* The roaring storm that hammers and purifies code [/bold italic color(214)]  "
    )

    console.print()
    console.print()
    console.print()

    console.print(
        f"  [dim]v{__version__}[/dim]  "
        f"  [dim]·[/dim]  "
        f"  [dim]Type [/dim][bold cyan]/help[/bold cyan][dim] for commands[/dim]"
    )

    console.print()

    # Hint strip (like Claude Code's one-liner tips)
    console.print(
        "  [dim]✦ [/dim][green]build[/green][dim] · [/dim]"
        "[yellow]fix[/yellow][dim] · [/dim]"
        "[magenta]edit[/magenta][dim] · [/dim]"
        "[cyan]review[/cyan][dim] · [/dim]"
        "[blue]suggest[/blue][dim]   —   [/dim]"
        "[dim]/exit to quit[/dim]"
    )

    console.print()


# ---------------------------------------------------------------------------
# Blinking REPL prompt (prompt_toolkit preferred, rich fallback)
# ---------------------------------------------------------------------------

def _make_prompt_toolkit_session():
    """Create a prompt_toolkit PromptSession with a blinking cursor."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.styles import Style

        style = Style.from_dict({
            "prompt": "ansibrightcyan bold",
            "": "ansiwhite",
        })

        session = PromptSession(
            style=style,
            mouse_support=False,
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
    return await loop.run_in_executor(None, lambda: Prompt.ask(f"[bold cyan]{prompt_text}[/bold cyan]"))


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

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    prompt: Optional[str] = typer.Argument(None, help="Task or question (e.g., 'Create a hello world script')"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory (defaults to current directory)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files"),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output"),
    max_agents: int = typer.Option(6, "--max-agents", help="Maximum number of sub-agents"),
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True, help="Show version and exit"
    ),
) -> None:
    """Rudra - Autonomous Coding Agent CLI."""
    # A subcommand was explicitly given — let it handle everything
    if ctx.invoked_subcommand is not None:
        return

    project_path = get_project_path(project_dir)
    project_context = load_project_context(project_path)
    config.agent.max_agents = max_agents

    if prompt:
        # ── Single-shot task mode ──────────────────────────────────────────
        print_banner()
        console.print(Panel(
            f"[bold]{prompt}[/bold]\n"
            f"[dim]Path:[/dim] {project_path}",
            title="⚡ Task",
            border_style="bright_cyan",
        ))

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
            console.print(Panel(
                f"[green]✓[/green] {result.message}\n\n"
                f"Files created:  {len(result.files_created)}\n"
                f"Files modified: {len(result.files_modified)}\n"
                f"Iterations:     {result.iterations}",
                title="✅ Complete",
                border_style="green",
            ))
        else:
            console.print(Panel(
                f"[red]✗[/red] {result.message}",
                title="❌ Error",
                border_style="red",
            ))
            raise typer.Exit(1)

    else:
        # ── Interactive REPL ───────────────────────────────────────────────
        print_banner()

        console.print(
            f"  [dim]Working directory:[/dim] [bold white]{project_path}[/bold white]\n"
        )

        pt_session = _make_prompt_toolkit_session()

        async def _chat_session():
            agent = await create_main_agent(
                project_path=project_path,
                task="",
                command="chat",
                project_context=project_context,
                console=console,
                verbose=verbose,
            )
            try:
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
                            console.print(agent.context.vfs.get_tree())
                            continue

                        elif cmd.startswith("/"):
                            console.print(f"  [yellow]Unknown command:[/yellow] {cmd}  [dim](type /help)[/dim]")
                            continue

                        if user_input.strip():
                            response = await agent.chat_turn(user_input)
                            console.print(f"\n[dim]{response}[/dim]\n")

                    except KeyboardInterrupt:
                        console.print("\n  [dim]Interrupted — type [bold]/exit[/bold] to quit[/dim]")
                    except EOFError:
                        console.print("\n[dim]  Goodbye! 👋[/dim]\n")
                        break
            finally:
                await agent.close()

        asyncio.run(_chat_session())


def _print_help() -> None:
    """Print the in-REPL help table."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("cmd", style="bold cyan", no_wrap=True)
    table.add_column("desc", style="dim white")

    rows = [
        ("/help",   "Show this help message"),
        ("/tree",   "Print the virtual file tree"),
        ("/exit",   "Quit Rudra"),
        ("",        ""),
        ("build",   "rudra build \"<task>\"  — create/enhance a project"),
        ("fix",     "rudra fix \"<issue>\"   — debug and repair"),
        ("edit",    "rudra edit <file> \"<instruction>\""),
        ("review",  "rudra review          — code quality report"),
        ("suggest", "rudra suggest \"<task>\" — propose improvements"),
    ]
    for cmd, desc in rows:
        table.add_row(cmd, desc)

    console.print()
    console.print(Panel(table, title="[bold cyan]Rudra Help[/bold cyan]", border_style="cyan", padding=(1, 2)))
    console.print()


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

@app.command()
def build(
    task: str = typer.Argument(..., help="Task description (e.g., 'Build a FastAPI app with JWT auth')"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files"),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output"),
    max_agents: int = typer.Option(6, "--max-agents", help="Maximum number of sub-agents"),
) -> None:
    """Build a new project or enhance an existing one."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Building project[/bold]\n"
        f"Task: {task}\n"
        f"Path: {project_path}",
        title="🔨 Build",
        border_style="bright_blue",
    ))

    config.agent.max_agents = max_agents
    project_context = load_project_context(project_path)

    async def _run():
        agent = await create_main_agent(
            project_path=project_path,
            task=task,
            project_context=project_context,
            command="build",
            console=console,
            dry_run=dry_run,
            verbose=verbose,
        )
        try:
            return await agent.run()
        finally:
            await agent.close()

    result = asyncio.run(_run())

    if result.success:
        console.print(Panel(
            f"[green]✓[/green] {result.message}\n\n"
            f"Files created:  {len(result.files_created)}\n"
            f"Files modified: {len(result.files_modified)}\n"
            f"Iterations:     {result.iterations}",
            title="✅ Complete",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]✗[/red] {result.message}",
            title="❌ Error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def chat(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output"),
) -> None:
    """Interactive chat mode for ongoing development."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(
        f"  [dim]Working directory:[/dim] [bold white]{project_path}[/bold white]\n"
    )

    project_context = load_project_context(project_path)
    pt_session = _make_prompt_toolkit_session()

    async def _chat_session():
        agent = await create_main_agent(
            project_path=project_path,
            task="",
            command="chat",
            project_context=project_context,
            console=console,
            verbose=verbose,
        )
        try:
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
                        console.print(agent.context.vfs.get_tree())
                        continue
                    elif cmd.startswith("/"):
                        console.print(f"  [yellow]Unknown command:[/yellow] {cmd}  [dim](type /help)[/dim]")
                        continue

                    if user_input.strip():
                        response = await agent.chat_turn(user_input)
                        console.print(f"\n[dim]{response}[/dim]\n")

                except KeyboardInterrupt:
                    console.print("\n  [dim]Interrupted — type [bold]/exit[/bold] to quit[/dim]")
                except EOFError:
                    console.print("\n[dim]  Goodbye! 👋[/dim]\n")
                    break
        finally:
            await agent.close()

    asyncio.run(_chat_session())


@app.command()
def fix(
    issue: str = typer.Argument(..., help="Description of the issue to fix"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Specific file to focus on"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview fixes without applying"),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output"),
) -> None:
    """Debug and fix a specific issue."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Fixing issue[/bold]\n"
        f"Issue: {issue}\n"
        f"Path: {project_path}"
        + (f"\nFile: {file}" if file else ""),
        title="🔧 Fix",
        border_style="yellow",
    ))

    async def _run():
        agent = await create_main_agent(
            project_path=project_path,
            task=issue,
            command="fix",
            console=console,
            dry_run=dry_run,
            verbose=verbose,
            issue=issue,
            file_path=file,
        )
        try:
            return await agent.run()
        finally:
            await agent.close()

    result = asyncio.run(_run())

    if result.success:
        console.print(Panel(f"[green]✓[/green] {result.message}", title="✅ Fixed", border_style="green"))
    else:
        console.print(Panel(f"[red]✗[/red] {result.message}", title="❌ Error", border_style="red"))
        raise typer.Exit(1)


@app.command()
def edit(
    file: str = typer.Argument(..., help="Path to the file to edit"),
    instruction: str = typer.Argument(..., help="What changes to make"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
    preview: bool = typer.Option(False, "--preview", "-p", help="Show diff before applying"),
) -> None:
    """Make targeted edits to a specific file."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Editing file[/bold]\n"
        f"File: {file}\n"
        f"Instruction: {instruction}",
        title="✏️  Edit",
        border_style="magenta",
    ))

    async def _run():
        agent = await create_main_agent(
            project_path=project_path,
            task=instruction,
            command="edit",
            console=console,
            dry_run=preview,
            verbose=False,
            file_path=file,
        )
        try:
            return await agent.run()
        finally:
            await agent.close()

    result = asyncio.run(_run())

    if result.success:
        console.print(Panel(f"[green]✓[/green] {result.message}", title="✅ Edited", border_style="green"))
    else:
        console.print(Panel(f"[red]✗[/red] {result.message}", title="❌ Error", border_style="red"))
        raise typer.Exit(1)


@app.command()
def review(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
    focus: Optional[str] = typer.Option(None, "--focus", "-f", help="Focus area (e.g., 'security', 'performance')"),
) -> None:
    """Review code for quality and issues."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Reviewing project[/bold]\n"
        f"Path: {project_path}"
        + (f"\nFocus: {focus}" if focus else ""),
        title="🔍 Review",
        border_style="bright_blue",
    ))

    task = "Review the codebase for quality, security, and best practices"
    if focus:
        task = f"Review the codebase focusing on {focus}"

    async def _run():
        agent = await create_main_agent(
            project_path=project_path,
            task=task,
            command="review",
            console=console,
            dry_run=True,
            verbose=True,
        )
        try:
            return await agent.run()
        finally:
            await agent.close()

    result = asyncio.run(_run())

    console.print(Panel(
        result.todo_summary if result.success else result.message,
        title="📋 Review Complete",
        border_style="bright_blue",
    ))


@app.command()
def suggest(
    task: str = typer.Argument(..., help="What improvement to suggest"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
) -> None:
    """Suggest improvements without applying them."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Suggesting improvements[/bold]\n"
        f"Task: {task}\n"
        f"Path: {project_path}",
        title="💡 Suggest",
        border_style="cyan",
    ))

    async def _run():
        agent = await create_main_agent(
            project_path=project_path,
            task=task,
            command="suggest",
            console=console,
            dry_run=True,
            verbose=True,
        )
        try:
            return await agent.run()
        finally:
            await agent.close()

    result = asyncio.run(_run())

    console.print(Panel(
        result.todo_summary if result.success else result.message,
        title="💡 Suggestions",
        border_style="cyan",
    ))


@app.command()
def resume(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
) -> None:
    """Resume a previous session (automatic — just re-run in the same directory)."""
    project_path = get_project_path(project_dir)
    session_file = project_path / ".rudra" / "session_id.txt"

    print_banner()
    if session_file.exists():
        session_id = session_file.read_text().strip()
        console.print(Panel(
            f"[green]Session ID:[/green] {session_id}\n"
            f"[green]Checkpoint DB:[/green] {project_path / '.rudra' / 'checkpoints.db'}\n\n"
            "Run any command in this directory to continue:\n"
            "  [bold]rudra build \"continue the work\"[/bold]\n"
            "  [bold]rudra chat[/bold]",
            title="▶️  Resume is Automatic",
            border_style="green",
        ))
    else:
        console.print(Panel(
            "No session found in this directory yet.\n"
            "Start one with: [bold]rudra build \"your task\"[/bold]",
            title="▶️  No Session Found",
            border_style="yellow",
        ))


@app.command()
def watch(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d", help="Project directory"),
    auto_apply: bool = typer.Option(False, "--auto-apply", help="Automatically apply suggested fixes (risky)"),
) -> None:
    """Watch project for changes and suggest fixes."""
    project_path = get_project_path(project_dir)

    print_banner()
    console.print(Panel(
        f"[bold]Watching project[/bold]\n"
        f"Path: {project_path}\n\n"
        "Press Ctrl+C to stop watching.",
        title="👁️  Watch",
        border_style="cyan",
    ))

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        console.print("[red]watchdog not installed. Run: pip install watchdog[/red]")
        raise typer.Exit(1)

    class ChangeHandler(FileSystemEventHandler):
        def __init__(self):
            self.vfs = VirtualFileSystem(project_path)
            self.vfs.load_gitignore()

        def on_modified(self, event):
            if event.is_directory:
                return
            try:
                rel_path = str(Path(event.src_path).relative_to(project_path))
                if self.vfs._is_ignored(rel_path):
                    return
            except ValueError:
                return

            console.print(f"[dim]Modified: {rel_path}[/dim]")

            if rel_path.endswith(".py"):
                from rudra.tools.code_tools import create_code_tools
                tools = create_code_tools(self.vfs)
                for tool in tools:
                    if tool.name == "check_syntax":
                        result = tool.invoke({"path": rel_path})
                        if "Error" in result:
                            console.print(f"[yellow]⚠ {result}[/yellow]")
                        break

    handler = ChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(project_path), recursive=True)
    observer.start()

    try:
        while True:
            asyncio.run(asyncio.sleep(1))
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[dim]Stopped watching[/dim]")

    observer.join()


# Entry point
if __name__ == "__main__":
    app()
