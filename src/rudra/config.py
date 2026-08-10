"""Configuration settings for Rudra."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_PROVIDER = "ollama"
# A1.34: was qwen3:14b, below the D6 32B floor that .env.example:9 already used.
_DEFAULT_MODEL = "qwen3:32b"
_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TEMPERATURE = 0.3
_DEFAULT_MAX_OUTPUT_TOKENS = 131072
_DEFAULT_TIMEOUT = 300

_ROLES = ("default", "planner", "coder")

_DEPRECATED_VARS = {
    "OLLAMA_BASE_URL": "RUDRA_BASE_URL",
    "OLLAMA_MODEL": "RUDRA_MODEL",
    "OLLAMA_MODEL_PLANNER": "RUDRA_PLANNER_MODEL",
    "OLLAMA_MODEL_CODER": "RUDRA_CODER_MODEL",
    "OLLAMA_TEMPERATURE": "RUDRA_TEMPERATURE",
    "OLLAMA_TIMEOUT": "RUDRA_TIMEOUT",
    "OLLAMA_NUM_PREDICT": "RUDRA_MAX_OUTPUT_TOKENS",
}

_warned_vars: set[str] = set()
"""Deprecation warnings are deduped explicitly rather than relying on the
warnings module's default per-location filter, so "warn once per variable" is
a property the tests can assert deterministically."""


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to construct one role's chat model.

    `api_key_env` names an environment variable. The key value itself is never
    stored here, never logged, and never written to a config file (C1.5).
    """

    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    temperature: float | None
    context_tokens: int | None
    max_output_tokens: int | None
    timeout: int | None


def _legacy(name: str) -> str | None:
    """Read a deprecated OLLAMA_* variable, warning once per variable."""
    value = os.getenv(name)
    if value is None:
        return None
    if name not in _warned_vars:
        _warned_vars.add(name)
        warnings.warn(
            f"{name} is deprecated; use {_DEPRECATED_VARS[name]} instead. "
            f"OLLAMA_* variables will be removed in the next release.",
            DeprecationWarning,
            stacklevel=4,
        )
    return value


def _env(role: str, suffix: str) -> str | None:
    """Role-specific key first, then the bare key."""
    if role != "default":
        scoped = os.getenv(f"RUDRA_{role.upper()}_{suffix}")
        if scoped is not None:
            return scoped
    return os.getenv(f"RUDRA_{suffix}")


def _legacy_model(role: str) -> str | None:
    if role == "planner":
        return _legacy("OLLAMA_MODEL_PLANNER") or _legacy("OLLAMA_MODEL")
    if role == "coder":
        return _legacy("OLLAMA_MODEL_CODER") or _legacy("OLLAMA_MODEL")
    return _legacy("OLLAMA_MODEL")


def _optional_int(raw: str | None, fallback: int | None) -> int | None:
    return int(raw) if raw is not None else fallback


def _optional_float(raw: str | None, fallback: float | None) -> float | None:
    return float(raw) if raw is not None else fallback


def _resolve_model_config(role: str) -> ModelConfig:
    return ModelConfig(
        provider=_env(role, "PROVIDER") or _DEFAULT_PROVIDER,
        model=_env(role, "MODEL") or _legacy_model(role) or _DEFAULT_MODEL,
        base_url=_env(role, "BASE_URL") or _legacy("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL,
        api_key_env=_env(role, "API_KEY_ENV"),
        temperature=_optional_float(
            _env(role, "TEMPERATURE") or _legacy("OLLAMA_TEMPERATURE"), _DEFAULT_TEMPERATURE
        ),
        context_tokens=_optional_int(_env(role, "CONTEXT_TOKENS"), None),
        max_output_tokens=_optional_int(
            _env(role, "MAX_OUTPUT_TOKENS") or _legacy("OLLAMA_NUM_PREDICT"),
            _DEFAULT_MAX_OUTPUT_TOKENS,
        ),
        timeout=_optional_int(_env(role, "TIMEOUT") or _legacy("OLLAMA_TIMEOUT"), _DEFAULT_TIMEOUT),
    )


def _resolve_all_models() -> dict[str, ModelConfig]:
    """Resolve every role once, at Config construction.

    Resolving here rather than inside model_for preserves the A1.15 semantics
    the existing tests assert: a Config freezes the environment it was built
    from, and only reset_config() picks up later changes.
    """
    return {role: _resolve_model_config(role) for role in _ROLES}


@dataclass
class AgentConfig:
    """Agent execution configuration."""

    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")


@dataclass
class Config:
    """Main configuration container."""

    agent: AgentConfig = field(default_factory=AgentConfig)
    models: dict[str, ModelConfig] = field(default_factory=_resolve_all_models)

    # Paths
    checkpoint_dir: str = ".rudra"

    def model_for(self, role: str) -> ModelConfig:
        """Settings for a role, falling back to `default` for unknown roles.

        Step 9 will call build_model("reviewer") before any reviewer config
        exists. Falling back beats raising, and beats shipping config keys for
        roles that have no consumer yet.
        """
        return self.models.get(role, self.models["default"])

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Config":
        """Load configuration from the environment, reading the project's .env first.

        The .env path is explicit rather than bare `load_dotenv()`, which
        resolves by walking upward from *this module's* directory — so a .env
        anywhere above the installed package leaked into every project the
        user ran Rudra in. See TODO.md A5.1.

        `project_root` is passed explicitly because the CLI accepts
        --project-dir, and the cwd is the project root only when that flag is
        absent (TODO.md A5.2). It defaults to the cwd so tests and subcommands
        that have no project path keep working.

        `override=False` is python-dotenv's default and is deliberately kept:
        a real environment variable still beats .env.
        """
        load_dotenv((project_root or Path.cwd()) / ".env")
        return cls()

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Get the checkpoint directory path for a project."""
        checkpoint_path = project_dir / self.checkpoint_dir
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        return checkpoint_path


_config: Config | None = None


def get_config(project_root: Path | None = None) -> Config:
    """Return the process-wide Config, loading it on first use.

    Replaces the module-level `config = Config.load()`, which froze the entire
    environment at first import of this module. See TODO.md A1.15.

    `project_root` is honored only on the call that actually constructs the
    Config. Every later call returns the cache — which is exactly why cli.py
    must resolve the project path before its first get_config() call
    (TODO.md A5.2).
    """
    global _config
    if _config is None:
        _config = Config.load(project_root)
    return _config


def reset_config() -> None:
    """Drop the cached Config so the next get_config() re-reads the environment.

    Test-support only. Also clears the deprecation-warning dedupe set, so a
    test asserting "warns once" is not silenced by an earlier test.
    """
    global _config
    _config = None
    _warned_vars.clear()
