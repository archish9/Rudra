"""Import and --help smoke tests for the CLI (C0.5).

cli.py was at 0% coverage -- no test imported it. These do not test behaviour;
they catch the import-time breakage that unit tests miss, which is exactly the
failure mode Step 3's config refactor risked (a decorator argument evaluated
at import).
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from pathlib import Path


def test_cli_module_imports():
    """A broken module-level statement fails here rather than at a user's shell."""
    import rudra.cli

    assert rudra.cli.app is not None


def test_help_exits_zero_and_lists_the_flags():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--verbose" in result.stdout
    assert "--project-dir" in result.stdout


def test_version_reports_the_installed_version():
    result = subprocess.run(
        [sys.executable, "-m", "rudra.cli", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "0.0.0+unknown" not in result.stdout


def test_project_path_is_resolved_before_config_loads() -> None:
    """A5.2: get_config() caches for the process, so if cli.py calls it
    before computing project_path, no later call can correct the .env
    location. This asserts source order because the ordering IS the fix —
    a behavioral test would need a full CLI run against a real backend."""
    from rudra import cli

    # Comments are stripped first. The fix's own explanatory comment names
    # get_config() while describing why it moved, and that mention sits
    # above the code — so an unstripped search finds the comment and reports
    # the very ordering bug the code no longer has.
    source = "\n".join(
        line
        for line in inspect.getsource(cli.main).splitlines()
        if not line.strip().startswith("#")
    )
    get_config_at = source.index("get_config(")
    project_path_at = source.index("project_path = get_project_path(")

    assert project_path_at < get_config_at, (
        "cli.main() must resolve project_path before its first get_config() "
        "call, or --project-dir cannot affect which .env is read (A5.2)."
    )


def test_the_first_get_config_call_passes_the_project_path() -> None:
    """Ordering alone is not enough — the path has to be handed over.

    Matched on the call's first argument rather than the whole call text:
    Step 6 added keyword arguments (`verbose=`, `permission_mode=`) to the
    same call, which broke an exact-string check while the property it
    guards — project_path is the first thing handed to get_config — was
    unchanged. See TODO.md A5.2.
    """
    import ast

    from rudra import cli

    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.main)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "get_config":
            assert node.args, "get_config() in cli.main must be given the project path"
            assert getattr(node.args[0], "id", None) == "project_path", (
                "cli.main()'s first get_config() argument must be project_path, "
                "or --project-dir cannot affect which config and .env are read (A5.2)."
            )
            return
    raise AssertionError("no get_config() call found in cli.main")


def test_models_test_is_a_registered_subcommand() -> None:
    """C1.6. Runs the CLI with no backend, so it must not make a network
    call just to render help."""
    from typer.testing import CliRunner

    from rudra.cli import app

    result = CliRunner().invoke(app, ["models", "test", "--help"])

    assert result.exit_code == 0
    assert "--role" in result.output


def test_a_subcommand_name_is_not_swallowed_as_a_task_prompt() -> None:
    """Regression guard for the callback's argument shape.

    With a declared positional `prompt` Argument, click bound "models" to it
    before ever trying to resolve a command name — so `rudra models` started
    an agent run for a task literally called "models", and `rudra models
    test` died with "No such command 'test'". Every subcommand added from
    here on depends on this staying fixed.
    """
    from typer.testing import CliRunner

    from rudra.cli import app

    result = CliRunner().invoke(app, ["models", "test", "--role", "planner", "--help"])

    assert result.exit_code == 0
    assert "Probe one role only" in result.output


def parse_cli(argv: list[str]):
    """Parse argv exactly as the real CLI would, without invoking anything.

    CliRunner().invoke() would run main() for real — which reaches
    create_main_agent, calls install_path_normalizer, and monkeypatches
    deepagents' validate_path for the rest of the session. That broke
    test_deepagents_contract's shared-object assertion from a different
    file, and would have made a network call besides. Parsing is the whole
    of what these tests are about, so they stop at make_context.
    """
    from typer.main import get_command

    from rudra.cli import app

    command = get_command(app)
    ctx = command.make_context("rudra", list(argv))
    return command, ctx


def test_a_quoted_task_is_not_mistaken_for_a_command(tmp_path: Path) -> None:
    """The other half of the contract, and the reason this asserts behavior.

    An earlier version of this test checked that a particular line of source
    existed in cli.main. It passed while `rudra "<task>"` — the primary
    documented interface — was completely broken, exiting with "No such
    command". Source-shape assertions cannot catch that; only parsing can.
    """
    _, ctx = parse_cli(["--project-dir", str(tmp_path), "reverse a string in python"])

    assert ctx.args == ["reverse a string in python"]


def test_an_unknown_subcommand_style_token_is_treated_as_a_task(tmp_path: Path) -> None:
    """`models` resolves as a command; `modelz` is just an odd task name."""
    _, ctx = parse_cli(["--project-dir", str(tmp_path), "modelz"])

    assert ctx.args == ["modelz"]


def test_a_real_command_name_still_dispatches() -> None:
    """The inverse of the test above: `models` must NOT become a task.

    Asserted at parse level as "the token was claimed for dispatch rather
    than left in ctx.args". ctx.invoked_subcommand is not usable here — it
    is populated during invoke(), so at make_context time it is None for
    every input and an assertion against it would pass vacuously.
    """
    _, ctx = parse_cli(["models"])

    assert ctx.args == [], "'models' leaked through as a task prompt"
    assert getattr(ctx, "_protected_args", []) == ["models"]


def test_an_unquoted_multi_word_task_is_rejoined() -> None:
    """`rudra build a flask app` and the quoted form must agree."""
    _, ctx = parse_cli(["build", "a", "flask", "app"])

    assert " ".join(ctx.args) == "build a flask app"
