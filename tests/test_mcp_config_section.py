"""[mcp] — un-reserved in Step 13, validated like every other section."""

import pytest

from rudra.config.layers import cli_layer
from rudra.config.loader import ConfigError, build_config


def write_config(tmp_path, body):
    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rudra" / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_defaults(tmp_path):
    cfg = build_config(tmp_path)
    assert cfg.mcp.enabled is True
    assert cfg.mcp.mcp_in_auto is False
    assert cfg.mcp.timeout == 60
    assert cfg.mcp.allow == ()
    assert cfg.mcp.deny == ()
    assert cfg.mcp.readonly == ()
    assert cfg.mcp.disabled_servers == ()


def test_section_is_accepted_now(tmp_path):
    root = write_config(tmp_path, '[mcp]\nenabled = false\nallow = ["kala__*"]\n')
    cfg = build_config(root)
    assert cfg.mcp.enabled is False
    assert cfg.mcp.allow == ("kala__*",)


def test_unknown_key_is_fatal_and_suggests(tmp_path):
    root = write_config(tmp_path, "[mcp]\nenable = false\n")
    with pytest.raises(ConfigError) as excinfo:
        build_config(root)
    assert "enabled" in str(excinfo.value)


def test_timeout_must_be_a_positive_int(tmp_path):
    root = write_config(tmp_path, "[mcp]\ntimeout = 0\n")
    with pytest.raises(ConfigError):
        build_config(root)


def test_timeout_rejects_a_bool(tmp_path):
    # bool is a subclass of int, so `timeout = true` would become 1 second.
    root = write_config(tmp_path, "[mcp]\ntimeout = true\n")
    with pytest.raises(ConfigError):
        build_config(root)


def test_pattern_lists_must_be_strings(tmp_path):
    root = write_config(tmp_path, "[mcp]\nallow = [3]\n")
    with pytest.raises(ConfigError):
        build_config(root)


def test_allow_mcp_flag_reaches_the_cli_layer():
    assert cli_layer(None, None, None, allow_mcp=True) == {"mcp": {"mcp_in_auto": True}}
    assert "mcp" not in cli_layer(None, None, None, allow_mcp=None)
