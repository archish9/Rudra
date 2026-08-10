"""The shell environment handed to LocalShellBackend (Step 7 spec §5.3).

Closes A1.44: the backend's own default is an EMPTY environment, so every
venv, nvm, rustup, and pyenv toolchain is invisible.
"""

from __future__ import annotations

import os

import pytest

from rudra.config.loader import build_config, reset_config
from rudra.permissions.env import scrubbed_env


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def test_path_survives(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    assert scrubbed_env(build_config(tmp_path))["PATH"] == "/usr/local/bin:/usr/bin"


@pytest.mark.parametrize(
    "name", ["VIRTUAL_ENV", "NVM_DIR", "CARGO_HOME", "HOME", "LANG", "PYENV_ROOT"]
)
def test_toolchain_variables_survive(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "/somewhere")
    assert name in scrubbed_env(build_config(tmp_path))


@pytest.mark.parametrize(
    "name",
    [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GITHUB_TOKEN",
        "MY_SECRET",
        "DB_PASSWORD",
        "SERVICE_CREDENTIALS",
        "AWS_ACCESS_KEY_ID",
        "AWS_SESSION_TOKEN",
    ],
)
def test_secret_shaped_names_are_removed(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "s3cret")
    assert name not in scrubbed_env(build_config(tmp_path))


def test_the_configured_api_key_env_is_removed_by_name(tmp_path, monkeypatch):
    """A key var named something innocuous must still be dropped."""
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "openai_compatible"\napi_key_env = "MY_UNUSUAL_NAME"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_UNUSUAL_NAME", "s3cret")
    assert "MY_UNUSUAL_NAME" not in scrubbed_env(build_config(tmp_path))


def test_no_secret_value_appears_anywhere_in_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-unique-canary-value")
    assert "sk-unique-canary-value" not in repr(scrubbed_env(build_config(tmp_path)))


def test_an_ordinary_variable_mentioning_a_key_is_kept(tmp_path, monkeypatch):
    """KEYBOARD_LAYOUT is not a secret. The regex is anchored for this reason."""
    monkeypatch.setenv("KEYBOARD_LAYOUT", "us")
    assert "KEYBOARD_LAYOUT" in scrubbed_env(build_config(tmp_path))


def test_the_result_is_a_copy_not_the_live_environment(tmp_path):
    env = scrubbed_env(build_config(tmp_path))
    env["ADDED_BY_TEST"] = "x"
    assert "ADDED_BY_TEST" not in os.environ
