"""Model-construction failures.

Every error here is raised before any network call, so a misconfigured run
dies at startup instead of part-way through inference. That is the point of
C1.7.

No error message may contain an API key value (C1.5). Messages name the
environment variable; the value is never read into a message.
"""

from __future__ import annotations


class ModelConfigError(Exception):
    """Base for every model-construction failure."""


class UnknownProviderError(ModelConfigError):
    """Configuration named a provider Rudra does not implement."""

    def __init__(self, provider: str, valid: tuple[str, ...]) -> None:
        super().__init__(
            f"Unknown provider {provider!r}. Valid providers: {', '.join(sorted(valid))}."
        )
        self.provider = provider


class MissingApiKeyError(ModelConfigError):
    """A provider needs a key and the named environment variable is unset."""

    def __init__(self, role: str, env_var: str) -> None:
        super().__init__(
            f"Role {role!r} needs an API key, but environment variable {env_var!r} is not set. "
            f"Export it, or point the role's api_key_env at a different variable. "
            f"Rudra never reads key values from configuration files."
        )
        self.role = role
        self.env_var = env_var


class ModelCapabilityError(ModelConfigError):
    """The model's profile explicitly reports that it cannot call tools."""

    def __init__(self, role: str, model: str) -> None:
        super().__init__(
            f"Model {model!r} (role {role!r}) reports tool_calling=False. "
            f"deepagents requires tool calling for every agent, so this model cannot be used. "
            f"Run `rudra models test` to check a candidate model."
        )
        self.role = role
        self.model = model
