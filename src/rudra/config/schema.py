"""Configuration data: the dataclasses, the builtin defaults, the valid values.

This module imports nothing from Rudra except the provider registry, so it
can be read and tested without any agent machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

BUILTIN_ROLES = ("default", "planner", "coder")

# Declared literally rather than derived from `rudra.llm.providers.PROVIDERS`,
# because importing it here would be circular: rudra.llm's __init__ imports
# factory, which imports rudra.config. The two are kept in agreement by
# tests/test_config_schema.py::test_every_provider_the_factory_supports_is_valid_here,
# which fails the moment a provider is added to one and not the other.
VALID_PROVIDERS = frozenset({"ollama", "openai_compatible", "openai", "anthropic", "google"})
VALID_MODES = ("ask", "auto", "plan")

RESERVED_SECTIONS = {
    "skills": "not supported yet — arrives in Step 11 (C5.1)",
    "memory": "not supported yet — arrives in Step 14 (C8.1)",
    "tools": "not supported yet — arrives in Step 7 (C3.1)",
    "mcp": "MCP is configured in a separate .mcp.json, not here — arrives in Step 13 (C4.2)",
}


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to construct one role's chat model.

    `api_key_env` names an environment variable. The key value itself is never
    stored here, never logged, and never written to a config file (C1.5).

    Field names are unchanged from Step 5 so `llm/factory.py` keeps working.
    """

    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    temperature: float | None
    context_tokens: int | None
    max_output_tokens: int | None
    timeout: int | None


@dataclass(frozen=True)
class AgentConfig:
    """Agent execution settings."""

    verbose: bool


@dataclass(frozen=True)
class PermissionsConfig:
    """Parsed but NOT enforced in Step 6 — enforcement is Step 7 (C3.3, U.7).

    `allow` and `deny` are shape-validated and stored; nothing reads them yet.
    `rudra doctor` says so explicitly rather than leaving the user to assume.
    """

    mode: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]


@dataclass(frozen=True)
class CompatConfig:
    """The two middlewares D4 kept, both opt-in."""

    task_anchor: bool
    sandbox_paths: bool


MODEL_KEYS = frozenset(f.name for f in fields(ModelConfig))

DEFAULTS: dict[str, Any] = {
    "model": {
        "default": {
            "provider": "ollama",
            "model": "qwen3:32b",
            "base_url": "http://localhost:11434",
            "api_key_env": None,
            "temperature": 0.3,
            "context_tokens": None,
            "max_output_tokens": 131072,
            "timeout": 300,
        }
    },
    "agent": {"verbose": True},
    "permissions": {"mode": "ask", "allow": [], "deny": []},
    "compat": {"task_anchor": False, "sandbox_paths": False},
}

__all__ = [
    "BUILTIN_ROLES",
    "DEFAULTS",
    "MODEL_KEYS",
    "RESERVED_SECTIONS",
    "VALID_MODES",
    "VALID_PROVIDERS",
    "AgentConfig",
    "CompatConfig",
    "ModelConfig",
    "PermissionsConfig",
]
