"""Rudra CLI - Autonomous Coding Agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import click
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from typer.core import TyperGroup

from rudra import __version__
from rudra.agent import AgentResult, create_main_agent
from rudra.config import get_config
from rudra.filesystem import project_tree
from rudra.state import ProjectConfigManager, ProjectContext


class TaskOrCommandGroup(TyperGroup):
    """Let a bare first argument be a task prompt, not a command name.

    Rudra's primary interface is `rudra "<task>"`, but click resolves the
    first positional token as a subcommand name and fails on anything it
    does not recognize. Declaring the prompt as a positional Argument on the
    callback instead makes the opposite trade — the callback consumes the
    token before command resolution runs, so `rudra models` launches an
    agent run for a task called "models" and `rudra models test` reports
    "No such command 'test'".

    Both forms have to work, so command resolution stays in charge and only
    unrecognized tokens fall through to the callback as ctx.args.

    `_protected_args` is click-internal (8.3.1: Group.parse_args splits
    `rest` into `ctx._protected_args` and `ctx.args`). It is read
    defensively and covered by tests that exercise both invocation forms
    end to end, so a click upgrade that renames it fails visibly.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        parsed = super().parse_args(ctx, args)
        protected = getattr(ctx, "_protected_args", [])
        if protected and protected[0] not in self.commands:
            ctx.args = [*protected, *ctx.args]
            ctx._protected_args = []
            return ctx.args
        return parsed


# Create the Typer app
app = typer.Typer(
    name="rudra",
    help="Rudra - Autonomous Coding Agent CLI",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    cls=TaskOrCommandGroup,
)

models_app = typer.Typer(help="Inspect and test configured models.")
app.add_typer(models_app, name="models")

config_app = typer.Typer(help="Inspect effective configuration (read-only).")
app.add_typer(config_app, name="config")

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


def _load_config_or_exit(project_dir: Optional[Path]):
    """Build a Config, turning ConfigError into a clean message.

    A malformed config file is a user error, not a crash — never show a
    traceback for one.
    """
    from rudra.config import ConfigError, build_config

    try:
        return build_config(get_project_path(project_dir))
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from None


def _flatten(cfg) -> list[tuple[str, object]]:
    """Every effective leaf as a dotted key, in a stable order."""
    from dataclasses import asdict

    rows: list[tuple[str, object]] = []
    for role in sorted(cfg.models):
        for key, value in asdict(cfg.models[role]).items():
            rows.append((f"model.{role}.{key}", value))
    rows.append(("agent.verbose", cfg.agent.verbose))
    rows.append(("permissions.mode", cfg.permissions.mode))
    rows.append(("permissions.allow", list(cfg.permissions.allow)))
    rows.append(("permissions.deny", list(cfg.permissions.deny)))
    rows.append(("compat.task_anchor", cfg.compat.task_anchor))
    rows.append(("compat.sandbox_paths", cfg.compat.sandbox_paths))
    return rows


def _source_of(cfg, key: str) -> str:
    """Which layer set this key.

    A role that inherits a value has no provenance entry of its own, so the
    lookup falls back to the default role's — otherwise every inherited key
    would misreport as 'builtin'.
    """
    source = cfg.provenance.get(key)
    if source is None and key.startswith("model."):
        _, _, leaf = key.split(".", 2)
        source = cfg.provenance.get(f"model.default.{leaf}")
    return source or "builtin"


@config_app.command("list")
def config_list(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    role: Optional[str] = typer.Option(None, "--role", help="Show one model role only"),
) -> None:
    """Show every effective value and the layer that set it."""
    cfg = _load_config_or_exit(project_dir)

    table = Table(title="Effective configuration", header_style="bold")
    for column in ("Key", "Value", "Source"):
        table.add_column(column, overflow="fold")

    for key, value in _flatten(cfg):
        if role and key.startswith("model.") and not key.startswith(f"model.{role}."):
            continue
        table.add_row(key, "-" if value is None else str(value), _source_of(cfg, key))

    console.print(table)
    for layer in ("user", "project"):
        path = cfg.sources.get(layer)
        console.print(f"[dim]{layer:>8}:[/dim] {path or '(none)'}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Dotted key, e.g. model.planner.model"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Print one value and where it came from."""
    cfg = _load_config_or_exit(project_dir)
    for candidate, value in _flatten(cfg):
        if candidate == key:
            shown = "-" if value is None else value
            console.print(f"{shown}  [dim](from {_source_of(cfg, key)})[/dim]")
            return
    console.print(f"[red]Unknown key '{key}'.[/red] Run `rudra config list` to see valid keys.")
    raise typer.Exit(code=1)


@app.command("init")
def init_command(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    global_: bool = typer.Option(
        False, "--global", help="Write ~/.config/rudra/config.toml instead of the project file"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file"),
) -> None:
    """Scaffold a commented config.toml and create the .rudra/ layout."""
    from rudra.config import user_toml_path
    from rudra.config.template import CONFIG_TEMPLATE
    from rudra.state.paths import ensure_layout

    if global_:
        target = user_toml_path()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = ensure_layout(get_project_path(project_dir)).config_toml

    if target.exists() and not force:
        console.print(f"[red]{target} already exists.[/red] Pass --force to overwrite it.")
        raise typer.Exit(code=1)

    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {target}")
    console.print("[dim]Edit it, then run `rudra models test` to check your model.[/dim]")


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
