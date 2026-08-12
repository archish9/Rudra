"""Rudra CLI - Autonomous Coding Agent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import click
import typer
from rich.console import Console
from rich.markup import escape
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
        None, "--role", help="Probe one role only (default: every distinct endpoint)"
    ),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Verify every configured model is reachable and can call tools.

    deepagents requires tool calling for every agent, and reachability does
    not imply it — hence the separate Tools stage. See TODO.md C1.6.

    Probes distinct endpoints rather than roles: Step 9b took BUILTIN_ROLES
    to five, and a single-model setup would otherwise fire five identical
    network calls.
    """
    from rudra.llm.probe import probe_role, roles_to_probe

    cfg = _load_config_or_exit(project_dir)
    entries = [((role,), cfg.models[role])] if role else roles_to_probe(cfg)

    table = Table(title="Model check", header_style="bold")
    for column in ("Roles", "Provider", "Model", "Construct", "Reach", "Tools", "Ctx"):
        table.add_column(column, overflow="fold")

    failed = False
    for roles, _model in entries:
        # One probe per distinct endpoint; the Roles column says who shares it.
        result = probe_role(roles[0])
        failed = failed or not result.ok
        style = "green" if result.ok else "red"
        table.add_row(
            ", ".join(roles),
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


EXIT_NO_TTY = 2

_MODE_DESCRIPTIONS = {
    "ask": "prompting before each write, edit, delete, or command",
    "auto": "approving everything without prompting",
    "plan": "making no project changes and running no commands",
}


def _permission_notice(cfg) -> str:
    """One line, shown on every run — not only when a flag is passed.

    Step 6 shipped this saying NOT ENFORCED, deliberately: an inert default
    of `ask` claims more safety than you get. Step 7 enforces it, so the
    line now describes what the mode actually does.
    """
    described = _MODE_DESCRIPTIONS.get(cfg.permissions.mode, cfg.permissions.mode)
    line = f"permissions: {cfg.permissions.mode} — {described}"
    if cfg.permissions.floor_disable:
        line += f" · floor disabled: {', '.join(cfg.permissions.floor_disable)}"
    return line


def _load_config_or_exit(project_dir: Optional[Path]):
    """Build a Config, turning ConfigError into a clean message.

    A malformed config file is a user error, not a crash — never show a
    traceback for one.
    """
    from rudra.config import ConfigError, build_config

    try:
        return build_config(get_project_path(project_dir))
    except ConfigError as exc:
        # escape() because these messages are built around bracketed section
        # names ("[tools] shell must be true or false") and Rich would parse
        # every one of them as a style tag, printing the error without the
        # section it exists to identify. See TODO.md A1.48.
        console.print(f"[red]Configuration error:[/red] {escape(str(exc))}")
        raise typer.Exit(code=1) from None


def _flatten(cfg) -> list[tuple[str, object]]:
    """Every effective leaf as a dotted key, in a stable order.

    Derived from each dataclass's own fields rather than listed by hand.
    The hand-written version claimed this same completeness and did not
    have it: `[tools] auto_branch` and `test_timeout` were accepted by the
    loader, honoured at run time, and never printed (TODO.md A1.54). Since
    `config list` is the only way to see which layer set a value — there is
    no `config set` by design (S6.1) — under-reporting is worse than it
    looks. Adding the two missing lines would have closed the instance and
    left the defect, so the enumeration is now structural.
    """
    from dataclasses import fields

    rows: list[tuple[str, object]] = []
    for role in sorted(cfg.models):
        model = cfg.models[role]
        for field in fields(model):
            rows.append((f"model.{role}.{field.name}", getattr(model, field.name)))

    for section in ("agent", "permissions", "tools", "compat"):
        block = getattr(cfg, section)
        for field in fields(block):
            value = getattr(block, field.name)
            # Tuples are an implementation detail of the frozen dataclasses;
            # the user wrote a TOML array and should see one back.
            rows.append(
                (f"{section}.{field.name}", list(value) if isinstance(value, tuple) else value)
            )
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


@app.command("doctor")
def doctor_command(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    offline: bool = typer.Option(
        False, "--offline", help="Skip model reachability checks (no network calls)"
    ),
) -> None:
    """Diagnose configuration, layout, and model reachability."""
    from importlib.metadata import version

    from rudra.compat.version_guard import EXPECTED_DEEPAGENTS_VERSION
    from rudra.state.paths import rudra_paths

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    paths = rudra_paths(project_path)

    table = Table(title="rudra doctor", header_style="bold")
    for column in ("Check", "Status", "Detail"):
        table.add_column(column, overflow="fold")

    table.add_row("project", "ok", str(project_path))
    for layer in ("user", "project"):
        path = cfg.sources.get(layer)
        table.add_row(
            f"config ({layer})", "ok" if path else "-", str(path) if path else "not present"
        )

    dotenv = project_path / ".env"
    table.add_row(
        ".env",
        "ok" if dotenv.exists() else "-",
        str(dotenv) if dotenv.exists() else "not present",
    )

    table.add_row(
        ".rudra layout",
        "ok" if paths.run.is_dir() else "missing",
        str(paths.root) if paths.run.is_dir() else "run `rudra init`",
    )

    stale = paths.root / "checkpoints.db"
    if stale.exists():
        table.add_row(
            "stale checkpoints.db",
            "warn",
            f"{stale} predates the run/ layout and is unused — safe to delete",
        )

    installed = version("deepagents")
    table.add_row(
        "deepagents",
        "ok" if installed == EXPECTED_DEEPAGENTS_VERSION else "warn",
        f"{installed} (pinned {EXPECTED_DEEPAGENTS_VERSION})",
    )

    floor_off = cfg.permissions.floor_disable
    table.add_row(
        "permissions",
        "warn" if floor_off else "ok",
        f"mode = {cfg.permissions.mode} — {_MODE_DESCRIPTIONS.get(cfg.permissions.mode, '')}. "
        f"{len(cfg.permissions.allow)} allow, {len(cfg.permissions.deny)} deny. "
        f"Deny floor: {'disabled ' + ', '.join(floor_off) if floor_off else 'all rules active'}.",
    )

    # escape() for the same reason as the config-error path: Rich parses
    # "[tools]" as a style tag and prints nothing where the section name
    # should be. See TODO.md A1.48.
    if not cfg.tools.shell:
        shell_state = "disabled by [tools] shell = false — the execute tool is absent"
    elif cfg.tools.shell_in_auto:
        shell_state = (
            "enabled, including under --auto ([tools] shell_in_auto = true). "
            "Unattended commands are not confined to the project — "
            "see Documentation/09-permissions.md"
        )
    else:
        shell_state = (
            "enabled in ask mode; disabled under --auto unless --allow-shell "
            "or [tools] shell_in_auto = true"
        )
    table.add_row("shell", "warn" if cfg.tools.shell_in_auto else "ok", escape(shell_state))

    if not offline:
        from rudra.llm.probe import probe_role, roles_to_probe

        # Distinct endpoints, not roles — same reason as `models test`.
        for roles, _model in roles_to_probe(cfg):
            result = probe_role(roles[0])
            table.add_row(
                f"model ({', '.join(roles)})",
                "ok" if result.ok else "fail",
                f"{result.provider} {result.model} — {result.reach}, tools: {result.tools}",
            )

    console.print(table)
    console.print(
        "[dim]MCP servers, skills, and memory are not checked — they arrive in "
        "Steps 13, 11, and 14.[/dim]"
    )


@app.command("verify")
def verify_command(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    scan_all: bool = typer.Option(
        False, "--all", help="Scan every source file, not just the ones git reports as changed"
    ),
    changed: Optional[list[Path]] = typer.Option(
        None, "--changed", help="Scope the stub scan to these files (repeatable)"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Run the deterministic verification gate: syntax, lint, typecheck, tests, stubs."""
    import json as json_module

    from rudra.permissions import build_gate
    from rudra.state.paths import ensure_layout
    from rudra.verify import (
        changed_files_from_git,
        exit_code,
        render,
        source_files,
        to_dict,
        verify_project,
    )

    if scan_all and changed:
        # Silently letting one win would make the report's scope a guess.
        console.print("[red]--all and --changed are mutually exclusive.[/red]")
        raise typer.Exit(code=2)

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    ensure_layout(project_path)
    gate = build_gate(cfg, project_path)
    quiet = Console(quiet=True) if as_json else console

    if changed:
        files: tuple[str, ...] = tuple(str(path) for path in changed)
    elif scan_all:
        files = source_files(project_path)
    else:
        from_git = changed_files_from_git(project_path, gate=gate, console=quiet, cfg=cfg)
        if from_git is None:
            # Not a repo. Scanning nothing would report a clean stub stage
            # over an unexamined tree, so scan everything and say so.
            files = source_files(project_path)
            if not as_json:
                console.print("[dim]Not a git repository — scanning every source file.[/dim]")
        else:
            files = from_git

    report = verify_project(project_path, changed_files=files, gate=gate, console=quiet, cfg=cfg)

    if as_json:
        console.print_json(json_module.dumps(to_dict(report)))
    else:
        render(report, console)

    raise typer.Exit(code=exit_code(report))


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
    auto: bool = typer.Option(
        False, "--auto", "--yolo", help="Approve every action without prompting"
    ),
    plan: bool = typer.Option(
        False, "--plan", help="Plan only: make no project changes, run no commands"
    ),
    allow_shell: bool = typer.Option(
        False,
        "--allow-shell",
        help="Let --auto run commands too (off by default: nobody reads them first)",
    ),
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
    permission_mode = "auto" if auto else "plan" if plan else None
    cfg = get_config(
        project_path,
        verbose=verbose,
        permission_mode=permission_mode,
        allow_shell=True if allow_shell else None,
    )

    from rudra.permissions import disabled_floor_notice, stdin_is_interactive

    # Before load_project_context, before ensure_layout, before any model.
    # In ask mode every write is gated and writing files is Rudra's whole
    # job, so a non-TTY ask run hits a prompt within seconds — failing at
    # second zero is the honest version of failing at second thirty, and it
    # leaves nothing behind on disk. See the Step 7 design spec §6.5.
    if cfg.permissions.mode == "ask" and not stdin_is_interactive():
        console.print(
            '[red]Error:[/red] permissions.mode = "ask" needs an interactive '
            "terminal, but stdin is not a TTY.\n\n"
            "  --auto                      approve everything (unattended)\n"
            '  permissions.mode = "auto"   same, persisted in .rudra/config.toml'
        )
        raise typer.Exit(EXIT_NO_TTY)

    floor_notice = disabled_floor_notice(cfg)
    if floor_notice:
        console.print(f"[yellow]Warning:[/yellow] {floor_notice}")

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
                f"[dim]Planner:[/dim] {cfg.model_for('planner').model}  "
                f"[dim]│  Coder:[/dim] {cfg.model_for('coder').model}\n"
                f"[yellow]{_permission_notice(cfg)}[/yellow]",
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
                            f"[dim]Planner:[/dim] {cfg.model_for('planner').model}  "
                            f"[dim]│  Coder:[/dim] {cfg.model_for('coder').model}\n"
                            f"[yellow]{_permission_notice(cfg)}[/yellow]",
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
