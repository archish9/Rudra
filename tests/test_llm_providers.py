"""Per-provider kwarg policy — guards A1.36 and A1.38.

Kwarg passthrough is not safe. ChatOllama accepts `timeout=` and silently
discards it (no field, no alias), while ChatOpenAI, ChatAnthropic, and
ChatGoogleGenerativeAI accept it through pydantic aliases. A blanket
passthrough therefore works on three providers and loses a setting on the
fourth with no error — which is why each provider declares its own kwargs.

These tests assert absence as loudly as presence. A kwarg that should not be
emitted is a silent misconfiguration, not a crash, so nothing else will catch
it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    ModelConfigError,
    UnknownProviderError,
)
from rudra.llm.providers import PROVIDERS


@dataclass(frozen=True)
class FakeSettings:
    """Stand-in for ModelConfig — build_kwargs only reads attributes."""

    base_url: str | None = "http://localhost:11434"
    temperature: float | None = 0.3
    max_output_tokens: int | None = 4096
    context_tokens: int | None = 32768
    timeout: int | None = 300


def test_every_documented_provider_is_registered() -> None:
    assert set(PROVIDERS) == {
        "ollama",
        "openai_compatible",
        "openai",
        "anthropic",
        "google",
    }


def test_ollama_emits_its_own_kwarg_names() -> None:
    kwargs = PROVIDERS["ollama"].build_kwargs(FakeSettings())

    assert kwargs["num_predict"] == 4096
    assert kwargs["num_ctx"] == 32768
    assert kwargs["reasoning"] is True
    assert kwargs["temperature"] == 0.3
    assert kwargs["base_url"] == "http://localhost:11434"


def test_ollama_omits_timeout() -> None:
    """A1.36: ChatOllama accepts timeout= and silently discards it."""
    assert "timeout" not in PROVIDERS["ollama"].build_kwargs(FakeSettings())


def test_ollama_sets_the_server_side_context_window() -> None:
    """num_ctx is the Ollama server's allocation; it defaults to 4096 and
    truncates silently above that. profile["max_input_tokens"] only tells
    deepagents when to summarize, so setting one without the other leaves
    either the agent or the server working against the wrong window."""
    assert PROVIDERS["ollama"].build_kwargs(FakeSettings()).get("num_ctx") == 32768


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai_compatible", "max_tokens"),
        ("openai", "max_tokens"),
        ("anthropic", "max_tokens"),
        ("google", "max_output_tokens"),
    ],
)
def test_hosted_providers_use_their_own_max_output_kwarg(provider: str, expected: str) -> None:
    kwargs = PROVIDERS[provider].build_kwargs(FakeSettings())
    assert kwargs[expected] == 4096


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_ollama_only_kwargs_never_leak_to_other_providers(provider: str) -> None:
    """C1.4: num_predict and reasoning are Ollama-only and raise elsewhere."""
    kwargs = PROVIDERS[provider].build_kwargs(FakeSettings())

    assert "num_predict" not in kwargs
    assert "num_ctx" not in kwargs
    assert "reasoning" not in kwargs


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_hosted_providers_accept_timeout(provider: str) -> None:
    assert PROVIDERS[provider].build_kwargs(FakeSettings())["timeout"] == 300


def test_openai_compatible_disables_the_responses_api() -> None:
    """A1.38: openai_compatible maps to the `openai` prefix, so it inherits
    deepagents' ProviderProfile(init_kwargs={"use_responses_api": True}).
    vLLM, LM Studio, Groq, Together, and OpenRouter serve /chat/completions
    and not /responses."""
    assert PROVIDERS["openai_compatible"].build_kwargs(FakeSettings())["use_responses_api"] is False


def test_real_openai_does_not_disable_the_responses_api() -> None:
    """The suppression is scoped to the compatible path, not to OpenAI itself."""
    assert "use_responses_api" not in PROVIDERS["openai"].build_kwargs(FakeSettings())


def test_unset_optional_settings_emit_no_kwarg() -> None:
    """A None must mean "let the provider default", not "send None"."""
    empty = FakeSettings(
        base_url=None,
        temperature=None,
        max_output_tokens=None,
        context_tokens=None,
        timeout=None,
    )
    kwargs = PROVIDERS["anthropic"].build_kwargs(empty)

    assert "base_url" not in kwargs
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs
    assert "timeout" not in kwargs


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_hosted_providers_need_an_api_key(provider: str) -> None:
    assert PROVIDERS[provider].needs_api_key is True


def test_ollama_needs_no_api_key() -> None:
    assert PROVIDERS["ollama"].needs_api_key is False


def test_lc_prefixes_are_the_langchain_names() -> None:
    """These strings become the `provider:model` spec prefix; a typo is a
    runtime error at model-construction time, not an import error."""
    assert PROVIDERS["ollama"].lc_prefix == "ollama"
    assert PROVIDERS["openai_compatible"].lc_prefix == "openai"
    assert PROVIDERS["openai"].lc_prefix == "openai"
    assert PROVIDERS["anthropic"].lc_prefix == "anthropic"
    assert PROVIDERS["google"].lc_prefix == "google_genai"


def test_unknown_provider_error_lists_the_valid_names() -> None:
    error = UnknownProviderError("gpt4all", tuple(PROVIDERS))

    assert "gpt4all" in str(error)
    for name in PROVIDERS:
        assert name in str(error)


def test_missing_api_key_error_names_the_variable_not_the_value() -> None:
    """C1.5: no error may ever carry a key value."""
    error = MissingApiKeyError("planner", "OPENROUTER_API_KEY")

    assert "OPENROUTER_API_KEY" in str(error)
    assert "planner" in str(error)


def test_capability_error_names_role_and_model() -> None:
    error = ModelCapabilityError("coder", "some-model")

    assert "coder" in str(error)
    assert "some-model" in str(error)


def test_all_errors_share_one_base() -> None:
    """Lets a caller catch every configuration failure with one except."""
    assert issubclass(UnknownProviderError, ModelConfigError)
    assert issubclass(MissingApiKeyError, ModelConfigError)
    assert issubclass(ModelCapabilityError, ModelConfigError)
