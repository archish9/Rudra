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
from rudra.config.loader import Config, build_config, get_config, reset_config
from rudra.config.schema import (
    AgentConfig,
    CompatConfig,
    ModelConfig,
    PermissionsConfig,
)

__all__ = [
    "AgentConfig",
    "CompatConfig",
    "Config",
    "ConfigError",
    "ModelConfig",
    "PermissionsConfig",
    "build_config",
    "get_config",
    "reset_config",
    "user_toml_path",
]
