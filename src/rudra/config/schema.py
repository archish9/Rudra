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

# Declared literally rather than imported from `rudra.permissions.floor`,
# for the same reason VALID_PROVIDERS is: this module imports nothing from
# the rest of Rudra, so it stays readable and testable with no agent
# machinery. Kept in agreement by test_config_permissions_and_tools.py::
# test_the_schema_names_the_same_floor_rules_the_floor_module_does.
FLOOR_RULE_NAMES = ("outside-root", "git-dir", "catastrophic-command")

# `outside-root` is deliberately absent: for the tools it covers, confinement
# comes from the backend's virtual_mode=True, not from Rudra's floor, so
# disabling it changed nothing and silently redirected the write into the
# project instead. Accepting a name and then not honouring it is worse than
# rejecting it. See TODO.md A1.50.
DISABLEABLE_FLOOR_RULES = ("git-dir", "catastrophic-command")

# `tools` was reserved here naming Step 7 as its implementing step. Step 7
# implements it, so it is a real section now — see ToolsConfig.
RESERVED_SECTIONS = {
    "skills": "not supported yet — arrives in Step 11 (C5.1)",
    "memory": "not supported yet — arrives in Step 14 (C8.1)",
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
    """Enforced from Step 7 (C3.3). See the Step 7 design spec §4.

    `allow`/`deny` are `tool` or `tool:pattern` strings using real tool
    names. `floor_disable` names built-in floor rules to switch off; it is
    per-rule rather than a boolean so one legitimate exception costs one
    name instead of surrendering the whole floor (spec §4.5).
    """

    mode: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]
    floor_disable: tuple[str, ...]


@dataclass(frozen=True)
class CompatConfig:
    """The two middlewares D4 kept, both opt-in."""

    task_anchor: bool
    sandbox_paths: bool


@dataclass(frozen=True)
class ToolsConfig:
    """The tool layer. Un-reserved in Step 7, which implements it.

    The oversized-tool-result threshold that would naturally live here is
    unreachable through `create_deep_agent` (TODO.md A1.47), and a key that
    silently does nothing is worse than no key.

    `shell_in_auto` is off by default. Under `--auto` nobody reads the
    command before it runs, and a shell command can write anywhere the user
    can — measured, not theorised (A1.49). Unattended runs therefore get
    the filesystem tools, which the backend genuinely confines, unless the
    user opts in once. `ask` mode is unaffected.

    `auto_branch` is off by default for the same reason in a different
    shape: it mutates the user's repository before the model has done
    anything, and nobody asked for it (Step 8 spec S8.3).

    `test_timeout` is a real key rather than a constant because Angular's
    Karma builder hangs indefinitely without a browser (C11.3), and no one
    number fits both a three-second unit suite and an integration run.
    """

    shell: bool
    shell_in_auto: bool
    auto_branch: bool
    test_timeout: int


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
    "permissions": {"mode": "ask", "allow": [], "deny": [], "floor_disable": []},
    "compat": {"task_anchor": False, "sandbox_paths": False},
    "tools": {
        "shell": True,
        "shell_in_auto": False,
        "auto_branch": False,
        "test_timeout": 600,
    },
}

__all__ = [
    "BUILTIN_ROLES",
    "DEFAULTS",
    "DISABLEABLE_FLOOR_RULES",
    "FLOOR_RULE_NAMES",
    "MODEL_KEYS",
    "RESERVED_SECTIONS",
    "VALID_MODES",
    "VALID_PROVIDERS",
    "AgentConfig",
    "CompatConfig",
    "ModelConfig",
    "PermissionsConfig",
    "ToolsConfig",
]
