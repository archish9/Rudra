"""Layered configuration (TODO.md C2.1, D1).

Transitional state: the public surface is still served by `_legacy`, the
pre-Step-6 environment-only resolver. `schema` is in place and `layers` /
`loader` land next; Task 4 of the Step 6 plan deletes `_legacy` and moves
this module's exports onto the layered loader.

Keeping `_legacy` as the live implementation until then is deliberate — it
keeps every commit's test suite green rather than leaving the package
half-wired across three commits.
"""

from rudra.config._legacy import (
    AgentConfig,
    Config,
    ModelConfig,
    get_config,
    reset_config,
)

__all__ = [
    "AgentConfig",
    "Config",
    "ModelConfig",
    "get_config",
    "reset_config",
]
