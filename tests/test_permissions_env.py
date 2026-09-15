"""The shell environment handed to LocalShellBackend (Step 7 spec §5.3).

Closes A1.44: the backend's own default is an EMPTY environment, so every
venv, nvm, rustup, and pyenv toolchain is invisible.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from rudra.config.loader import build_config, reset_config
from rudra.permissions.env import PIP_REQUIRE_VIRTUALENV, scrubbed_env


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


# --- OPEN-80: the child must not inherit RUDRA's Python -------------------
#
# `shutil.which("python3")` inside an agent's shell resolved to Rudra's own
# interpreter, because Rudra runs from its own virtualenv and puts that bin
# directory first on PATH. `pip install` did too, permanently: nine
# distributions from an August Flask run were still in Rudra's `.venv` on
# 2026-09-02, every one recording `pip` as its installer against a lock file
# that names none of them.
#
# The rule is narrow on purpose. This is not a sandbox and A1.44 is still in
# force: every other venv, nvm, rustup and pyenv path survives, because
# removing them is what made the toolchain invisible in the first place.
# Exactly one environment is taken away -- Rudra's own.


def _rudra_bin() -> str:
    return str(Path(sys.executable).parent)


def test_rudras_own_bin_is_removed_from_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([_rudra_bin(), "/usr/local/bin", "/usr/bin"]))
    path = scrubbed_env(build_config(tmp_path))["PATH"].split(os.pathsep)
    assert _rudra_bin() not in path
    assert path == ["/usr/local/bin", "/usr/bin"]


def test_every_other_path_entry_survives(tmp_path, monkeypatch):
    """A1.44: this is a real-toolchains-work default, not a sandbox."""
    entries = ["/opt/homebrew/bin", "/some/project/.venv/bin", "/usr/bin"]
    monkeypatch.setenv("PATH", os.pathsep.join(entries))
    assert scrubbed_env(build_config(tmp_path))["PATH"].split(os.pathsep) == entries


def test_virtual_env_is_dropped_when_it_names_rudras_own(tmp_path, monkeypatch):
    """`pip` reads VIRTUAL_ENV, so leaving it is leaving the install target."""
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(sys.executable).parent.parent))
    assert "VIRTUAL_ENV" not in scrubbed_env(build_config(tmp_path))


def test_virtual_env_survives_when_it_names_someone_elses(tmp_path, monkeypatch):
    """The user's own project venv is exactly what A1.44 exists to keep."""
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "project" / ".venv"))
    assert "VIRTUAL_ENV" in scrubbed_env(build_config(tmp_path))


def test_a_path_entry_spelled_differently_is_still_removed(tmp_path, monkeypatch):
    """Both spellings are compared, for A1.58's reason: on macOS a path behind
    a symlink and its resolved form differ, and a miss reads as 'not Rudra'."""
    monkeypatch.setenv("PATH", os.pathsep.join([_rudra_bin() + "/.", "/usr/bin"]))
    assert scrubbed_env(build_config(tmp_path))["PATH"] == "/usr/bin"


# --- OPEN-120: pip may not install into an interpreter outside a venv -----
#
# Run a04f89bd2ed6's tester ran `pip3 install Flask==3.0.3 SQLAlchemy==2.0.29`
# in a project with no venv. OPEN-80 had taken Rudra's own venv off PATH, so
# `pip3` meant the MACHINE's Python, and it downgraded the user's Flask and
# SQLAlchemy. pip's own switch refuses that for `pip`, `pip3`, `python -m pip`
# and anything that shells out to one.


def test_pip_is_told_to_require_a_virtualenv(tmp_path):
    assert scrubbed_env(build_config(tmp_path))[PIP_REQUIRE_VIRTUALENV] == "1"


def test_a_user_exported_zero_does_not_switch_it_off(tmp_path, monkeypatch):
    """Owner's call, 2026-09-15: a floor, not a default."""
    monkeypatch.setenv(PIP_REQUIRE_VIRTUALENV, "0")
    assert scrubbed_env(build_config(tmp_path))[PIP_REQUIRE_VIRTUALENV] == "1"
