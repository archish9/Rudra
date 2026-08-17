"""Step 11b's config surface: the un-reserved [skills] section."""

from __future__ import annotations

import os

import pytest

from rudra.config.loader import ConfigError, build_config, reset_config
from rudra.skills.registry import DEFAULT_ENABLED


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


def test_skills_defaults_to_the_shipped_set(tmp_path):
    cfg = build_config(write_config(tmp_path, "[agent]\nmax_questions = 2\n"))

    assert set(cfg.skills.enabled) == set(DEFAULT_ENABLED)


def test_skills_section_is_no_longer_reserved(tmp_path):
    cfg = build_config(write_config(tmp_path, '[skills]\nenabled = ["brainstorming"]\n'))

    assert cfg.skills.enabled == ("brainstorming",)


def test_an_empty_enabled_list_is_allowed(tmp_path):
    """The off switch. S11b.5: one key does both jobs."""
    cfg = build_config(write_config(tmp_path, "[skills]\nenabled = []\n"))

    assert cfg.skills.enabled == ()


def test_an_unknown_skill_name_is_a_hard_error(tmp_path):
    """A typo must not mean a skill that silently never loads."""
    with pytest.raises(ConfigError) as excinfo:
        build_config(write_config(tmp_path, '[skills]\nenabled = ["brainstorm"]\n'))

    message = str(excinfo.value)
    assert "brainstorm" in message
    assert "brainstorming" in message


def test_an_unknown_key_in_skills_is_a_hard_error(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        build_config(write_config(tmp_path, "[skills]\nenable = true\n"))

    assert "enable" in str(excinfo.value)


def test_enabled_must_be_a_list(tmp_path):
    with pytest.raises(ConfigError):
        build_config(write_config(tmp_path, '[skills]\nenabled = "brainstorming"\n'))


def test_an_off_by_default_skill_may_be_enabled(tmp_path):
    """All 14 are vendored; enabling one is a config change, not a rebuild."""
    cfg = build_config(write_config(tmp_path, '[skills]\nenabled = ["using-git-worktrees"]\n'))

    assert cfg.skills.enabled == ("using-git-worktrees",)
