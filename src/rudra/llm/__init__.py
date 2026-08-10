"""Provider-agnostic model construction. See TODO.md C1.1-C1.8."""

from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    ModelConfigError,
    UnknownProviderError,
)
from rudra.llm.factory import build_model
from rudra.llm.providers import PROVIDERS, ProviderEntry

__all__ = [
    "PROVIDERS",
    "MissingApiKeyError",
    "ModelCapabilityError",
    "ModelConfigError",
    "ProviderEntry",
    "UnknownProviderError",
    "build_model",
]
