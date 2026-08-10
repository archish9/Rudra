"""Step 7's config surface: floor_disable and the un-reserved [tools]."""

from __future__ import annotations

import os

import pytest

from rudra.config.loader import ConfigError, build_config, reset_config
from rudra.config.schema import FLOOR_RULE_NAMES


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body: str):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_floor_disable_defaults_to_empty(tmp_path):
    assert build_config(tmp_path).permissions.floor_disable == ()


def test_floor_disable_is_read_from_toml(tmp_path):
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside-root"]\n')
    assert build_config(root).permissions.floor_disable == ("outside-root",)


def test_an_unknown_floor_rule_name_is_a_config_error(tmp_path):
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside_root"]\n')
    with pytest.raises(ConfigError, match="Did you mean 'outside-root'"):
        build_config(root)


def test_floor_disable_must_be_a_list_of_strings(tmp_path):
    root = write_config(tmp_path, "[permissions]\nfloor_disable = true\n")
    with pytest.raises(ConfigError, match="must be a list of strings"):
        build_config(root)


def test_the_schema_names_the_same_floor_rules_the_floor_module_does():
    """schema.py must not import rudra.permissions — this test keeps them honest.

    Same pattern as VALID_PROVIDERS, declared literally for the same reason
    and guarded by an equivalent test.
    """
    from rudra.permissions.floor import FLOOR_RULE_NAMES as REAL

    assert FLOOR_RULE_NAMES == REAL


def test_tools_section_is_no_longer_reserved(tmp_path):
    root = write_config(tmp_path, "[tools]\nshell = false\n")
    assert build_config(root).tools.shell is False


def test_tools_defaults_to_shell_enabled(tmp_path):
    assert build_config(tmp_path).tools.shell is True


def test_an_unknown_tools_key_is_a_config_error(tmp_path):
    root = write_config(tmp_path, "[tools]\nshel = false\n")
    with pytest.raises(ConfigError, match="Did you mean 'shell'"):
        build_config(root)


def test_no_inert_output_limit_key_ships(tmp_path):
    """A1.47: the threshold is unreachable, so it must not read as settable."""
    root = write_config(tmp_path, "[tools]\ntool_output_limit_tokens = 4000\n")
    with pytest.raises(ConfigError):
        build_config(root)


def test_shell_must_be_a_bool(tmp_path):
    root = write_config(tmp_path, '[tools]\nshell = "yes"\n')
    with pytest.raises(ConfigError, match="must be true or false"):
        build_config(root)


def test_skills_and_memory_stay_reserved(tmp_path):
    for section, step in (("skills", "Step 11"), ("memory", "Step 14")):
        root = write_config(tmp_path, f"[{section}]\nx = 1\n")
        with pytest.raises(ConfigError, match=step):
            build_config(root)
        reset_config()


def test_env_layer_sets_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("RUDRA_SHELL", "false")
    assert build_config(tmp_path).tools.shell is False


def test_a_config_error_still_names_its_section_on_screen(tmp_path):
    """A1.48: Rich parses `[tools]` as a style tag and prints nothing.

    Asserted through the rendered CLI output, not the exception's str().
    Every existing config test asserts the latter, which is why the whole
    suite passed while every one of these messages was broken on screen.
    """
    from typer.testing import CliRunner

    from rudra.cli import app

    write_config(tmp_path, "[tools]\nshel = false\n")
    result = CliRunner().invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert "[tools]" in result.output


def test_a_reserved_section_error_names_the_section_on_screen(tmp_path):
    from typer.testing import CliRunner

    from rudra.cli import app

    write_config(tmp_path, "[skills]\nx = 1\n")
    result = CliRunner().invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert "[skills]" in result.output


def test_every_new_key_is_visible_to_config_list(tmp_path):
    """A config value the user cannot inspect is half-shipped."""
    from rudra.cli import _flatten

    keys = {key for key, _ in _flatten(build_config(tmp_path))}
    assert "permissions.floor_disable" in keys
    assert "tools.shell" in keys


def test_the_init_template_round_trips_through_the_loader(tmp_path):
    """rudra init must scaffold a file the loader accepts (Step 6's rule)."""
    from rudra.config.template import CONFIG_TEMPLATE

    root = write_config(tmp_path, CONFIG_TEMPLATE)
    cfg = build_config(root)
    assert cfg.permissions.floor_disable == ()
    assert cfg.tools.shell is True
