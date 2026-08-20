"""Rudra CLI - Autonomous Coding Agent."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import click
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from typer.core import TyperGroup

from rudra import __version__
from rudra.cli_repl import REPL_COMMANDS, build_session, expand_mentions
from rudra.config import get_config
from rudra.context.usage import render_usage
from rudra.filesystem import project_tree

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from rudra.agent import AgentResult

# `rudra.agent` is NOT imported here (C9.8 / A1.94). That one line pulled
# deepagents, langchain.agents and a provider package into every
# invocation -- `--version`, `--help`, `config list`, shell completion --
# and measured 0.58s of it. It is imported inside the two command bodies
# that build an agent instead. `AgentResult` is used only in annotations,
# which `from __future__ import annotations` (line 3) leaves unevaluated.
# tests/test_cli_startup_imports.py asserts the absence on the import
# graph rather than on the clock.


async def create_main_agent(*args: Any, **kwargs: Any) -> Any:
    """The factory, imported at call time rather than at import time.

    A proxy rather than a local import at each call site, for two reasons:
    the call sites stay readable, and `rudra.cli.create_main_agent`
    remains a patchable name -- four test modules monkeypatch it, and a
    function-local import would have made the seam disappear while
    pretending the tests still covered it.
    """
    from rudra.agent import create_main_agent as _factory

    return await _factory(*args, **kwargs)


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

skills_app = typer.Typer(help="Inspect and validate the skill library.")
app.add_typer(skills_app, name="skills")

mcp_app = typer.Typer(help="Configure and check MCP servers.")
app.add_typer(mcp_app, name="mcp")

memory_app = typer.Typer(help="Inspect and prune this project's long-term memory.")
app.add_typer(memory_app, name="memory")

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


async def _prompt_input(session, prompt_text: str) -> str:
    """Read one line from the user, using prompt_toolkit if available.

    Uses prompt_async() so it cooperates with the already-running asyncio loop
    instead of trying to nest a second asyncio.run() call inside it.
    """
    if session is not None:
        from prompt_toolkit.formatted_text import HTML

        from rudra.cli_repl import wants_more_input

        # prompt_async is awaitable — safe to call inside a running event loop
        line = await session.prompt_async(HTML(f"<prompt>{prompt_text}</prompt> "))
        # A trailing backslash continues, shell-style. Kept here rather
        # than in prompt_toolkit's own multiline mode because that makes
        # Enter ambiguous for every ordinary one-line prompt, which is
        # nearly all of them.
        while wants_more_input(line):
            more = await session.prompt_async(HTML("<prompt>...</prompt> "))
            line = line[:-1] + "\n" + more
        return line
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


EXIT_CANCELLED = 130
"""128 + SIGINT, the shell convention. A wrapper script can tell "the user
stopped it" from "it failed" without reading the output."""

_HARD_EXIT_WINDOW = 2.0
"""Seconds within which a second Ctrl-C stops being polite. A cancel that
itself hangs must not need a kill from another terminal."""


@contextmanager
def _cancel_on_sigint(task: asyncio.Task) -> Iterator[Callable[[], None]]:
    """Route Ctrl-C to `task.cancel()` for as long as this is entered.

    Yields the handler so a test can press the button without raising a
    real signal; the second-press window is otherwise untestable without
    depending on how pytest's own SIGINT handling interleaves with the
    loop.

    Installed per turn and removed on exit: the REPL builds a fresh agent
    per input (S15.4), so a handler left behind would cancel the NEXT
    turn's task rather than this one's.

    `add_signal_handler` rather than `signal.signal`, because the default
    handler raises KeyboardInterrupt on whatever frame the interpreter is
    executing -- inside an event loop that means the exception surfaces
    wherever it lands, including inside the checkpointer's own await.
    Cancelling the task instead delivers it at an await point the loop
    chose, which is what makes work()'s task-boundary catch reliable.
    """
    import time

    pressed: list[float] = []

    def handle() -> None:
        now = time.monotonic()
        if pressed and now - pressed[-1] < _HARD_EXIT_WINDOW:
            # The graceful path did not take. os._exit, not sys.exit:
            # SystemExit would be caught by the loop and become another
            # hang, which is the thing the user is trying to escape.
            os._exit(EXIT_CANCELLED)
        pressed.append(now)
        console.print("\n[yellow]Stopping after this task — press Ctrl-C again to force.[/yellow]")
        task.cancel()

    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, handle)
    except NotImplementedError:
        # Windows has no add_signal_handler. Ctrl-C keeps its default
        # meaning there rather than pretending to be graceful.
        yield handle
        return
    try:
        yield handle
    finally:
        loop.remove_signal_handler(signal.SIGINT)


EXIT_NO_TTY = 2
# A --continue that cannot proceed. Same code as the TTY refusal: both
# are "the run did not start, and here is the sentence why".
EXIT_RESUME_REFUSED = 2

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


@skills_app.command("list")
def skills_list(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Show every skill Rudra can see, and whether it is in the prompt.

    Three columns carry the whole model: where a skill came from, whether
    it reaches the model, and — the question that would otherwise generate
    bug reports — why one exists and does nothing.
    """
    from rudra.skills.cache import ensure_cache
    from rudra.skills.registry import BUNDLES
    from rudra.skills.sources import resolve_sources
    from rudra.skills.validate import validate_tree

    cfg = _load_config_or_exit(project_dir)
    root = Path(project_dir) if project_dir else Path.cwd()
    enabled = frozenset(cfg.skills.enabled)
    cache = ensure_cache(BUNDLES, enabled) if enabled else None
    sources = resolve_sources(root, cache)

    # Walk in precedence order and let the last source own the name, which
    # is the rule SkillsMiddleware applies (skills.py:955).
    owner: dict[str, str] = {}
    rows: list[tuple[str, str, str]] = []
    for source in sources:
        if source.label == "bundled":
            trees = sorted((source.local_path / "library").iterdir())
        else:
            trees = [source.local_path]
        for tree in trees:
            for finding in validate_tree(tree):
                # An invalid skill is NOT in the prompt, whatever `enabled`
                # says: SkillsMiddleware skips it silently. Reporting "yes"
                # here would make this command lie in exactly the case it
                # exists to explain.
                if not finding.ok:
                    state = "invalid"
                elif source.label != "bundled" and finding.name not in enabled:
                    state = "yes"
                else:
                    state = "yes" if finding.name in enabled else "—"
                rows.append((finding.name, source.label, state))
                owner[finding.name] = source.label

    table = Table(title="Skills", header_style="bold")
    for column in ("Skill", "Source", "In prompt", "Note"):
        table.add_column(column, overflow="fold")
    for name, label, state in rows:
        shadowed = owner[name] != label
        table.add_row(
            escape(name),
            label,
            "" if shadowed else state,
            f"shadowed by {owner[name]}" if shadowed else "",
        )
    console.print(table)


@skills_app.command("validate")
def skills_validate(
    path: Optional[Path] = typer.Argument(None, help="A directory of skills to check"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Check whether skills would actually load, and say why not.

    Exists because SkillsMiddleware skips an unparseable skill in silence,
    so without this a typo means a skill that never loads and never
    explains itself (S11b.3).
    """
    from rudra.skills.sources import resolve_sources
    from rudra.skills.validate import validate_tree

    if path is not None:
        trees = [Path(path)]
    else:
        root = Path(project_dir) if project_dir else Path.cwd()
        trees = [source.local_path for source in resolve_sources(root, None)]

    problems = 0
    checked = 0
    for tree in trees:
        for finding in validate_tree(tree):
            checked += 1
            if finding.ok:
                console.print(f"[green]ok[/green]      {escape(finding.name)}")
            else:
                problems += 1
                console.print(
                    f"[red]invalid[/red] {escape(finding.name)}: {escape(finding.reason)}"
                )

    if problems:
        console.print(f"\n[red]{problems} skill(s) would not load.[/red]")
        raise typer.Exit(1)
    console.print(f"\n[green]All {checked} skill(s) valid.[/green]")


@skills_app.command("rebuild")
def skills_rebuild(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Re-render the bundled skill cache and drop stale copies."""
    from rudra.skills.cache import cache_key, ensure_cache, prune_stale
    from rudra.skills.registry import BUNDLES

    cfg = _load_config_or_exit(project_dir)
    enabled = frozenset(cfg.skills.enabled)
    if not enabled:
        console.print("Skills are disabled ([skills] enabled = []); nothing to build.")
        return

    key = cache_key(BUNDLES, enabled)
    removed = prune_stale(None, key)
    cache = ensure_cache(BUNDLES, enabled)
    console.print(f"Skill cache: {cache.root}")
    if removed:
        console.print(f"Removed {len(removed)} stale cache(s).")


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
    from rudra.config.schema import BUILTIN_ROLES
    from rudra.context.budget import evict_limit
    from rudra.state.paths import rudra_paths

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    paths = rudra_paths(project_path)

    table = Table(title="rudra doctor", header_style="bold")
    for column in ("Check", "Status", "Detail"):
        table.add_column(column, overflow="fold")

    # escape() on every data cell below, not just the ones that look
    # risky: a Rich Table parses cell markup exactly the way console.print
    # does, and a project path is user-controlled. A directory named
    # "[draft] proj" otherwise renders as "proj" and doctor reports a path
    # that does not exist (A1.91). Deliberate style tags -- see `rudra mcp
    # test`'s "[red]fail[/red]" -- are the reason this is applied per cell
    # rather than by wrapping add_row.
    table.add_row("project", "ok", escape(str(project_path)))
    for layer in ("user", "project"):
        path = cfg.sources.get(layer)
        table.add_row(
            f"config ({layer})",
            "ok" if path else "-",
            escape(str(path)) if path else "not present",
        )

    dotenv = project_path / ".env"
    table.add_row(
        ".env",
        "ok" if dotenv.exists() else "-",
        escape(str(dotenv)) if dotenv.exists() else "not present",
    )

    table.add_row(
        ".rudra layout",
        "ok" if paths.run.is_dir() else "missing",
        escape(str(paths.root)) if paths.run.is_dir() else "run `rudra init`",
    )

    # The MCP checks C2.3 deferred to Step 13. `--offline` changes nothing
    # here: shutil.which starts no process, and spawning lives in
    # `rudra mcp test`.
    from rudra.mcp import mcp_json_path, read_mcp_json
    from rudra.mcp.config import McpConfigError

    mcp_file = mcp_json_path(project_path)
    try:
        entries = read_mcp_json(mcp_file)
        detail = ", ".join(entry.name for entry in entries) or "no servers"
        table.add_row(".mcp.json", "ok" if mcp_file.exists() else "-", escape(detail))
    except McpConfigError as exc:
        entries = ()
        table.add_row(".mcp.json", "error", escape(str(exc)))

    for entry in entries:
        if not cfg.mcp.enabled:
            table.add_row(f"mcp: {escape(entry.name)}", "-", escape("[mcp] enabled = false"))
        elif entry.name in cfg.mcp.disabled_servers:
            table.add_row(
                f"mcp: {escape(entry.name)}", "-", escape("disabled in [mcp] disabled_servers")
            )
        elif entry.command and shutil.which(entry.command) is None:
            table.add_row(
                f"mcp: {escape(entry.name)}", "missing", escape(f"'{entry.command}' is not on PATH")
            )
        else:
            table.add_row(
                f"mcp: {escape(entry.name)}", "ok", escape(entry.url or entry.command or "")
            )

    configured = {entry.name for entry in entries}
    unknown = [name for name in cfg.mcp.disabled_servers if name not in configured]
    if unknown:
        table.add_row(
            "mcp policy",
            "warn",
            escape(f"disabled_servers names unconfigured: {', '.join(unknown)}"),
        )

    stale = paths.root / "checkpoints.db"
    if stale.exists():
        table.add_row(
            "stale checkpoints.db",
            "warn",
            escape(f"{stale} predates the run/ layout and is unused — safe to delete"),
        )

    from rudra.memory.degrade import last_failure, reset_failures
    from rudra.memory.prefetch import MODEL_SIZE_MB, is_warm, model_cache_dir
    from rudra.memory.store import MemoryStore
    from rudra.memory.taxonomy import TaxonomyError

    try:
        reset_failures()
        store = MemoryStore(project_path, backend=cfg.memory.backend)
        count = store.count()
        # A1.89: `count()` degrades to 0, so without this the row said
        # `ok — 0 memories` for a palace that could not be opened at all,
        # which is the one place a user looks to find out.
        failure = last_failure()
        if failure:
            table.add_row(
                "memory",
                "fail",
                escape(f"unreadable at {paths.memory_palace} — {failure}"),
            )
        else:
            table.add_row(
                "memory",
                "ok",
                escape(
                    f"{count} memories in {paths.memory_palace} (backend: {cfg.memory.backend})"
                ),
            )
    except TaxonomyError as exc:
        table.add_row("memory", "warn", escape(f"off for this project — {exc}"))

    table.add_row(
        "memory model",
        "ok" if is_warm() else "warn",
        escape(f"{model_cache_dir()}")
        if is_warm()
        else f"not fetched — ~{MODEL_SIZE_MB} MB downloads on first use. Run `rudra init`.",
    )

    # C8.1b by name: the user's own MemPalace config is deliberately not
    # honoured, and silently ignoring someone's configuration is how a bug
    # reproduces on one machine only. Only shown when one exists -- a
    # warning nobody needs trains people to skip the table.
    global_config = Path.home() / ".mempalace" / "config.json"
    if global_config.exists():
        table.add_row(
            "mempalace config",
            "warn",
            escape(
                f"{global_config} exists and is ignored — Rudra passes palace_path, "
                f"collection_name and backend explicitly (C8.1b)."
            ),
        )

    export_dir = paths.memory_export
    exported = len(list(export_dir.glob("*.md"))) if export_dir.is_dir() else 0
    table.add_row(
        "memory export",
        "ok" if exported else "-",
        escape(f"{exported} room file(s) in {export_dir}")
        if exported
        else escape(f"none — `rudra memory export` writes the durable copy to {export_dir}"),
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

    # An undeclared context window is a real setting with a real cost, not
    # an absence (S12.9): tool results then evict at deepagents' default
    # instead of at a tenth of the model's actual window. escape() because
    # Rich would parse "[model.<role>]" as a style tag (A1.48).
    undeclared = [role for role in BUILTIN_ROLES if evict_limit(cfg, role) is None]
    limits = ", ".join(
        f"{role} {evict_limit(cfg, role)}" for role in BUILTIN_ROLES if evict_limit(cfg, role)
    )
    table.add_row(
        "context window",
        "warn" if undeclared else "ok",
        escape(
            f"No context_tokens for: {', '.join(undeclared)}. Tool results evict at "
            f"deepagents' 20000-token default for those roles, and summarization "
            f"triggers at 170000 — set [model.<role>] context_tokens."
            if undeclared
            else f"Tool results evict at: {limits}."
        ),
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
        "[dim]MCP servers are checked here; `rudra mcp test` actually starts them. "
        "`rudra memory list` shows what has been remembered.[/dim]"
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

    # C8.5 / D12: fetch the embedding model now, so no run discovers a
    # 167 MB download mid-task. Saying the size *before* starting matters --
    # an unexplained download during what looks like a config-file command
    # is the surprise D12 exists to prevent.
    from rudra.memory.prefetch import MODEL_SIZE_MB, is_warm, warm_model

    if is_warm():
        console.print("[dim]Embedding model already present.[/dim]")
    else:
        console.print(
            f"[dim]Fetching the embedding model (~{MODEL_SIZE_MB} MB, once per machine, "
            f"shared by every project)…[/dim]"
        )
        ok, detail = warm_model()
        console.print(
            f"[green]Embedding model ready[/green] — {detail}"
            if ok
            else f"[yellow]Embedding model not fetched[/yellow] — {detail}. "
            f"It will download on first use instead."
        )

    # No MCP server ships enabled (S13.4): every candidate duplicates
    # something Rudra already gates natively, and a shipped default is an
    # unrequested subprocess. The empty file exists so `rudra mcp add` has
    # somewhere obvious to write, and so the schema is discoverable. JSON
    # carries no comments, so the explanation goes to the console instead of
    # into an invalid file.
    if not global_:
        mcp_file = get_project_path(project_dir) / ".mcp.json"
        if not mcp_file.exists():
            mcp_file.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")
            console.print(f"[green]Wrote[/green] {mcp_file} [dim](no servers configured)[/dim]")

    console.print("[dim]Edit it, then run `rudra models test` to check your model.[/dim]")


@mcp_app.command("list")
def mcp_list(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Show every configured MCP server and whether Rudra will load it."""
    from rudra.mcp import mcp_json_path, read_mcp_json
    from rudra.mcp.config import McpConfigError

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    path = mcp_json_path(project_path)
    try:
        entries = read_mcp_json(path)
    except McpConfigError as exc:
        # escape: the message interpolates the .mcp.json path and a server
        # name, both user-controlled. A path like /tmp/[draft]/proj is Rich
        # markup otherwise -- A1.48's class, on a second surface.
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc

    if not entries:
        console.print(f"No MCP servers configured ({path} is absent or empty).")
        # Escaped: Rich reads [args...] as a markup tag and eats it.
        console.print(escape("Add one with `rudra mcp add <name> -- <command> [args...]`."))
        return

    table = Table(title="MCP servers", header_style="bold")
    for column in ("Server", "Transport", "Command", "Loaded"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        target = entry.url or " ".join([entry.command or "", *entry.args]).strip()
        if not cfg.mcp.enabled:
            state = "no — [mcp] enabled = false"
        elif entry.name in cfg.mcp.disabled_servers:
            state = "no — disabled"
        else:
            state = "yes"
        table.add_row(escape(entry.name), entry.transport, escape(target), state)
    console.print(table)


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Name for this server, e.g. kala"),
    command: Optional[list[str]] = typer.Argument(None, help="Command and args, after a `--`"),
    url: Optional[str] = typer.Option(None, "--url", help="HTTP/SSE endpoint instead of a command"),
    transport: Optional[str] = typer.Option(None, "--transport", help="stdio | http | sse"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Add a server to .mcp.json."""
    from rudra.mcp import ServerEntry, mcp_json_path, read_mcp_json, write_mcp_json

    project_path = get_project_path(project_dir)
    path = mcp_json_path(project_path)
    existing = [entry for entry in read_mcp_json(path) if entry.name != name]

    argv = list(command or [])
    if not argv and url is None:
        console.print("[red]Error:[/red] give a command after `--`, or a --url.")
        raise typer.Exit(1)

    entry = ServerEntry(
        name=name,
        transport=transport or ("stdio" if argv else "http"),
        command=argv[0] if argv else None,
        args=tuple(argv[1:]),
        env={},
        url=url,
        headers={},
    )
    write_mcp_json(path, [*existing, entry])
    console.print(f"Added '{name}' to {path}.")
    console.print("[dim]Check it with `rudra mcp test`.[/dim]")


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(...),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Remove a server from .mcp.json."""
    from rudra.mcp import mcp_json_path, read_mcp_json, write_mcp_json

    path = mcp_json_path(get_project_path(project_dir))
    entries = read_mcp_json(path)
    kept = [entry for entry in entries if entry.name != name]
    if len(kept) == len(entries):
        known = ", ".join(entry.name for entry in entries) or "none"
        console.print(f"[red]Error:[/red] no MCP server named '{name}'. Configured: {known}.")
        raise typer.Exit(1)
    write_mcp_json(path, kept)
    console.print(f"Removed '{name}' from {path}.")


@mcp_app.command("test")
def mcp_test(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Start each enabled server and list what it offers."""
    import asyncio

    from rudra.mcp import McpClient, mcp_json_path, read_mcp_json
    from rudra.mcp.client import McpUnavailable

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    entries = [
        entry
        for entry in read_mcp_json(mcp_json_path(project_path))
        if entry.name not in cfg.mcp.disabled_servers
    ]
    if not entries:
        console.print("No MCP servers to test.")
        return

    client = McpClient(entries, timeout=cfg.mcp.timeout)
    table = Table(title="rudra mcp test", header_style="bold")
    for column in ("Server", "Status", "Detail"):
        table.add_column(column, overflow="fold")

    async def check() -> bool:
        ok = True
        for entry in entries:
            try:
                infos = await client.list_tools(entry.name)
            except McpUnavailable as exc:
                ok = False
                table.add_row(escape(entry.name), "[red]fail[/red]", escape(str(exc)))
                continue
            names = ", ".join(info.name for info in infos) or "no tools"
            table.add_row(escape(entry.name), "ok", f"{len(infos)} tools: {escape(names)}")
        await client.aclose()
        return ok

    healthy = asyncio.run(check())
    console.print(table)
    if not healthy:
        raise typer.Exit(1)


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
        False, "--plan", help="Plan only: show the plan and stop, changing nothing"
    ),
    continue_: bool = typer.Option(
        False,
        "--continue",
        "--resume",
        help="Work the remaining tasks from the last run instead of planning afresh",
    ),
    allow_shell: bool = typer.Option(
        False,
        "--allow-shell",
        help="Let --auto run commands too (off by default: nobody reads them first)",
    ),
    allow_mcp: bool = typer.Option(
        False,
        "--allow-mcp",
        help="Let --auto call MCP tools too (off by default: a server is a separate process)",
    ),
    verbose: Optional[bool] = typer.Option(
        None, "--verbose/--no-verbose", "-V", help="Show detailed output"
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Stream the model's prose token by token into the trace (implies --verbose)",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help=(
            "Write a machine-readable run log to .rudra/run/logs/debug.jsonl. "
            "Pair with --verbose for a bug report: the log records what the "
            "trace shows, so a quiet run logs errors only."
        ),
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
        verbose=True if stream else verbose,
        permission_mode=permission_mode,
        allow_shell=True if allow_shell else None,
        allow_mcp=True if allow_mcp else None,
    )

    from rudra.permissions import disabled_floor_notice, stdin_is_interactive

    # Before ensure_layout, before any model.
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

    # `verbose` stays THREE-STATE all the way to create_main_agent, which
    # resolves it against [agent] verbose in one place (trace.resolve_level).
    # It used to be collapsed here with `if verbose is None: verbose =
    # cfg.agent.verbose`, which erased the difference between "the user
    # asked for less" and "the user said nothing" -- the difference between
    # QUIET and NORMAL. Resolving inside the factory also keeps A1.15's
    # rule intact: no config value is read at import time.

    # Before any agent is built, so a bad --continue never constructs a
    # model, reads a key, or touches the network.
    if continue_:
        from rudra.agent.main_agent import ResumeRefused, check_resumable
        from rudra.state.paths import rudra_paths

        try:
            resumed = check_resumable(rudra_paths(project_path).ledger_json, prompt)
        except ResumeRefused as refusal:
            console.print(f"[red]Cannot continue:[/red] {refusal}")
            raise typer.Exit(EXIT_RESUME_REFUSED) from None

        # The recorded request is the run's task when none was typed --
        # `rudra --continue` alone is the normal form.
        prompt = prompt or resumed.request

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
                command="auto",
                console=console,
                dry_run=dry_run,
                verbose=verbose,
                debug=debug,
                resume=continue_,
            )
            try:
                return await agent.run()
            finally:
                await agent.close()

        async def _guarded() -> AgentResult:
            """Run under a SIGINT handler that cancels rather than raises.

            The task exists so there is something to cancel: Ctrl-C used
            to unwind out of asyncio.run wherever the interpreter happened
            to be, which is A1.93 -- the ledger kept an IN_PROGRESS task
            that `--continue` then refused to pick up.
            """
            task = asyncio.create_task(_run())
            with _cancel_on_sigint(task):
                return await task

        try:
            result: AgentResult = asyncio.run(_guarded())
        except asyncio.CancelledError:
            # The cancel landed outside work()'s task boundary -- during
            # planning, or between tasks. Nothing is half-written and the
            # ledger is whatever the last save left, which is exactly what
            # `--continue` expects.
            console.print("\n[yellow]Cancelled.[/yellow] Resume with: rudra --continue")
            raise typer.Exit(EXIT_CANCELLED) from None

        if result.success:
            body = (
                f"[green]✓[/green] {result.message}\n\n"
                f"Files created:  {len(result.files_created)}\n"
                f"Files modified: {len(result.files_modified)}\n"
                f"Iterations:     {result.iterations}"
            )
            block = render_usage(result.usage)
            if block:
                body += f"\n\n{block}"
            console.print(Panel(body, title="✅ Complete", border_style="green"))
        else:
            cancelled = result.message.lower().startswith("cancelled")
            console.print(
                Panel(
                    f"[red]✗[/red] {escape(result.message)}",
                    title="⏹ Cancelled" if cancelled else "❌ Error",
                    border_style="yellow" if cancelled else "red",
                )
            )
            # 130 rather than 1 when the user stopped it: a wrapper script
            # should not have to parse prose to tell those apart.
            raise typer.Exit(EXIT_CANCELLED if cancelled else 1)

    else:
        # ── Interactive REPL ───────────────────────────────────────────────
        print_banner()

        console.print(f"  [dim]Working directory:[/dim] [bold white]{project_path}[/bold white]\n")

        pt_session = build_session(project_path)

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
                            f"  [yellow]Unknown command:[/yellow] {escape(cmd)}  "
                            f"[dim](type /help)[/dim]"
                        )
                        continue

                    if not user_input.strip():
                        continue

                    # Before the Panel, so what is shown is what is sent.
                    user_input = expand_mentions(user_input, project_path)

                    console.print(
                        Panel(
                            f"[bold]{escape(user_input)}[/bold]\n"
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
                        command="auto",
                        console=console,
                        dry_run=dry_run,
                        verbose=verbose,
                        debug=debug,
                    )
                    turn = asyncio.create_task(agent.run())
                    try:
                        with _cancel_on_sigint(turn):
                            result = await turn
                    finally:
                        await agent.close()

                    if result.success:
                        body = (
                            f"[green]✓[/green] {result.message}\n\n"
                            f"Files created:  {len(result.files_created)}\n"
                            f"Files modified: {len(result.files_modified)}"
                        )
                        block = render_usage(result.usage)
                        if block:
                            body += f"\n\n{block}"
                        console.print(Panel(body, title="✅ Complete", border_style="green"))
                    else:
                        console.print(
                            Panel(
                                f"[red]✗[/red] {result.message}",
                                title="❌ Error",
                                border_style="red",
                            )
                        )

                # BEFORE KeyboardInterrupt, and separate from it: they mean
                # different things now. A CancelledError is a turn the user
                # stopped; a KeyboardInterrupt at the prompt is a line they
                # want cleared. A cancelled TURN is not a cancelled SESSION
                # -- getting the prompt back is the point of Ctrl-C here.
                except asyncio.CancelledError:
                    console.print(
                        "\n  [yellow]Cancelled.[/yellow] "
                        "[dim]The ledger is intact — `rudra --continue` picks it up.[/dim]"
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

    # The one table (cli_repl.REPL_COMMANDS), so help and completion
    # cannot disagree about which commands exist.
    rows = [*REPL_COMMANDS.items()]
    rows += [
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


def _memory_store(project_dir: Optional[Path]):
    """The store for one CLI invocation, or exit 1 with the reason.

    TaxonomyError is the constructor's, so it cannot degrade -- there is no
    store yet. Everything after construction degrades on its own.
    """
    from rudra.memory.degrade import reset_failures
    from rudra.memory.store import MemoryStore
    from rudra.memory.taxonomy import TaxonomyError

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    reset_failures()
    try:
        return MemoryStore(project_path, backend=cfg.memory.backend)
    except TaxonomyError as exc:
        # escape: the message quotes the project directory name, which is
        # exactly the value that made this raise. Same class as A1.48.
        console.print(f"[red]Error:[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc


def _exit_if_memory_failed() -> None:
    """Turn a degraded call into a message and exit 1 (A1.89).

    Every store method returns a safe default on failure, so without this
    a broken palace reaches the user as "Nothing recorded for this project
    yet" — a false statement about a store that was never read. `export`
    is the worst of them: it would report success and write nothing while
    the user believes they now hold a durable copy.
    """
    from rudra.memory.degrade import last_failure

    failure = last_failure()
    if failure is None:
        return
    console.print(f"[red]Memory is unavailable:[/red] {escape(failure)}")
    console.print("[dim]Nothing was read or written. `rudra doctor` shows the palace path.[/dim]")
    raise typer.Exit(1)


def _memory_table(title: str, rows) -> Table:
    table = Table(title=title, header_style="bold")
    for column in ("Room", "By", "Recorded", "Memory"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            escape(row.room),
            escape(row.added_by),
            escape(row.filed_at[:10]),
            escape(row.content),
        )
    return table


@memory_app.command("list")
def memory_list(
    room: Optional[str] = typer.Option(
        None, "--room", help="decisions | tasks | blockers | preferences"
    ),
    added_by: Optional[str] = typer.Option(None, "--added-by", help="rudra | agent"),
    limit: int = typer.Option(50, "--limit"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Show what this project has remembered."""
    store = _memory_store(project_dir)
    rows = store.list_entries(room=room, added_by=added_by, limit=limit)
    _exit_if_memory_failed()
    if not rows:
        console.print("Nothing recorded for this project yet.")
        return
    console.print(_memory_table(f"Memory — {store.wing}", rows))


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="What to look for"),
    room: Optional[str] = typer.Option(None, "--room"),
    limit: int = typer.Option(5, "--limit"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Search this project's memory by meaning."""
    store = _memory_store(project_dir)
    hits = store.search(query, room=room, limit=limit)
    _exit_if_memory_failed()
    if not hits:
        console.print("Nothing recorded on this project matches that.")
        return
    table = Table(title=f"Memory search — {query}", header_style="bold")
    for column in ("Room", "By", "Score", "Memory"):
        table.add_column(column, overflow="fold")
    for hit in hits:
        table.add_row(
            escape(hit.room), escape(hit.added_by), f"{hit.score:.3f}", escape(hit.content)
        )
    console.print(table)


@memory_app.command("forget")
def memory_forget(
    room: Optional[str] = typer.Option(None, "--room"),
    added_by: Optional[str] = typer.Option(None, "--added-by", help="rudra | agent"),
    all_: bool = typer.Option(False, "--all", help="Every memory for this project"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Delete memories. Irreversible."""
    from rudra.permissions import stdin_is_interactive

    if not (all_ or room or added_by):
        console.print("[red]Error:[/red] name what to forget — --room, --added-by, or --all.")
        raise typer.Exit(1)

    store = _memory_store(project_dir)
    rows = store.list_entries(room=room, added_by=added_by, limit=10_000)
    _exit_if_memory_failed()
    if not rows:
        console.print("Nothing matches — nothing deleted.")
        return

    console.print(_memory_table(f"About to delete {len(rows)}", rows[:20]))
    if len(rows) > 20:
        console.print(f"[dim]…and {len(rows) - 20} more.[/dim]")

    # Refuse rather than hang: a piped stdin cannot answer a prompt, and this
    # deletion cannot be undone. Same rule mode="ask" follows, and the same
    # helper -- stdin_is_interactive survives a stdin with no isatty, which
    # is what CliRunner substitutes.
    if not yes:
        if not stdin_is_interactive():
            console.print(
                "[red]Refusing:[/red] this deletes data and stdin is not a terminal. Pass --yes."
            )
            raise typer.Exit(2)
        if not typer.confirm(f"Delete {len(rows)} memories? This cannot be undone"):
            console.print("Nothing deleted.")
            return

    deleted = store.delete([row.id for row in rows])
    _exit_if_memory_failed()
    console.print(f"[green]Deleted[/green] {deleted} memories.")


@memory_app.command("export")
def memory_export(
    out: Optional[Path] = typer.Option(None, "--out", help="Defaults to .rudra/memory/export/"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Write memory to markdown — the durable, committable copy."""
    from rudra.memory.export import export_memory
    from rudra.state.paths import rudra_paths

    store = _memory_store(project_dir)
    target = out or rudra_paths(get_project_path(project_dir)).memory_export
    stats = export_memory(store, target)
    _exit_if_memory_failed()
    if not stats["drawers"]:
        console.print("Nothing recorded for this project yet — nothing exported.")
        return
    console.print(f"[green]Exported[/green] {stats['drawers']} memories to {target}")


@memory_app.command("import")
def memory_import(
    source: Path = typer.Argument(..., help="A directory written by `rudra memory export`"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Restore memory from an exported markdown tree."""
    from rudra.memory.export import import_memory

    store = _memory_store(project_dir)
    stats = import_memory(store, source)
    _exit_if_memory_failed()
    if not stats["drawers"]:
        console.print(f"Nothing to import from {source}.")
        return
    console.print(f"[green]Imported[/green] {stats['drawers']} memories from {source}")
