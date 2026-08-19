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
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["git-dir"]\n')
    assert build_config(root).permissions.floor_disable == ("git-dir",)


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


def test_memory_is_no_longer_reserved(tmp_path):
    # [skills] was reserved here until Step 11b implemented it, [mcp] until
    # Step 13 did, and [memory] until Step 14a did. Reserved-to-real is the
    # transition worth covering, so each row moves here rather than going away.
    root = write_config(tmp_path, '[memory]\nbackend = "sqlite"\n')
    assert build_config(root).memory.backend == "sqlite"
    reset_config()


def test_mcp_is_no_longer_reserved(tmp_path):
    root = write_config(tmp_path, "[mcp]\nenabled = false\n")
    assert build_config(root).mcp.enabled is False
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

    write_config(tmp_path, "[memory]\nx = 1\n")
    result = CliRunner().invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert "[memory]" in result.output


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


def test_outside_root_cannot_be_disabled(tmp_path):
    """A1.50: accepting the name and not honouring it is worse than rejecting."""
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside-root"]\n')
    with pytest.raises(ConfigError, match="cannot contain 'outside-root'"):
        build_config(root)


def test_the_outside_root_error_explains_itself_and_names_the_alternatives(tmp_path):
    """Rejecting a setting is only better than ignoring it if you say why.

    Asserted on properties rather than wording: it must say the disabling
    would have no effect, list what CAN be disabled, and point somewhere a
    user can actually read — not at the internal ledger.
    """
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["outside-root"]\n')
    try:
        build_config(root)
    except ConfigError as exc:
        message = str(exc)
        assert "would change nothing" in message
        assert "git-dir" in message and "catastrophic-command" in message
        assert "Documentation/" in message
        assert "TODO.md" not in message, "internal ledger reference leaked to the user"
    else:
        raise AssertionError("expected ConfigError")


def test_no_user_facing_message_cites_the_internal_ledger():
    """TODO.md is a developer artifact; users have no access to it."""
    import inspect

    from rudra import cli

    source = inspect.getsource(cli)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "TODO.md" not in line:
            continue
        assert '"' not in line and "'" not in line, f"user-facing string cites TODO.md: {stripped}"


def test_the_other_floor_rules_are_still_disableable(tmp_path):
    root = write_config(
        tmp_path, '[permissions]\nfloor_disable = ["git-dir", "catastrophic-command"]\n'
    )
    assert build_config(root).permissions.floor_disable == ("git-dir", "catastrophic-command")


def test_shell_in_auto_defaults_off(tmp_path):
    assert build_config(tmp_path).tools.shell_in_auto is False


def test_shell_in_auto_is_read_from_toml(tmp_path):
    root = write_config(tmp_path, "[tools]\nshell_in_auto = true\n")
    assert build_config(root).tools.shell_in_auto is True


def test_env_layer_sets_shell_in_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("RUDRA_SHELL_IN_AUTO", "true")
    assert build_config(tmp_path).tools.shell_in_auto is True


# --- Step 8: auto_branch and test_timeout ---


def test_tools_defaults_include_auto_branch_and_test_timeout(tmp_path):
    cfg = build_config(tmp_path)
    assert cfg.tools.auto_branch is False
    assert cfg.tools.test_timeout == 600


def test_auto_branch_is_read_from_toml(tmp_path):
    root = write_config(tmp_path, "[tools]\nauto_branch = true\n")
    assert build_config(root).tools.auto_branch is True


def test_test_timeout_accepts_an_integer(tmp_path):
    root = write_config(tmp_path, "[tools]\ntest_timeout = 90\n")
    assert build_config(root).tools.test_timeout == 90


def test_test_timeout_rejects_a_bool(tmp_path):
    # isinstance(True, int) is True in Python, so bool must be excluded
    # explicitly or `test_timeout = true` silently becomes a 1-second timeout.
    root = write_config(tmp_path, "[tools]\ntest_timeout = true\n")
    with pytest.raises(ConfigError, match="whole number of seconds"):
        build_config(root)


def test_test_timeout_rejects_zero(tmp_path):
    root = write_config(tmp_path, "[tools]\ntest_timeout = 0\n")
    with pytest.raises(ConfigError, match="greater than 0"):
        build_config(root)


def test_test_timeout_rejects_a_negative_value(tmp_path):
    root = write_config(tmp_path, "[tools]\ntest_timeout = -5\n")
    with pytest.raises(ConfigError, match="greater than 0"):
        build_config(root)


def test_test_timeout_rejects_a_string(tmp_path):
    root = write_config(tmp_path, '[tools]\ntest_timeout = "600"\n')
    with pytest.raises(ConfigError, match="whole number of seconds"):
        build_config(root)


def test_auto_branch_rejects_a_string(tmp_path):
    root = write_config(tmp_path, '[tools]\nauto_branch = "yes"\n')
    with pytest.raises(ConfigError, match="true or false"):
        build_config(root)


def test_unknown_tools_key_still_suggests_the_nearest_name(tmp_path):
    root = write_config(tmp_path, "[tools]\nauto_brnch = true\n")
    with pytest.raises(ConfigError, match="auto_branch"):
        build_config(root)


def test_env_layer_sets_auto_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("RUDRA_AUTO_BRANCH", "true")
    assert build_config(tmp_path).tools.auto_branch is True


def test_test_timeout_env_var_does_not_become_a_model_role(tmp_path, monkeypatch):
    # _split_role_and_suffix would read TEST_TIMEOUT as role "test" plus
    # suffix "timeout" -- a real MODEL_KEY -- and invent a [model.test].
    monkeypatch.setenv("RUDRA_TEST_TIMEOUT", "45")
    cfg = build_config(tmp_path)
    assert cfg.tools.test_timeout == 45
    assert "test" not in cfg.models
