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
    """A1.52: _flatten claimed completeness it did not have.

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
