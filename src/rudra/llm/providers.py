"""Per-provider construction policy.

Why a policy table and not kwarg passthrough (A1.36): ChatOllama has no
`timeout` field and no alias, so `ChatOllama(model='q', timeout=42)`
constructs without error and drops the value — `getattr(m, 'timeout',
'ABSENT')` returns `ABSENT`. The other three providers accept `base_url`,
`api_key`, and `timeout` through pydantic aliases despite differing field
names (`anthropic_api_url`, `default_request_timeout`, `google_api_key`,
`openai_api_base`, `request_timeout`). Blanket passthrough would therefore
work on three providers and silently lose a setting on the fourth.

Adding a provider is one row in PROVIDERS, not a new code path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rudra.config import ModelConfig


@dataclass(frozen=True)
class ProviderEntry:
    """How to turn a role's settings into constructor kwargs.

    This module deliberately avoids naming LangChain's model-construction
    entry point even in prose: tests/test_no_direct_provider_imports.py
    asserts that exactly one file in the package mentions it.
    """

    name: str
    lc_prefix: str
    """LangChain provider name used as the `provider:model` spec prefix."""

    max_output_kwarg: str | None
    """Provider's name for the output-token cap, or None if it has none."""

    needs_api_key: bool
    supports_timeout: bool
    """False only for ollama, which silently discards `timeout` (A1.36)."""

    context_kwarg: str | None = None
    """Server-side context-window kwarg. Ollama only: `num_ctx` tells the
    server how much context to allocate (it defaults to 4096 and truncates
    silently above that), which is a separate concern from
    profile["max_input_tokens"] telling deepagents when to summarize."""

    extra_kwargs: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    """Always-on kwargs for this provider."""

    def build_kwargs(self, settings: ModelConfig) -> dict[str, Any]:
        """Emit exactly the kwargs this provider accepts.

        A `None` setting emits no kwarg at all, so the provider's own default
        applies rather than an explicit `None` being sent over the wire.
        """
        kwargs: dict[str, Any] = {}
        if settings.base_url is not None:
            kwargs["base_url"] = settings.base_url
        if settings.temperature is not None:
            kwargs["temperature"] = settings.temperature
        if self.max_output_kwarg is not None and settings.max_output_tokens is not None:
            kwargs[self.max_output_kwarg] = settings.max_output_tokens
        if self.context_kwarg is not None and settings.context_tokens is not None:
            kwargs[self.context_kwarg] = settings.context_tokens
        if self.supports_timeout and settings.timeout is not None:
            kwargs["timeout"] = settings.timeout
        kwargs.update(self.extra_kwargs)
        return kwargs


PROVIDERS: Mapping[str, ProviderEntry] = MappingProxyType(
    {
        "ollama": ProviderEntry(
            name="ollama",
            lc_prefix="ollama",
            max_output_kwarg="num_predict",
            needs_api_key=False,
            supports_timeout=False,
            context_kwarg="num_ctx",
            extra_kwargs=MappingProxyType({"reasoning": True}),
        ),
        "openai_compatible": ProviderEntry(
            name="openai_compatible",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
            # A1.38: this path maps to the `openai` prefix and would otherwise
            # inherit deepagents' ProviderProfile(use_responses_api=True).
            # vLLM, LM Studio, Groq, Together, and OpenRouter serve
            # /chat/completions, not OpenAI's /responses.
            extra_kwargs=MappingProxyType({"use_responses_api": False}),
        ),
        "openai": ProviderEntry(
            name="openai",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
        "anthropic": ProviderEntry(
            name="anthropic",
            lc_prefix="anthropic",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
        "google": ProviderEntry(
            name="google",
            lc_prefix="google_genai",
            max_output_kwarg="max_output_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
    }
)
