"""Build a chat model for a role, for any provider.

This is the only module in Rudra that calls init_chat_model or imports a
provider package's concept of a model. Everything else asks for a role.

Why not deepagents' resolve_model: it accepts only the spec
(deepagents/_models.py:35), so per-role base_url, temperature, and profile
cannot ride it. Why not register_provider_profile: its registry is keyed
globally by provider or provider:model, so two roles pointing at the same
model would overwrite each other's kwargs. Composing at the call site gets
the built-in profiles with no global writes and no role collisions.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model

from rudra.compat.version_guard import require_deepagents_attr
from rudra.config import get_config
from rudra.llm.errors import MissingApiKeyError, ModelCapabilityError, UnknownProviderError
from rudra.llm.providers import PROVIDERS

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from rudra.config import Config, ModelConfig

logger = logging.getLogger(__name__)


def _apply_provider_profile():
    """Resolve deepagents' beta provider-profile helper.

    A1.37: `deepagents.profiles` is beta-flagged, so this joins the U.4
    monkeypatch and the U.13 private import behind the version guard. A
    deepagents bump that moves or renames it fails here, legibly, instead of
    silently dropping the provider kwargs it injects.
    """
    return require_deepagents_attr(
        "deepagents.profiles.provider.provider_profiles",
        "apply_provider_profile",
        "A1.37",
    )


def _resolve_api_key(role: str, settings: ModelConfig) -> str:
    """The key for this role: the literal one if set, else the named variable.

    `api_key` is checked first because it is the more specific statement --
    a user who wrote the key into config meant that key, and silently
    preferring a stale environment variable over it would be the "which
    source won?" defect the whole loader exists to prevent (OPEN-6).

    The value still never enters a message, a log line, or a repr: the
    errors here name variables only (C1.5), and `ModelConfig.api_key` is
    `repr=False`.
    """
    if settings.api_key:
        return settings.api_key
    if not settings.api_key_env:
        raise MissingApiKeyError(role, f"RUDRA_{role.upper()}_API_KEY_ENV (or RUDRA_API_KEY_ENV)")
    value = os.getenv(settings.api_key_env)
    if not value:
        raise MissingApiKeyError(role, settings.api_key_env)
    return value


def build_model(role: str, cfg: Config | None = None) -> BaseChatModel:
    """Construct the chat model for `role`.

    Makes no network call — LangChain chat model constructors do no I/O.
    Every failure mode raises before inference could start (C1.7).

    Args:
        role: `planner`, `coder`, or any name; unknown roles use `default`.
        cfg: Config to read from. Defaults to the process-wide one; tests
            pass an explicit Config instead of monkeypatching os.environ.

    Returns:
        A resolved BaseChatModel.

    Raises:
        UnknownProviderError: configuration named an unimplemented provider.
        MissingApiKeyError: the provider needs a key and the named variable
            is unset.
        ModelCapabilityError: the model's profile reports tool_calling=False.
    """
    settings = (cfg or get_config()).model_for(role)

    try:
        entry = PROVIDERS[settings.provider]
    except KeyError:
        raise UnknownProviderError(settings.provider, tuple(PROVIDERS)) from None

    spec = f"{entry.lc_prefix}:{settings.model}"

    role_kwargs: dict[str, Any] = entry.build_kwargs(settings)
    if entry.needs_api_key:
        role_kwargs["api_key"] = _resolve_api_key(role, settings)

    # U.9: deepagents' built-in provider profiles supply their kwargs first
    # and our role kwargs win on collision — which is how openai_compatible
    # suppresses use_responses_api (A1.38).
    kwargs = _apply_provider_profile()(spec, role_kwargs)

    model = init_chat_model(spec, **kwargs)

    # C1.4a: deepagents' compute_summarization_defaults switches from a fixed
    # 170k trigger to ("fraction", 0.85) only when this key is an int. Merge
    # rather than replace — hosted providers ship a populated profile whose
    # tool_calling key the check below reads.
    if settings.context_tokens is not None:
        model = model.model_copy(
            update={
                "profile": {
                    **(model.profile or {}),
                    "max_input_tokens": settings.context_tokens,
                }
            }
        )

    # C1.7. Three-state: False refuses, True proceeds, absent proceeds.
    # Every local model reports profile=None, so absent must not mean "no".
    if (model.profile or {}).get("tool_calling") is False:
        raise ModelCapabilityError(role, settings.model)

    # A1.35 data: harness profiles cannot match an instance whose identifier
    # carries a colon. Recording what we actually resolved gives U.10 real
    # data instead of a re-derivation.
    logger.debug(
        "built model role=%s spec=%s provider=%s identifier=%s",
        role,
        spec,
        settings.provider,
        getattr(model, "model_name", None) or getattr(model, "model", None),
    )
    return model
