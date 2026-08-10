"""Each layer reader in isolation. None of them knows about precedence."""

import os
import warnings
from pathlib import Path

import pytest

from rudra.config.layers import (
    ConfigError,
    _reset_deprecation_warnings,
    builtin_layer,
    cli_layer,
    env_layer,
    project_toml_layer,
    read_toml,
    user_toml_path,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """No ambient Rudra configuration may reach a layer test."""
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    _reset_deprecation_warnings()
    yield
    _reset_deprecation_warnings()


def test_builtin_layer_is_a_copy() -> None:
    """Mutating a returned layer must not poison DEFAULTS for the next call."""
    first = builtin_layer()
    first["model"]["default"]["model"] = "tampered"
    assert builtin_layer()["model"]["default"]["model"] == "qwen3:32b"


def test_read_toml_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert read_toml(tmp_path / "nope.toml") == {}


def test_read_toml_parses_a_real_file(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('[model.planner]\nmodel = "x"\n', encoding="utf-8")
    assert read_toml(target) == {"model": {"planner": {"model": "x"}}}


def test_read_toml_names_the_file_and_line_on_a_syntax_error(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("[model\nmodel = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        read_toml(target)
    message = str(excinfo.value)
    assert str(target) in message
    assert "line" in message.lower()


def test_user_toml_path_honors_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_toml_path() == tmp_path / "rudra" / "config.toml"


def test_user_toml_path_falls_back_to_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert user_toml_path() == Path.home() / ".config" / "rudra" / "config.toml"


def test_project_toml_layer_reads_the_d15_location(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "config.toml").write_text("[agent]\nverbose = false\n", encoding="utf-8")
    assert project_toml_layer(tmp_path) == {"agent": {"verbose": False}}


def test_env_layer_maps_bare_names_to_the_default_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_MODEL", "bare")
    assert env_layer(("default", "planner", "coder"))["model"]["default"]["model"] == "bare"


def test_env_layer_maps_role_scoped_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_PLANNER_MODEL", "scoped")
    layer = env_layer(("default", "planner", "coder"))
    assert layer["model"]["planner"]["model"] == "scoped"
    assert "default" not in layer.get("model", {})


def test_env_layer_recognises_a_role_declared_only_in_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declaring [model.reviewer] makes RUDRA_REVIEWER_MODEL work, no code change."""
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "judge")
    layer = env_layer(("default", "planner", "coder", "reviewer"))
    assert layer["model"]["reviewer"]["model"] == "judge"


def test_env_layer_without_that_role_does_not_invent_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "judge")
    layer = env_layer(("default", "planner", "coder"))
    assert "reviewer" not in layer.get("model", {})


def test_env_layer_maps_non_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_VERBOSE", "false")
    monkeypatch.setenv("RUDRA_PERMISSIONS_MODE", "auto")
    layer = env_layer(("default",))
    assert layer["agent"]["verbose"] is False
    assert layer["permissions"]["mode"] == "auto"


def test_env_layer_coerces_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_TEMPERATURE", "0.7")
    monkeypatch.setenv("RUDRA_CONTEXT_TOKENS", "32768")
    layer = env_layer(("default",))
    assert layer["model"]["default"]["temperature"] == 0.7
    assert layer["model"]["default"]["context_tokens"] == 32768


def test_bare_verbose_still_works_and_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.42: VERBOSE is unprefixed and collision-prone. One release of grace."""
    monkeypatch.setenv("VERBOSE", "false")
    with pytest.warns(DeprecationWarning, match="RUDRA_VERBOSE"):
        assert env_layer(("default",))["agent"]["verbose"] is False

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        env_layer(("default",))  # second call must not warn again


def test_rudra_verbose_beats_bare_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERBOSE", "false")
    monkeypatch.setenv("RUDRA_VERBOSE", "true")
    assert env_layer(("default",))["agent"]["verbose"] is True


def test_ollama_shim_still_populates_the_default_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """S6.4: the OLLAMA_* shim survives Step 6."""
    monkeypatch.setenv("OLLAMA_MODEL", "legacy:32b")
    with pytest.warns(DeprecationWarning, match="RUDRA_MODEL"):
        layer = env_layer(("default",))
    assert layer["model"]["default"]["model"] == "legacy:32b"


def test_a_modern_name_beats_the_legacy_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "legacy:32b")
    monkeypatch.setenv("RUDRA_MODEL", "modern:32b")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        layer = env_layer(("default",))
    assert layer["model"]["default"]["model"] == "modern:32b"


def test_cli_layer_omits_unset_flags() -> None:
    assert cli_layer(verbose=None, permission_mode=None) == {}


def test_cli_layer_carries_set_flags() -> None:
    layer = cli_layer(verbose=True, permission_mode="auto")
    assert layer == {"agent": {"verbose": True}, "permissions": {"mode": "auto"}}
