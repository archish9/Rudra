"""Guards A1.15, A5.1, A5.2, A1.34, and C1.3 — configuration correctness.

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

RUDRA_ENV_VARS = tuple(
    f"RUDRA_{prefix}{suffix}"
    for prefix in ("", "PLANNER_", "CODER_")
    for suffix in (
        "PROVIDER",
        "MODEL",
        "BASE_URL",
        "API_KEY_ENV",
        "TEMPERATURE",
        "CONTEXT_TOKENS",
        "MAX_OUTPUT_TOKENS",
        "TIMEOUT",
    )
)

ALL_ENV_VARS = (*OLLAMA_ENV_VARS, *RUDRA_ENV_VARS)


@pytest.fixture(autouse=True)
def clean_config_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test starts with no cached Config and no inherited OLLAMA_* vars.

    Without the delenv loop these tests pass or fail based on the developer's
    own shell, which is exactly the class of bug A5.1 describes.

    The chdir closes the other half of that gap, and it is not optional.
    Deleting the variables is not enough: Config.load() calls load_dotenv()
    against the *cwd*, so running from the repo root re-populates every
    RUDRA_* and OLLAMA_* name straight back out of the developer's own
    gitignored .env, after this fixture has just removed them. Starting each
    test in an empty directory means "unset" actually means unset. Tests that
    want a .env write one into their own tmp_path and chdir to it, which
    overrides this.

    The post-yield restore below is direct os.environ manipulation, not
    monkeypatch.delenv/setenv. Config.load() calls the real load_dotenv(),
    which writes straight to os.environ — monkeypatch never sees that write
    and so records no undo entry for it. monkeypatch.delenv(raising=False)
    on a var that was absent when the test started is the same story: no
    prior value, no undo entry. Either way, monkeypatch's own teardown has
    nothing to roll back, and the var survives into every later test in the
    session — the exact leak this fixture's docstring claims to prevent.
    Snapshotting and restoring the real values ourselves closes that gap
    regardless of how the variable got there.
    """
    original = {name: os.environ.get(name) for name in ALL_ENV_VARS}
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def test_get_config_is_cached() -> None:
    assert get_config() is get_config()


def test_reset_config_rereads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.15: an env change after import must be visible, which it was not."""
    monkeypatch.setenv("OLLAMA_MODEL", "first-model")
    assert get_config().model_for("default").model == "first-model"

    monkeypatch.setenv("OLLAMA_MODEL", "second-model")
    assert get_config().model_for("default").model == "first-model", "cache should hold until reset"

    reset_config()
    assert get_config().model_for("default").model == "second-model"


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

    # Patched where the name is actually bound, which moves as the package is
    # built out: `rudra.config` re-exports the surface, but load_dotenv is
    # called inside the module that owns loading.
    import rudra.config.loader as loading_module

    monkeypatch.setattr(loading_module, "load_dotenv", spy)
    monkeypatch.chdir(tmp_path)

    reset_config()
    get_config()

    assert recorded == [tmp_path / ".env"]


def test_dotenv_in_the_cwd_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The converse — guards against over-correcting into ignoring .env entirely."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reset_config()
    assert get_config().model_for("default").max_output_tokens == 4096


def test_a_real_env_var_beats_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """override=False is retained: the documented escape hatch must keep working."""
    (tmp_path / ".env").write_text("OLLAMA_NUM_PREDICT=4096\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "8192")

    reset_config()
    assert get_config().model_for("default").max_output_tokens == 8192
    assert os.environ["OLLAMA_NUM_PREDICT"] == "8192"


def test_defaults_are_ollama_at_32b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.34: the coded default was qwen3:14b, below the D6 32B floor, while
    .env.example:9 said qwen3:32b. A user with no .env silently ran a 14B
    model against a codebase whose 14B workarounds were deleted in Step 2.

    chdir to an empty directory first: run from the repo root, Config.load
    reads the developer's own gitignored .env and this stops testing the
    coded defaults at all. CI has no .env and would pass either way, which
    is exactly the kind of only-fails-on-someone-else's-machine gap A5.1
    was about.
    """
    monkeypatch.chdir(tmp_path)
    reset_config()

    settings = get_config().model_for("default")

    assert settings.provider == "ollama"
    assert settings.model == "qwen3:32b"
    assert settings.base_url == "http://localhost:11434"


def test_role_specific_keys_beat_the_bare_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_MODEL", "shared-model")
    monkeypatch.setenv("RUDRA_PLANNER_MODEL", "planner-model")
    reset_config()

    assert get_config().model_for("planner").model == "planner-model"
    assert get_config().model_for("coder").model == "shared-model"
    assert get_config().model_for("default").model == "shared-model"


def test_unknown_roles_fall_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 9 calls build_model("reviewer") before any reviewer config exists.
    Falling back beats raising, and beats shipping dead config now."""
    monkeypatch.setenv("RUDRA_MODEL", "shared-model")
    reset_config()

    assert get_config().model_for("reviewer").model == "shared-model"
    assert get_config().model_for("summarizer").model == "shared-model"


def test_every_setting_is_role_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_CODER_PROVIDER", "anthropic")
    monkeypatch.setenv("RUDRA_CODER_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("RUDRA_CODER_BASE_URL", "https://example.test")
    monkeypatch.setenv("RUDRA_CODER_API_KEY_ENV", "MY_KEY_VAR")
    monkeypatch.setenv("RUDRA_CODER_TEMPERATURE", "0.7")
    monkeypatch.setenv("RUDRA_CODER_CONTEXT_TOKENS", "200000")
    monkeypatch.setenv("RUDRA_CODER_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("RUDRA_CODER_TIMEOUT", "120")
    reset_config()

    coder = get_config().model_for("coder")

    assert coder.provider == "anthropic"
    assert coder.model == "claude-sonnet-4-5"
    assert coder.base_url == "https://example.test"
    assert coder.api_key_env == "MY_KEY_VAR"
    assert coder.temperature == 0.7
    assert coder.context_tokens == 200000
    assert coder.max_output_tokens == 8192
    assert coder.timeout == 120


def test_legacy_ollama_vars_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """C1.3: one release of backwards compatibility."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://legacy:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    monkeypatch.setenv("OLLAMA_MODEL_PLANNER", "legacy-planner")
    monkeypatch.setenv("OLLAMA_MODEL_CODER", "legacy-coder")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.9")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "42")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "2048")
    reset_config()

    cfg = get_config()

    assert cfg.model_for("default").base_url == "http://legacy:11434"
    assert cfg.model_for("default").model == "legacy-model"
    assert cfg.model_for("planner").model == "legacy-planner"
    assert cfg.model_for("coder").model == "legacy-coder"
    assert cfg.model_for("default").temperature == 0.9
    assert cfg.model_for("default").timeout == 42
    assert cfg.model_for("default").max_output_tokens == 2048


def test_legacy_vars_warn_once_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir first, or the repo's own gitignored .env re-populates the other
    six OLLAMA_* variables during Config.load and each one warns too, making
    the count assertion below fail on a developer's machine and pass in CI.

    VERBOSE is deleted for the same reason, one variable further out: it is
    also a deprecated name, so a developer with it exported gets a second
    warning here. That went unnoticed while some earlier test in the suite
    happened to consume VERBOSE's warn-once first — an ordering dependency,
    not a property, and it broke the moment a new test reset the dedupe set
    in its teardown.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VERBOSE", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    reset_config()

    with pytest.warns(DeprecationWarning) as record:
        get_config().model_for("planner")
        get_config().model_for("coder")

    messages = [str(w.message) for w in record]
    assert len(messages) == 1, f"expected one warning per variable, got {messages}"
    assert "OLLAMA_MODEL" in messages[0]
    assert "RUDRA_MODEL" in messages[0]


def test_rudra_vars_beat_legacy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    monkeypatch.setenv("RUDRA_MODEL", "new-model")
    reset_config()

    assert get_config().model_for("default").model == "new-model"


def test_dotenv_follows_the_project_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5.2: `rudra -d /path/to/proj` run from elsewhere must read the
    project's .env, not the invocation directory's. A5.1 fixed the upward
    walk but keyed the lookup to Path.cwd(), which is the project root only
    when -d is absent."""
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".env").write_text("RUDRA_MODEL=from-project\n", encoding="utf-8")
    (elsewhere / ".env").write_text("RUDRA_MODEL=from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    reset_config()
    assert get_config(project).model_for("default").model == "from-project"


def test_dotenv_still_defaults_to_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-argument path must keep working for tests and subcommands."""
    (tmp_path / ".env").write_text("RUDRA_MODEL=from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reset_config()
    assert get_config().model_for("default").model == "from-cwd"


def test_ollama_config_is_gone() -> None:
    """C1.3: the class is deleted, not deprecated in place."""
    assert not hasattr(rudra.config, "OllamaConfig")
