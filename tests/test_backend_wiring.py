"""The composite backend (Step 7 spec §5). Closes C3.1 and C3.2.

Also pins A1.45: artifacts must never land in the user's project.
"""

from __future__ import annotations

import os

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.filesystem import FilesystemMiddleware

from rudra.agent.main_agent import build_backend
from rudra.config.loader import build_config, reset_config
from rudra.state.paths import ensure_layout, rudra_paths


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def backend_for(tmp_path):
    ensure_layout(tmp_path)
    return build_backend(build_config(tmp_path), tmp_path)


def test_paths_expose_an_artifacts_directory(tmp_path):
    assert rudra_paths(tmp_path).artifacts == tmp_path / ".rudra" / "run" / "artifacts"


def test_ensure_layout_creates_the_artifacts_directory(tmp_path):
    assert ensure_layout(tmp_path).artifacts.is_dir()


def test_artifacts_are_inside_the_gitignored_run_subtree(tmp_path):
    """D15: run/ is volatile. Artifacts churn, so they belong under it."""
    ensure_layout(tmp_path)
    body = (tmp_path / ".rudra" / ".gitignore").read_text(encoding="utf-8")
    assert "run/" in body


def test_the_backend_is_a_composite(tmp_path):
    assert isinstance(backend_for(tmp_path), CompositeBackend)


def test_the_default_is_a_local_shell_backend(tmp_path):
    assert isinstance(backend_for(tmp_path).default, LocalShellBackend)


def test_execute_actually_runs_a_command(tmp_path):
    """This is C3.1's whole point. U.17 proved a plain FilesystemBackend cannot."""
    result = backend_for(tmp_path).execute("echo shell-is-live")
    assert result.exit_code == 0
    assert "shell-is-live" in result.output


def test_commands_run_in_the_project_directory(tmp_path):
    assert str(tmp_path) in backend_for(tmp_path).execute("pwd").output


def test_the_shell_environment_carries_path(tmp_path):
    """A1.44: the backend default is an EMPTY environment."""
    assert backend_for(tmp_path).execute("echo $PATH").output.strip()


def test_a_real_toolchain_is_reachable(tmp_path):
    """Step 8's git tools and test runner fail on call one without this."""
    assert backend_for(tmp_path).execute("which python3").exit_code == 0


def test_no_secret_reaches_the_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-canary-not-in-shell")
    assert "sk-canary-not-in-shell" not in backend_for(tmp_path).execute("env").output


def test_artifacts_are_routed_out_of_the_project(tmp_path):
    """A1.45: artifacts_root defaults to the backend root, i.e. the repo."""
    middleware = FilesystemMiddleware(backend=backend_for(tmp_path))
    assert middleware._large_tool_results_prefix.startswith("/artifacts/")
    assert middleware._conversation_history_prefix.startswith("/artifacts/")


def test_shell_false_yields_a_backend_without_execution(tmp_path):
    root = write_config(tmp_path, "[tools]\nshell = false\n")
    ensure_layout(root)
    backend = build_backend(build_config(root), root)
    assert not isinstance(backend.default, LocalShellBackend)


def test_permissions_are_never_passed_to_deepagents():
    """U.7: passing them with an execute-capable backend raises. Guard it.

    Parsed, not grepped. A substring check matches the comment explaining
    why the argument is absent — the same self-matching trap Step 6's A5.2
    ordering guard fell into.
    """
    import ast
    import inspect

    from rudra.agent import main_agent, planner_agent
    from rudra.subagents import build as subagent_build

    for module in (main_agent, planner_agent, subagent_build):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            passed = {kw.arg for kw in node.keywords if kw.arg}
            assert "permissions" not in passed, (
                f"{module.__name__} passes permissions= to a call; "
                f"deepagents raises NotImplementedError on an execute-capable backend"
            )


def test_the_agent_constructor_builds_a_gate():
    """Hard gate 2: shell must never ship without the permission layer."""
    import ast
    import inspect

    from rudra.agent import main_agent

    tree = ast.parse(inspect.getsource(main_agent.create_main_agent))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "build_gate" in called
    assert "build_backend" in called
