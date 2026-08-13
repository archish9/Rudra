"""Precedence, provenance, and validation — the heart of C2.1."""

import os
from pathlib import Path

import pytest

from rudra.config import Config, ConfigError, get_config, reset_config
from rudra.config.loader import deep_merge


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """No ambient config may reach these tests."""
    from rudra.config.layers import _reset_deprecation_warnings

    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _reset_deprecation_warnings()
    reset_config()
    yield
    reset_config()


def _write_project_toml(root: Path, body: str) -> None:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(body, encoding="utf-8")


def _write_user_toml(tmp_path: Path, body: str) -> None:
    target = tmp_path / "xdg" / "rudra"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.toml").write_text(body, encoding="utf-8")


def test_merge_is_per_leaf_not_per_table() -> None:
    merged, _ = deep_merge(
        [
            ("user", {"model": {"planner": {"temperature": 0.5, "model": "a"}}}),
            ("project", {"model": {"planner": {"model": "b"}}}),
        ]
    )
    assert merged["model"]["planner"] == {"temperature": 0.5, "model": "b"}


def test_provenance_names_the_winning_layer() -> None:
    _, provenance = deep_merge(
        [
            ("builtin", {"agent": {"verbose": True}}),
            ("project", {"agent": {"verbose": False}}),
        ]
    )
    assert provenance["agent.verbose"] == "project"


def test_project_toml_beats_user_toml(tmp_path: Path) -> None:
    _write_user_toml(tmp_path, '[model.default]\nmodel = "from-user"\n')
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nmodel = "from-project"\n')
    cfg = get_config(project)
    assert cfg.model_for("default").model == "from-project"
    assert cfg.provenance["model.default.model"] == "project"


def test_user_toml_beats_builtin(tmp_path: Path) -> None:
    _write_user_toml(tmp_path, '[model.default]\nmodel = "from-user"\n')
    project = tmp_path / "proj"
    project.mkdir()
    cfg = get_config(project)
    assert cfg.model_for("default").model == "from-user"
    assert cfg.provenance["model.default.model"] == "user"


def test_env_beats_project_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nmodel = "from-project"\n')
    monkeypatch.setenv("RUDRA_MODEL", "from-env")
    cfg = get_config(project)
    assert cfg.model_for("default").model == "from-env"
    assert cfg.provenance["model.default.model"] == "env"


def test_cli_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_VERBOSE", "true")
    cfg = get_config(tmp_path, verbose=False)
    assert cfg.agent.verbose is False
    assert cfg.provenance["agent.verbose"] == "cli"


def test_role_inheritance_happens_after_the_cross_layer_merge(tmp_path: Path) -> None:
    """The subtle one. A project [model.planner] must still inherit a
    user-level [model.default]."""
    _write_user_toml(tmp_path, "[model.default]\ntemperature = 0.9\n")
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.planner]\nmodel = "planner-only"\n')
    planner = get_config(project).model_for("planner")
    assert planner.model == "planner-only"
    assert planner.temperature == 0.9


def test_an_undeclared_role_falls_back_to_default(tmp_path: Path) -> None:
    """Step 9 calls build_model("reviewer") before reviewer config exists."""
    cfg = get_config(tmp_path)
    assert cfg.model_for("reviewer") == cfg.model_for("default")


def test_a_toml_declared_role_is_addressable_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.reviewer]\nmodel = "declared"\n')
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "env-wins")
    assert get_config(project).model_for("reviewer").model == "env-wins"


def test_unknown_key_is_a_hard_error_naming_the_nearest_match(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[model.default]\ntempreature = 0.3\n")
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "tempreature" in message
    assert "temperature" in message
    assert "config.toml" in message


def test_reserved_section_names_the_step_that_implements_it(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[skills]\nsuperpowers = true\n")
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    assert "Step 11" in str(excinfo.value)


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[permisions]\nmode = 'ask'\n")
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    assert "permisions" in str(excinfo.value)


def test_invalid_provider_lists_the_valid_ones(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nprovider = "banana"\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "banana" in message
    assert "openai_compatible" in message


def test_invalid_permission_mode_lists_the_valid_ones(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[permissions]\nmode = "yolo"\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "yolo" in message
    assert "ask" in message


def test_negative_context_tokens_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[model.default]\ncontext_tokens = -5\n")
    with pytest.raises(ConfigError, match="context_tokens"):
        get_config(project)


def test_allow_and_deny_must_be_lists_of_strings(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[permissions]\nallow = 5\n")
    with pytest.raises(ConfigError, match="allow"):
        get_config(project)


def test_compat_flags_must_be_booleans(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[compat]\ntask_anchor = "yes"\n')
    with pytest.raises(ConfigError, match="task_anchor"):
        get_config(project)


def test_an_api_key_value_never_appears_in_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1.5, asserted as a property rather than assumed."""
    monkeypatch.setenv("MY_SECRET", "sk-do-not-leak-me")
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(
        project, '[model.default]\napi_key_env = "MY_SECRET"\nprovider = "banana"\n'
    )
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    assert "sk-do-not-leak-me" not in str(excinfo.value)


def test_project_dir_selects_the_project_toml(tmp_path: Path) -> None:
    """A5.2, extended from .env to config.toml."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write_project_toml(elsewhere, '[model.default]\nmodel = "the-right-one"\n')
    cwd_project = tmp_path / "cwd"
    cwd_project.mkdir()
    _write_project_toml(cwd_project, '[model.default]\nmodel = "the-wrong-one"\n')
    assert get_config(elsewhere).model_for("default").model == "the-right-one"


def test_sources_records_which_files_were_found(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[agent]\nverbose = false\n")
    cfg = get_config(project)
    assert cfg.sources["project"] == project / ".rudra" / "config.toml"
    assert cfg.sources["user"] is None


def test_get_checkpoint_path_creates_nothing(tmp_path: Path) -> None:
    """A1.43."""
    cfg = get_config(tmp_path)
    path = cfg.get_checkpoint_path(tmp_path)
    assert path == tmp_path / ".rudra" / "run" / "checkpoints.db"
    assert not (tmp_path / ".rudra").exists()


def test_config_is_cached_until_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_config(tmp_path)
    monkeypatch.setenv("RUDRA_MODEL", "changed")
    assert get_config(tmp_path) is first
    reset_config()
    assert get_config(tmp_path).model_for("default").model == "changed"


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = get_config(tmp_path)
    with pytest.raises(Exception):
        cfg.agent = None  # type: ignore[misc]
    assert isinstance(cfg, Config)


# --- Step 10a: the clarification budget (C6.8) ---


def test_max_questions_defaults_to_five(tmp_path: Path) -> None:
    assert get_config(tmp_path).agent.max_questions == 5


def test_max_questions_can_be_zero_to_disable_asking(tmp_path: Path) -> None:
    """0 is legal here and illegal for max_fix_attempts, deliberately.

    A run that asks nothing is a normal unattended run, while 0 attempts
    would block every task without the coder running once.
    """
    project = tmp_path / "proj"
    _write_project_toml(project, "[agent]\nmax_questions = 0\n")
    cfg = get_config(project)
    assert cfg.agent.max_questions == 0
    assert cfg.provenance["agent.max_questions"] == "project"


@pytest.mark.parametrize("bad", ["-1", "true", '"five"', "2.5"])
def test_a_bad_max_questions_is_a_config_error(tmp_path: Path, bad: str) -> None:
    project = tmp_path / "proj"
    _write_project_toml(project, f"[agent]\nmax_questions = {bad}\n")
    with pytest.raises(ConfigError) as caught:
        get_config(project)
    assert "max_questions" in str(caught.value)


def test_config_list_reports_max_questions(tmp_path: Path) -> None:
    """A key that is honoured and never printed is A1.54 exactly."""
    from rudra.cli import _flatten

    keys = [key for key, _ in _flatten(get_config(tmp_path))]
    assert "agent.max_questions" in keys
