"""Model-construction failures.

Every error here is raised before any network call, so a misconfigured run
dies at startup instead of part-way through inference. That is the point of
C1.7.

No error message may contain an API key value (C1.5). Messages name the
environment variable; the value is never read into a message. Since OPEN-6
a key may also be given literally as `api_key`, which does not weaken this:
that field is `repr=False` and nothing here reads it.

That holds for the *value* field by construction. It did not hold for the
*name* field until A1.81: a user who set `api_key_env` to their key rather
than to a variable name had it printed straight back at them. Names that do
not look like names are now masked -- see `_looks_like_a_secret`.
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


def _looks_like_a_secret(value: str) -> bool:
    """Is this a key someone pasted where a variable *name* belongs?

    Two signals, either sufficient. A known key prefix is the strong one.
    Failing `isidentifier()` is the general one: environment variable names
    are identifiers, and keys are not -- they carry dashes, dots or slashes.

    Deliberately not a length test. A short key is still a key, and a long
    name is still a name.
    """
    known_prefixes = ("sk-", "sk_", "ghp_", "gho_", "github_pat_", "AKIA", "ASIA", "xoxb-", "AIza")
    return value.startswith(known_prefixes) or not value.isidentifier()


class MissingApiKeyError(ModelConfigError):
    """A provider needs a key and the named environment variable is unset.

    `api_key_env` holds the *name* of a variable. Setting it to the key
    itself is the obvious mistake -- every other tool in this space takes
    the key directly -- and it used to render that key into this message
    and onto the user's terminal (A1.81). A name that does not look like a
    name is therefore masked and explained rather than echoed.
    """

    def __init__(self, role: str, env_var: str) -> None:
        if _looks_like_a_secret(env_var):
            message = (
                f"Role {role!r} needs an API key, and its api_key_env appears to hold the key "
                f"itself rather than the *name* of an environment variable. "
                f"Rename that setting to api_key and the value will be used as-is:\n"
                f'    api_key = "..."\n'
                f"Prefer ~/.config/rudra/config.toml for it — a project .rudra/config.toml is "
                f"documented as safe to commit, and a key there would be committed with it. "
                f"To keep the key out of files entirely, set api_key_env to a variable name "
                f'instead (api_key_env = "OPENROUTER_API_KEY") and export the key into it.\n'
                f"The value has been withheld from this message; treat it as exposed and rotate "
                f"it if it reached a log or a shared terminal."
            )
        else:
            message = (
                f"Role {role!r} needs an API key, but environment variable {env_var!r} is not "
                f"set. Export it, point the role's api_key_env at a different variable, or set "
                f"api_key in ~/.config/rudra/config.toml to give the key directly."
            )
        super().__init__(message)
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
