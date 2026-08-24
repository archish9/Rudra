"""Layered configuration (TODO.md C2.1, D1).

Load order, later overriding earlier:

1. built-in defaults          (schema.DEFAULTS)
2. ~/.config/rudra/config.toml
3. <project>/.rudra/config.toml
4. RUDRA_* environment variables (including .env)
5. CLI flags

Every resolved value records which layer set it — see `Config.provenance`
and `rudra config list`.
"""

from rudra.config.layers import ConfigError, user_toml_path
from rudra.config.loader import (
    Config,
    build_config,
    committed_api_key_notice,
    get_config,
    reset_config,
)
from rudra.config.schema import (
    AgentConfig,
    CompatConfig,
    McpConfig,
    MemoryConfig,
    ModelConfig,
    PermissionsConfig,
    SkillsConfig,
    ToolsConfig,
)

__all__ = [
    "AgentConfig",
    "CompatConfig",
    "McpConfig",
    "MemoryConfig",
    "Config",
    "ConfigError",
    "ModelConfig",
    "PermissionsConfig",
    "SkillsConfig",
    "ToolsConfig",
    "build_config",
    "committed_api_key_notice",
    "get_config",
    "reset_config",
    "user_toml_path",
]
