"""OPEN-6: an API key may be written into config.toml, and never displayed.

Before this, `api_key_env` was the only way to supply a key and it holds a
variable *name*. That pushed every user toward a project-level `.env` --
a per-project file for what is almost always a per-machine secret -- and
the owner hit the confusing `MissingApiKeyError` that results from pasting
the key into the name field.

The rule that replaced "never in TOML" is narrower and holds in both
directions: a key may be *stored*, and is never *shown*. These tests pin
both halves, plus the one distinction that matters -- the global config is
in nobody's repository, the project config is documented as committable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rudra.config import ModelConfig, committed_api_key_notice, reset_config
from rudra.config.layers import ConfigError
from rudra.config.loader import build_config

SECRET = "nvapi-" + "z" * 60


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No inherited RUDRA_*, no ambient .env, no cached Config.

    Same reasoning as test_config.py's fixture: without the chdir,
    build_config() calls load_dotenv() against the cwd and repopulates the
    variables this test just cleared out of the developer's own .env.
    """
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _openai_role(**overrides: object) -> str:
    """A [model.default] block for a provider that actually needs a key."""
    lines = [
        "[model.default]",
        'provider = "openai_compatible"',
        'model = "some-model"',
        'base_url = "https://example.invalid/v1"',
    ]
    for key, value in overrides.items():
        lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# the key resolves, from every layer
# --------------------------------------------------------------------------


def test_a_key_written_in_the_project_config_is_used(tmp_path: Path) -> None:
    """The owner's actual request: put the key in config.toml, have it work."""
    _write(tmp_path / ".rudra" / "config.toml", _openai_role(api_key=SECRET))

    cfg = build_config(tmp_path)

    assert cfg.model_for("planner").api_key == SECRET, "inherited from [model.default]"


def test_a_key_written_in_the_global_config_is_used(tmp_path: Path) -> None:
    """The documented home for it -- ~/.config/rudra/, in nobody's repo."""
    _write(tmp_path / "xdg" / "rudra" / "config.toml", _openai_role(api_key=SECRET))

    assert build_config(tmp_path).model_for("coder").api_key == SECRET


def test_the_env_layer_carries_a_literal_key_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MODEL_KEYS is derived from ModelConfig's fields, so RUDRA_API_KEY
    works with no change to the env layer -- this pins that it stays true."""
    _write(tmp_path / ".rudra" / "config.toml", _openai_role())
    monkeypatch.setenv("RUDRA_API_KEY", SECRET)

    assert build_config(tmp_path).model_for("default").api_key == SECRET


def test_a_per_role_key_overrides_the_default_one(tmp_path: Path) -> None:
    body = _openai_role(api_key=SECRET) + f'\n[model.coder]\napi_key = "{SECRET}-coder"\n'
    _write(tmp_path / ".rudra" / "config.toml", body)

    cfg = build_config(tmp_path)

    assert cfg.model_for("coder").api_key == f"{SECRET}-coder"
    assert cfg.model_for("planner").api_key == SECRET


# --------------------------------------------------------------------------
# precedence against api_key_env
# --------------------------------------------------------------------------


def test_a_literal_key_beats_the_named_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who wrote the key into config meant that key. Silently
    preferring a stale environment variable would be exactly the "which
    source won?" defect the loader exists to prevent."""
    from rudra.llm.factory import _resolve_api_key

    monkeypatch.setenv("SOME_VAR", "the-environment-one")
    settings = ModelConfig(
        provider="openai_compatible",
        model="m",
        base_url=None,
        api_key_env="SOME_VAR",
        temperature=None,
        context_tokens=None,
        max_output_tokens=None,
        timeout=None,
        api_key=SECRET,
    )

    assert _resolve_api_key("planner", settings) == SECRET


def test_the_named_variable_still_works_when_no_literal_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-OPEN-6 path, unchanged. This is the regression guard."""
    from rudra.llm.factory import _resolve_api_key

    monkeypatch.setenv("SOME_VAR", "the-environment-one")
    settings = ModelConfig(
        provider="openai_compatible",
        model="m",
        base_url=None,
        api_key_env="SOME_VAR",
        temperature=None,
        context_tokens=None,
        max_output_tokens=None,
        timeout=None,
    )

    assert _resolve_api_key("planner", settings) == "the-environment-one"


# --------------------------------------------------------------------------
# stored, never shown
# --------------------------------------------------------------------------


def test_the_key_is_absent_from_the_dataclass_repr() -> None:
    """`repr=False` makes this structural. A rule that each log call site
    has to remember is the kind that A1.81 already caught failing once."""
    settings = ModelConfig(
        provider="openai_compatible",
        model="m",
        base_url=None,
        api_key_env=None,
        temperature=None,
        context_tokens=None,
        max_output_tokens=None,
        timeout=None,
        api_key=SECRET,
    )

    assert SECRET not in repr(settings)
    assert settings.api_key == SECRET, "hidden from the repr, not from the code"


def test_config_list_masks_the_key(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from rudra.cli import app

    _write(tmp_path / ".rudra" / "config.toml", _openai_role(api_key=SECRET))

    result = CliRunner().invoke(app, ["config", "list", "-d", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    # Not even a fragment: a partial leak is still a leak (A1.81's rule).
    assert not any(SECRET[i : i + 20] in result.output for i in range(len(SECRET) - 20))
    assert "api_key" in result.output, "the row must still exist, so it is visibly SET"


def test_config_get_masks_the_key(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from rudra.cli import app

    _write(tmp_path / ".rudra" / "config.toml", _openai_role(api_key=SECRET))

    result = CliRunner().invoke(
        app, ["config", "get", "model.default.api_key", "-d", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output


def test_a_non_string_key_is_rejected_without_echoing_it(tmp_path: Path) -> None:
    """Every other type check in validate() interpolates the value. This one
    must not -- naming the type is enough to fix a mistyped key."""
    _write(tmp_path / ".rudra" / "config.toml", _openai_role() + "api_key = 12345678\n")

    with pytest.raises(ConfigError) as caught:
        build_config(tmp_path)

    assert "12345678" not in str(caught.value)
    assert "int" in str(caught.value)


# --------------------------------------------------------------------------
# the project/global distinction
# --------------------------------------------------------------------------


def test_a_key_in_the_project_config_warns(tmp_path: Path) -> None:
    """README.md tells users to commit .rudra/config.toml."""
    _write(tmp_path / ".rudra" / "config.toml", _openai_role(api_key=SECRET))

    notice = committed_api_key_notice(build_config(tmp_path))

    assert notice is not None
    assert "commit" in notice
    assert SECRET not in notice


def test_a_key_in_the_global_config_does_not_warn(tmp_path: Path) -> None:
    """~/.config/rudra/config.toml is in nobody's repository -- same shape
    as ~/.aws/credentials. Warning here would be noise on every run."""
    _write(tmp_path / "xdg" / "rudra" / "config.toml", _openai_role(api_key=SECRET))

    assert committed_api_key_notice(build_config(tmp_path)) is None


def test_no_key_at_all_does_not_warn(tmp_path: Path) -> None:
    _write(tmp_path / ".rudra" / "config.toml", _openai_role(api_key_env="SOME_VAR"))

    assert committed_api_key_notice(build_config(tmp_path)) is None
