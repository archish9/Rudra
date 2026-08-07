"""Guards A1.15 and A5.1 — configuration was frozen at import, from the wrong .env.

A1.15: config.py ended with `config = Config.load()`, so every
`field(default_factory=lambda: os.getenv(...))` was evaluated the first time
`rudra.config` was imported. Nothing could change the environment afterwards.

A5.1: config.py:9 called bare `load_dotenv()`, which walks upward from the
*calling module's* directory. A `.env` anywhere above the installed package was
therefore loaded for any process, whatever directory it ran in — which is how
Step 1's smoke run, launched from a tempdir outside the repo, still picked up
the repo's own .env and sent num_predict=-1 to the model.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import rudra.config
from rudra.config import get_config, reset_config

OLLAMA_ENV_VARS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_MODEL_PLANNER",
    "OLLAMA_MODEL_CODER",
    "OLLAMA_TEMPERATURE",
    "OLLAMA_TIMEOUT",
    "OLLAMA_NUM_PREDICT",
)


@pytest.fixture(autouse=True)
def clean_config_state(monkeypatch: pytest.MonkeyPatch):
    """Every test starts with no cached Config and no inherited OLLAMA_* vars.

    Without the delenv loop these tests pass or fail based on the developer's
    own shell, which is exactly the class of bug A5.1 describes.
    """
    for name in OLLAMA_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def test_get_config_is_cached() -> None:
    assert get_config() is get_config()


def test_reset_config_rereads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.15: an env change after import must be visible, which it was not."""
    monkeypatch.setenv("OLLAMA_MODEL", "first-model")
    assert get_config().ollama.model == "first-model"

    monkeypatch.setenv("OLLAMA_MODEL", "second-model")
    assert get_config().ollama.model == "first-model", "cache should hold until reset"

    reset_config()
    assert get_config().ollama.model == "second-model"


def test_there_is_no_module_level_config() -> None:
    """An alias would still be evaluated at import time by whoever binds it."""
    assert not hasattr(rudra.config, "config")


def test_dotenv_is_looked_up_at_the_cwd_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5.1: the .env path must be explicit, not resolved by an upward walk.

    This one asserts the call, not an observable value, and that is deliberate.
    The original defect was that bare load_dotenv() resolves relative to
    config.py's own location inside site-packages — reproducing it behaviorally
    would mean planting a .env above the installed package, which no test should
    do to a developer's machine. What is checkable, and what actually fixes it,
    is that exactly one explicit path is passed and it is derived from the cwd.
    """
    recorded: list[object] = []

    def spy(*args: object, **kwargs: object) -> bool:
        recorded.append(args[0] if args else kwargs.get("dotenv_path"))
        return False

    monkeypatch.setattr(rudra.config, "load_dotenv", spy)
    monkeypatch.chdir(tmp_path)

    reset_config()
    get_config()

    assert recorded == [tmp_path / ".env"]


def test_dotenv_in_the_cwd_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The converse — guards against over-correcting into ignoring .env entirely."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reset_config()
    assert get_config().ollama.num_predict == 4096


def test_a_real_env_var_beats_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """override=False is retained: the documented escape hatch must keep working."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "8192")

    reset_config()
    assert get_config().ollama.num_predict == 8192
    assert os.environ["OLLAMA_NUM_PREDICT"] == "8192"
