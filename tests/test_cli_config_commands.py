"""CLI surface for configuration: init, config list, config get, doctor."""

import os
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_config()
    yield
    reset_config()


# --------------------------------------------------------------------------
# rudra init
# --------------------------------------------------------------------------


def test_init_writes_a_config_that_parses_back(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    written = tmp_path / ".rudra" / "config.toml"
    assert written.exists()
    with written.open("rb") as handle:
        tomllib.load(handle)


def test_init_output_round_trips_through_the_loader(tmp_path: Path) -> None:
    """A scaffold the loader rejects would be worse than no scaffold."""
    from rudra.config import build_config

    runner.invoke(app, ["init", "-d", str(tmp_path)])
    cfg = build_config(tmp_path)
    assert cfg.model_for("coder").model == "qwen3-coder:32b"
    assert cfg.provenance["model.coder.model"] == "project"


def test_init_creates_the_d15_layout(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert (tmp_path / ".rudra" / "run").is_dir()
    assert (tmp_path / ".rudra" / ".gitignore").exists()


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    (tmp_path / ".rudra" / "config.toml").write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "--force" in result.output
    assert (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8") == "# mine\n"


def test_init_force_overwrites(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    (tmp_path / ".rudra" / "config.toml").write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "-d", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "# mine" not in (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")


def test_init_global_writes_to_the_user_location(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--global"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "xdg" / "rudra" / "config.toml").exists()


def test_the_template_contains_no_api_key_value(tmp_path: Path) -> None:
    """C1.5: the scaffold names an env var, never a key."""
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    body = (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")
    assert "api_key_env" in body
    assert "sk-" not in body


def test_the_template_documents_what_each_permission_mode_does(tmp_path: Path) -> None:
    """Step 6 shipped this template saying permissions were NOT ENFORCED.

    Step 7 enforces them, so that warning is now false and must be gone.
    Asserting its absence alone would pass against an empty file, so the
    real content is asserted too.
    """
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    body = (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")
    assert "NOT ENFORCED" not in body
    for mode in ("ask", "auto", "plan"):
        assert mode in body
    assert "floor_disable" in body
    assert "[tools]" in body


# --------------------------------------------------------------------------
# rudra config list / get
# --------------------------------------------------------------------------


def _write_project_toml(root: Path, body: str) -> None:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(body, encoding="utf-8")


def test_config_list_shows_values_and_their_source(tmp_path: Path) -> None:
    _write_project_toml(tmp_path, '[model.default]\nmodel = "from-project"\n')
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "from-project" in result.output
    assert "project" in result.output


def test_config_list_marks_builtin_values(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code == 0
    assert "builtin" in result.output


def test_config_get_returns_one_value(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "get", "model.default.provider", "-d", str(tmp_path)])
    assert result.exit_code == 0
    assert "ollama" in result.output


def test_config_get_rejects_an_unknown_key(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "get", "model.default.nonsense", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "nonsense" in result.output


def test_config_get_never_prints_an_api_key_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1.5."""
    monkeypatch.setenv("MY_SECRET", "sk-do-not-leak-me")
    _write_project_toml(tmp_path, '[model.default]\napi_key_env = "MY_SECRET"\n')
    result = runner.invoke(app, ["config", "get", "model.default.api_key_env", "-d", str(tmp_path)])
    assert "MY_SECRET" in result.output
    assert "sk-do-not-leak-me" not in result.output


def test_a_bad_config_file_reports_the_path_not_a_traceback(tmp_path: Path) -> None:
    _write_project_toml(tmp_path, "[model\n")
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "config.toml" in result.output
    assert "Traceback" not in result.output


# --------------------------------------------------------------------------
# rudra doctor
# --------------------------------------------------------------------------


def test_doctor_reports_config_sources(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert result.exit_code == 0, result.output
    assert "config" in result.output.lower()


def test_doctor_states_what_the_permission_mode_does(tmp_path: Path) -> None:
    """Step 6's honesty requirement, updated for Step 7 enforcing the mode.

    The requirement is that `doctor` never leaves the user guessing what
    permissions will do. Step 6 satisfied it by saying NOT ENFORCED; Step 7
    satisfies it by describing the behaviour, and saying NOT ENFORCED now
    would be the dishonest answer.
    """
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert "NOT ENFORCED" not in result.output
    assert "mode = ask" in result.output
    assert "prompting" in result.output


def test_doctor_flags_a_stale_top_level_checkpoint_db(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "checkpoints.db").write_bytes(b"stale")
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert "checkpoints.db" in result.output


def test_doctor_reports_a_broken_config_cleanly(tmp_path: Path) -> None:
    _write_project_toml(tmp_path, "[model\n")
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_config_list_shows_every_field_of_every_section(tmp_path: Path) -> None:
    """A1.54: _flatten claimed completeness it did not have.

    Derived from the dataclasses rather than hand-listed, so a key added
    later cannot go missing again.
    """
    from dataclasses import fields

    from rudra.cli import _flatten
    from rudra.config.loader import build_config

    cfg = build_config(tmp_path)
    shown = {key for key, _ in _flatten(cfg)}

    for section in ("agent", "permissions", "tools", "compat"):
        for field in fields(getattr(cfg, section)):
            assert f"{section}.{field.name}" in shown, (
                f"{section}.{field.name} is honoured at run time but never printed"
            )


def test_config_list_prints_the_step_8_tools_keys(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert "auto_branch" in result.stdout
    assert "test_timeout" in result.stdout


# --------------------------------------------------------------------------
# bare `rudra` auto-init — first run scaffolds, later runs don't
# --------------------------------------------------------------------------


def test_bare_invocation_scaffolds_an_uninitialized_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No task, no existing config.toml: it writes one before doing anything else."""
    monkeypatch.setattr("rudra.memory.prefetch.is_warm", lambda: True)
    result = runner.invoke(app, ["-d", str(tmp_path)])
    assert (tmp_path / ".rudra" / "config.toml").exists()
    assert "No .rudra/config.toml found" in result.output


def test_bare_invocation_does_not_rescaffold_an_initialized_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second bare run: the gate is the file's own existence, nothing else."""
    monkeypatch.setattr("rudra.memory.prefetch.is_warm", lambda: True)
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    written_at = (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")

    result = runner.invoke(app, ["-d", str(tmp_path)])

    assert "No .rudra/config.toml found" not in result.output
    assert (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8") == written_at


def test_a_task_prompt_does_not_trigger_auto_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scope is bare `rudra` only — `rudra "task"` on an uninitialized project

    stays on defaults rather than silently scaffolding files the user never
    asked for.
    """
    monkeypatch.setattr("rudra.memory.prefetch.is_warm", lambda: True)
    result = runner.invoke(app, ["-d", str(tmp_path), "do a thing"])
    assert not (tmp_path / ".rudra" / "config.toml").exists()
    assert "No .rudra/config.toml found" not in result.output


def test_bare_invocation_warms_the_embedding_model_and_reports_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The download is silent at the library level (prefetch.py:44) -- Rudra

    must call it and report the result itself, or a slow first run gives no
    sign anything happened. (The in-progress spinner is real-terminal-only
    by Rich's own design -- CliRunner has no TTY to show it on, so it isn't
    asserted here; what's stably testable is that warm_model() runs and its
    result reaches the user.)
    """
    calls: list[bool] = []

    def _fake_warm() -> tuple[bool, str]:
        calls.append(True)
        return True, "downloaded to /fake/cache"

    monkeypatch.setattr("rudra.memory.prefetch.is_warm", lambda: False)
    monkeypatch.setattr("rudra.memory.prefetch.warm_model", _fake_warm)

    result = runner.invoke(app, ["-d", str(tmp_path)])

    assert calls == [True]
    assert "downloaded to /fake/cache" in result.output


# --------------------------------------------------------------------------
# the REPL re-reads config.toml every turn
# --------------------------------------------------------------------------


def _repl_with(monkeypatch: pytest.MonkeyPatch, inputs: list[str], agent_factory):
    """Run the REPL over a scripted input list and return the output."""
    from rudra import cli as cli_module

    remaining = list(inputs)

    async def scripted(session, prompt_text):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    monkeypatch.setattr(cli_module, "_prompt_input", scripted)
    monkeypatch.setattr(cli_module, "create_main_agent", agent_factory)
    monkeypatch.setattr(cli_module, "print_banner", lambda: None)
    return runner.invoke(app, ["--auto"])


def test_editing_config_toml_mid_session_takes_effect_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_config() caches for the process (A5.2): without a reload, an
    edit made while the REPL is already running was invisible until the
    session was closed and reopened. Each task turn now re-reads the file,
    so the SECOND turn should see an edit made during the first."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("rudra.memory.prefetch.is_warm", lambda: True)

    init_result = runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert init_result.exit_code == 0, init_result.output
    config_path = tmp_path / ".rudra" / "config.toml"
    assert 'model = "qwen3-coder:32b"' in config_path.read_text()

    from rudra.agent.main_agent import AgentResult

    class _Agent:
        async def run(self):
            return AgentResult(success=True, message="done")

        async def close(self):
            return None

    edited = False

    async def factory(**kwargs):
        nonlocal edited
        if not edited:
            text = config_path.read_text()
            config_path.write_text(text.replace("qwen3-coder:32b", "edited-coder-model"))
            edited = True
        return _Agent()

    result = _repl_with(monkeypatch, ["turn one", "turn two", "/exit"], factory)

    assert "qwen3-coder:32b" in result.output, result.output
    assert "edited-coder-model" in result.output, result.output
    assert result.output.index("qwen3-coder:32b") < result.output.index("edited-coder-model")
