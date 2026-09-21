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

    default_base_url: str | None = None
    """Endpoint to use when the role declares none. Provider-specific, so it
    lives here and NOT in DEFAULTS: a value in DEFAULTS survives per-leaf
    merging that a user table cannot clear (TOML has no null), so an Ollama
    URL there reached every hosted provider and the documented Anthropic
    config talked to localhost:11434 (CR-D1)."""

    default_max_output_tokens: int | None = None
    """Output cap to use when the role declares none. Here for CR-D1's
    reason: 131072 is an Ollama `num_predict` value, and forwarding it as
    `max_tokens` exceeds every current Anthropic and OpenAI model's cap
    (CR-D2)."""

    no_client_retries: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    """Kwargs that switch the provider client's OWN retries off (OPEN-115).

    `ModelRetryMiddleware` is the retry layer: it logs, counts, and knows
    whether the endpoint has served this run. The client SDK retried inside
    each of its attempts, invisibly -- 12 requests per logical call, 24 on
    google, of which `usage.json` saw 4. `llm/retry.py::is_transient` and
    `retry_wait` carry what that layer did and the middleware did not: every
    5xx, `x-should-retry`, and `Retry-After`.

    A mapping rather than a number because the same word means different
    things: openai's and anthropic's `max_retries` counts RETRIES, so none
    is 0; google's builds `HttpRetryOptions(attempts=)`, so none is 1, and
    langchain_google_genai warns that 0 reads as Google's default. Empty for
    ollama, whose client has no retry layer."""

    chunk_timeout_kwarg: str | None = None
    """Provider's name for its bound on a STREAMED call's silence, or None if
    its client has none (OPEN-139).

    langchain_openai ends an async streamed call after `stream_chunk_timeout`
    seconds without a parsed chunk -- 120 by default, the wait for the FIRST
    chunk included -- and that is not httpx's read timeout, which `timeout`
    sets and which an SSE keepalive resets. So under `--stream` a call's real
    bound was 120 s, set by nothing here: run 654a00c7f546 hit it where the
    role said 300, and a local model whose prefill passes 120 s would fail
    every attempt. It is set to the role's `timeout`, so one number means
    "seconds a call may go without producing anything" with or without
    `--stream`. Never 0: on a server that sends keepalives it is the only
    bound a stalled stream has."""

    def build_kwargs(self, settings: ModelConfig) -> dict[str, Any]:
        """Emit exactly the kwargs this provider accepts.

        A `None` setting emits no kwarg at all, so the provider's own default
        applies rather than an explicit `None` being sent over the wire.
        """
        kwargs: dict[str, Any] = {}
        base_url = settings.base_url if settings.base_url is not None else self.default_base_url
        if base_url is not None:
            kwargs["base_url"] = base_url
        if settings.temperature is not None:
            kwargs["temperature"] = settings.temperature
        max_output = (
            settings.max_output_tokens
            if settings.max_output_tokens is not None
            else self.default_max_output_tokens
        )
        if self.max_output_kwarg is not None and max_output is not None:
            kwargs[self.max_output_kwarg] = max_output
        if self.context_kwarg is not None and settings.context_tokens is not None:
            kwargs[self.context_kwarg] = settings.context_tokens
        if self.supports_timeout and settings.timeout is not None:
            kwargs["timeout"] = settings.timeout
            if self.chunk_timeout_kwarg is not None:
                kwargs[self.chunk_timeout_kwarg] = settings.timeout
        kwargs.update(self.no_client_retries)
        kwargs.update(self.extra_kwargs)
        return kwargs


def effective_base_url(settings: ModelConfig) -> str | None:
    """The endpoint this role will actually reach.

    `settings.base_url` alone is not the answer: a provider may supply its
    own default when the role declares none (see ProviderEntry), so anything
    that reports or groups by endpoint must resolve it the same way
    `build_kwargs` does.
    """
    if settings.base_url is not None:
        return settings.base_url
    entry = PROVIDERS.get(settings.provider)
    return entry.default_base_url if entry is not None else None


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
            default_base_url="http://localhost:11434",
            default_max_output_tokens=131072,
        ),
        "openai_compatible": ProviderEntry(
            name="openai_compatible",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
            chunk_timeout_kwarg="stream_chunk_timeout",
            # A1.38: this path maps to the `openai` prefix and would otherwise
            # inherit deepagents' ProviderProfile(use_responses_api=True).
            # vLLM, LM Studio, Groq, Together, and OpenRouter serve
            # /chat/completions, not OpenAI's /responses.
            #
            # OPEN-137: `stream_usage` because langchain_openai leaves it off
            # whenever a base_url is set -- always, here -- and `--stream`
            # makes every model call stream, so no call asked for
            # `stream_options.include_usage` and run e1a57a3e3791 recorded no
            # token count for any role. Only a streamed request carries it; a
            # run without `--stream` sends what it always sent.
            extra_kwargs=MappingProxyType({"use_responses_api": False, "stream_usage": True}),
            no_client_retries=MappingProxyType({"max_retries": 0}),
        ),
        "openai": ProviderEntry(
            name="openai",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
            chunk_timeout_kwarg="stream_chunk_timeout",
            # OPEN-137, for the same reason, and so it does not hang on
            # whether a base_url is set. Inert on today's path: deepagents'
            # profile sends this prefix to /responses, which reports usage
            # unasked and never reads `stream_usage`.
            extra_kwargs=MappingProxyType({"stream_usage": True}),
            no_client_retries=MappingProxyType({"max_retries": 0}),
        ),
        "anthropic": ProviderEntry(
            name="anthropic",
            lc_prefix="anthropic",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
            no_client_retries=MappingProxyType({"max_retries": 0}),
        ),
        "google": ProviderEntry(
            name="google",
            lc_prefix="google_genai",
            max_output_kwarg="max_output_tokens",
            needs_api_key=True,
            supports_timeout=True,
            no_client_retries=MappingProxyType({"max_retries": 1}),
        ),
    }
)
