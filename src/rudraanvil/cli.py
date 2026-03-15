"""RudraAnvil CLI - Autonomous Coding Agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from rudraanvil import __version__
from rudraanvil.agent import create_main_agent
from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem, FileSyncManager, SyncMode
from rudraanvil.state import CheckpointManager, TodoList, ProjectConfigManager, ProjectContext

# Create the Typer app
app = typer.Typer(
    name="rudraanvil",
    help="RudraAnvil - Autonomous Coding Agent CLI",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print(f"[bold]RudraAnvil[/bold] v{__version__}")
        raise typer.Exit()


def get_project_path(project_dir: Optional[Path]) -> Path:
    """Get the project path, defaulting to current directory."""
    if project_dir:
        return project_dir.resolve()
    return Path.cwd()


def get_or_prompt_project_context(project_path: Path) -> ProjectContext:
    """Load project context or interactively prompt for missing details."""
    config_manager = ProjectConfigManager(project_path)
    context = config_manager.load()
    
    needs_save = False
    
    if not context.primary_language:
        console.print("\n[bold yellow]Project Context Required[/bold yellow]")
        console.print("To help the agent write the best code, please provide some details about your stack.")
        
        context.primary_language = Prompt.ask(
            "[cyan]Primary Programming Language[/cyan] (e.g., Python, TypeScript, Go)"
        )
        context.framework = Prompt.ask(
            "[cyan]Frameworks/Libraries[/cyan] (e.g., FastAPI, Next.js, React) [dim](optional)[/dim]", 
            default=""
        )
        context.database = Prompt.ask(
            "[cyan]Database[/cyan] (e.g., PostgreSQL, MongoDB) [dim](optional)[/dim]", 
            default=""
        )
        context.additional_context = Prompt.ask(
            "[cyan]Any additional architecture rules or context?[/cyan] [dim](optional)[/dim]", 
            default=""
        )
        needs_save = True
        
    if needs_save:
        config_manager.save(context)
        console.print("[green]✓ Project context saved for future tasks.[/green]\n")
        
    return context


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """RudraAnvil - Autonomous Coding Agent CLI.
    
    Build, debug, and maintain software projects with an AI-powered agent.
    """
    pass


@app.command()
def build(
    task: str = typer.Argument(..., help="Task description (e.g., 'Build a FastAPI app with JWT auth')"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files"),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output (default: VERBOSE in .env)"),
    max_agents: int = typer.Option(6, "--max-agents", help="Maximum number of sub-agents"),
) -> None:
    """Build a new project or enhance an existing one.
    
    This is the primary command for creating and modifying projects.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Building project[/bold]\n"
        f"Task: {task}\n"
        f"Path: {project_path}",
        title="🔨 RudraAnvil",
        border_style="blue",
    ))
    
    # Update config
    config.agent.max_agents = max_agents
    
    # Get interactive context
    project_context = get_or_prompt_project_context(project_path)
    
    # Create and run agent
    agent = create_main_agent(
        project_path=project_path,
        task=task,
        project_context=project_context,
        command="build",
        console=console,
        dry_run=dry_run,
        verbose=verbose,
    )
    
    result = asyncio.run(agent.run())
    
    # Show result
    if result.success:
        console.print(Panel(
            f"[green]✓[/green] {result.message}\n\n"
            f"Files created: {len(result.files_created)}\n"
            f"Files modified: {len(result.files_modified)}\n"
            f"Iterations: {result.iterations}",
            title="Complete",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]✗[/red] {result.message}",
            title="Error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def chat(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output (default: VERBOSE in .env)"),
) -> None:
    """Interactive chat mode for ongoing development.
    
    Enter a REPL where you can have a conversation with the agent.
    Type '/exit' or '/quit' to leave.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Chat mode[/bold]\n"
        f"Path: {project_path}\n\n"
        "Type your request and press Enter.\n"
        "Commands: /exit, /status, /save, /tree",
        title="💬 RudraAnvil Chat",
        border_style="cyan",
    ))
    
    # Get interactive context
    project_context = get_or_prompt_project_context(project_path)
    
    # Create agent for chat mode
    agent = create_main_agent(
        project_path=project_path,
        task="",
        command="chat",
        project_context=project_context,
        console=console,
        verbose=verbose,
    )
    
    # Chat loop
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]rudraanvil[/bold cyan]")
            
            # Handle commands
            cmd = user_input.strip().lower()
            if cmd in ("/exit", "/quit", "exit", "quit"):
                console.print("[dim]Goodbye![/dim]")
                break
            
            elif cmd == "/status":
                console.print(agent.context.todo_list.summary())
                continue
            
            elif cmd == "/save":
                agent.context.checkpoint_manager.save()
                console.print("[green]Session saved[/green]")
                continue
            
            elif cmd == "/tree":
                console.print(agent.context.vfs.get_tree())
                continue
            
            elif cmd.startswith("/"):
                console.print(f"[yellow]Unknown command: {cmd}[/yellow]")
                continue
            
            # Process with agent
            if user_input.strip():
                response = asyncio.run(agent.chat_turn(user_input))
                console.print(f"\n[dim]{response}[/dim]")
        
        except KeyboardInterrupt:
            console.print("\n[dim]Use /exit to quit[/dim]")
        except EOFError:
            break
    
    # Save on exit
    agent.context.checkpoint_manager.save()


@app.command()
def fix(
    issue: str = typer.Argument(..., help="Description of the issue to fix"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Specific file to focus on"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview fixes without applying"),
    verbose: bool = typer.Option(config.agent.verbose, "--verbose/--no-verbose", "-V", help="Show detailed output (default: VERBOSE in .env)"),
) -> None:
    """Debug and fix a specific issue.
    
    Focuses on diagnosing and repairing bugs or errors.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Fixing issue[/bold]\n"
        f"Issue: {issue}\n"
        f"Path: {project_path}"
        + (f"\nFile: {file}" if file else ""),
        title="🔧 RudraAnvil Fix",
        border_style="yellow",
    ))
    
    agent = create_main_agent(
        project_path=project_path,
        task=issue,
        command="fix",
        console=console,
        dry_run=dry_run,
        verbose=verbose,
        issue=issue,
        file_path=file,
    )
    
    result = asyncio.run(agent.run())
    
    if result.success:
        console.print(Panel(
            f"[green]✓[/green] {result.message}",
            title="Fixed",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]✗[/red] {result.message}",
            title="Error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def edit(
    file: str = typer.Argument(..., help="Path to the file to edit"),
    instruction: str = typer.Argument(..., help="What changes to make"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
    preview: bool = typer.Option(False, "--preview", "-p", help="Show diff before applying"),
) -> None:
    """Make targeted edits to a specific file.
    
    Use this for precise modifications without rebuilding.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Editing file[/bold]\n"
        f"File: {file}\n"
        f"Instruction: {instruction}",
        title="✏️ RudraAnvil Edit",
        border_style="magenta",
    ))
    
    agent = create_main_agent(
        project_path=project_path,
        task=instruction,
        command="edit",
        console=console,
        dry_run=preview,
        verbose=False,
        file_path=file,
    )
    
    result = asyncio.run(agent.run())
    
    if result.success:
        console.print(Panel(
            f"[green]✓[/green] {result.message}",
            title="Edited",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]✗[/red] {result.message}",
            title="Error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def review(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
    focus: Optional[str] = typer.Option(
        None, "--focus", "-f", help="Focus area (e.g., 'security', 'performance')"
    ),
) -> None:
    """Review code for quality and issues.
    
    Analyzes the project and outputs a report without making changes.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Reviewing project[/bold]\n"
        f"Path: {project_path}"
        + (f"\nFocus: {focus}" if focus else ""),
        title="🔍 RudraAnvil Review",
        border_style="blue",
    ))
    
    task = "Review the codebase for quality, security, and best practices"
    if focus:
        task = f"Review the codebase focusing on {focus}"
    
    agent = create_main_agent(
        project_path=project_path,
        task=task,
        command="review",
        console=console,
        dry_run=True,  # Review doesn't write files
        verbose=True,
    )
    
    result = asyncio.run(agent.run())
    
    # Output is the review itself
    console.print(Panel(
        result.todo_summary if result.success else result.message,
        title="Review Complete",
        border_style="blue",
    ))


@app.command()
def suggest(
    task: str = typer.Argument(..., help="What improvement to suggest"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
) -> None:
    """Suggest improvements without applying them.
    
    Proposes enhancements with code snippets and diffs for review.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Suggesting improvements[/bold]\n"
        f"Task: {task}\n"
        f"Path: {project_path}",
        title="💡 RudraAnvil Suggest",
        border_style="cyan",
    ))
    
    agent = create_main_agent(
        project_path=project_path,
        task=task,
        command="suggest",
        console=console,
        dry_run=True,  # Suggestions don't write files
        verbose=True,
    )
    
    result = asyncio.run(agent.run())
    
    console.print(Panel(
        result.todo_summary if result.success else result.message,
        title="Suggestions",
        border_style="cyan",
    ))


@app.command()
def resume(
    session_id: Optional[str] = typer.Argument(None, help="Session ID to resume"),
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
    list_sessions: bool = typer.Option(
        False, "--list", "-l", help="List available sessions"
    ),
) -> None:
    """Resume an interrupted session.
    
    Picks up a paused or interrupted task from a checkpoint.
    """
    project_path = get_project_path(project_dir)
    checkpoint_dir = project_path / ".rudraanvil"
    checkpoint_manager = CheckpointManager(checkpoint_dir)
    
    # List sessions if requested
    if list_sessions or session_id is None:
        sessions = checkpoint_manager.list_sessions()
        
        if not sessions:
            console.print("[yellow]No saved sessions found[/yellow]")
            raise typer.Exit()
        
        table = Table(title="Available Sessions")
        table.add_column("Session ID", style="cyan")
        table.add_column("Task", style="white")
        table.add_column("Updated", style="dim")
        table.add_column("Iterations", style="green")
        
        for session in sessions:
            table.add_row(
                session["session_id"],
                session["task"],
                session["updated_at"],
                str(session["iterations"]),
            )
        
        console.print(table)
        
        if session_id is None:
            raise typer.Exit()
    
    # Resume the session
    try:
        checkpoint = checkpoint_manager.load(session_id)
    except FileNotFoundError:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
    
    console.print(Panel(
        f"[bold]Resuming session[/bold]\n"
        f"Session: {session_id}\n"
        f"Task: {checkpoint.task_description}\n"
        f"Iterations completed: {checkpoint.iterations_completed}",
        title="▶️ RudraAnvil Resume",
        border_style="green",
    ))
    
    # Restore state and continue
    agent = create_main_agent(
        project_path=project_path,
        task=checkpoint.task_description,
        command="build",
        console=console,
    )
    
    # Restore todo list and VFS from checkpoint
    from rudraanvil.state.todo import TodoList
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem
    
    agent.context.todo_list = TodoList.from_dict(checkpoint.todo_list)
    if checkpoint.virtual_fs:
        agent.context.vfs = VirtualFileSystem.from_dict(checkpoint.virtual_fs)
    agent.iterations = checkpoint.iterations_completed
    
    result = asyncio.run(agent.run())
    
    if result.success:
        console.print(Panel(
            f"[green]✓[/green] {result.message}",
            title="Complete",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[red]✗[/red] {result.message}",
            title="Error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def watch(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory"
    ),
    auto_apply: bool = typer.Option(
        False, "--auto-apply", help="Automatically apply suggested fixes (risky)"
    ),
) -> None:
    """Watch project for changes and suggest fixes.
    
    Monitors the directory and provides real-time assistance.
    """
    project_path = get_project_path(project_dir)
    
    console.print(Panel(
        f"[bold]Watching project[/bold]\n"
        f"Path: {project_path}\n\n"
        "Press Ctrl+C to stop watching.",
        title="👁️ RudraAnvil Watch",
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
            
            # Skip ignored files
            try:
                rel_path = str(Path(event.src_path).relative_to(project_path))
                if self.vfs._is_ignored(rel_path):
                    return
            except ValueError:
                return
            
            console.print(f"[dim]Modified: {rel_path}[/dim]")
            
            # Check for common issues
            if rel_path.endswith(".py"):
                from rudraanvil.tools.code_tools import create_code_tools
                tools = create_code_tools(self.vfs)
                
                # Find lint tool
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
