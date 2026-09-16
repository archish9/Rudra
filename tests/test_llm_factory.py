"""build_model — the single place Rudra constructs a chat model.

No test here makes a network call. Constructing a LangChain chat model does
no I/O; only .invoke()/.stream() do. Live coverage is in test_llm_live.py
behind the `live` marker.
"""

from __future__ import annotations

import pytest

from rudra.config import (
    AgentConfig,
    CompatConfig,
    Config,
    McpConfig,
    MemoryConfig,
    ModelConfig,
    PermissionsConfig,
    SkillsConfig,
    TelemetryConfig,
    ToolsConfig,
)
from rudra.llm import build_model
from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    UnknownProviderError,
)


def make_config(**overrides) -> Config:
    """A Config with one explicit role, bypassing environment resolution."""
    base = {
        "provider": "ollama",
        "model": "qwen3:32b",
        "base_url": "http://localhost:11434",
        "api_key_env": None,
        "temperature": 0.3,
        "context_tokens": None,
        "max_output_tokens": 4096,
        "timeout": 300,
    }
    base.update(overrides)
    settings = ModelConfig(**base)
    # Config takes agent/permissions/compat explicitly — a half-specified
    # Config is not a valid one, and defaulting them here would be exactly
    # the kind of quiet default Step 6 exists to remove.
    return Config(
        agent=AgentConfig(verbose=False),
        permissions=PermissionsConfig(mode="ask", allow=(), deny=(), floor_disable=()),
        compat=CompatConfig(task_anchor=False, sandbox_paths=False),
        tools=ToolsConfig(shell=True, shell_in_auto=False, auto_branch=False, test_timeout=600),
        skills=SkillsConfig(enabled=()),
        mcp=McpConfig(
            enabled=True,
            mcp_in_auto=False,
            disabled_servers=(),
            allow=(),
            deny=(),
            timeout=60,
            readonly=(),
        ),
        memory=MemoryConfig(backend="chroma"),
        telemetry=TelemetryConfig(
            enabled=False,
            host="https://cloud.langfuse.com",
            public_key=None,
            public_key_env=None,
        ),
        models={"default": settings, "planner": settings, "coder": settings},
    )


def test_ollama_builds_a_chat_ollama() -> None:
    model = build_model("planner", make_config())

    assert type(model).__name__ == "ChatOllama"
    assert model.model == "qwen3:32b"
    assert model.num_predict == 4096


def test_a_colon_in_the_model_name_is_not_a_provider_separator() -> None:
    """`ollama:qwen3:32b` must split on the FIRST colon. Ollama tags contain
    colons, so getting this wrong turns every tagged model into a bad spec."""
    model = build_model("planner", make_config(model="qwen3:32b"))

    assert model.model == "qwen3:32b"


def test_openai_compatible_builds_a_chat_openai_with_the_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert type(model).__name__ == "ChatOpenAI"
    assert model.model_name == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"


def test_openai_compatible_never_enables_the_responses_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A1.38. The colon-free model id matters: with a colon, deepagents'
    provider-profile lookup rejects the spec and the OpenAI profile never
    applies, so the bug hides. A colon-free id is where it bites."""
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="qwen3-coder-30b",
            base_url="http://localhost:8000/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert model.use_responses_api is False


def test_context_tokens_land_in_the_model_profile() -> None:
    """C1.4a / A1.17. compute_summarization_defaults only switches from the
    fixed 170k trigger to ("fraction", 0.85) when profile["max_input_tokens"]
    is an int, so without this every local model never compacts."""
    model = build_model("planner", make_config(context_tokens=32768))

    assert model.profile["max_input_tokens"] == 32768


def test_context_tokens_also_size_the_ollama_server_window() -> None:
    """num_ctx is a separate concern from the profile: it is what the Ollama
    server allocates, defaulting to 4096 and truncating silently above it."""
    model = build_model("planner", make_config(context_tokens=32768))

    assert model.num_ctx == 32768


def test_no_context_tokens_leaves_the_profile_alone() -> None:
    model = build_model("planner", make_config(context_tokens=None))

    assert model.profile is None


def test_the_profile_merge_preserves_provider_capability_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blind assignment would delete tool_calling, which the C1.7 check reads."""
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "planner",
        make_config(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            base_url=None,
            api_key_env="TEST_KEY_VAR",
            context_tokens=200000,
        ),
    )

    assert model.profile["max_input_tokens"] == 200000
    assert model.profile["tool_calling"] is True


def test_unknown_provider_raises_before_construction() -> None:
    with pytest.raises(UnknownProviderError) as excinfo:
        build_model("planner", make_config(provider="gpt4all"))

    assert "gpt4all" in str(excinfo.value)
    assert "openai_compatible" in str(excinfo.value)


def test_missing_key_variable_raises_and_never_prints_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1.5."""
    monkeypatch.delenv("ABSENT_KEY_VAR", raising=False)

    with pytest.raises(MissingApiKeyError) as excinfo:
        build_model(
            "coder",
            make_config(
                provider="anthropic", model="claude-sonnet-4-5", api_key_env="ABSENT_KEY_VAR"
            ),
        )

    assert "ABSENT_KEY_VAR" in str(excinfo.value)


def test_a_provider_needing_a_key_with_none_configured_raises() -> None:
    with pytest.raises(MissingApiKeyError):
        build_model(
            "coder",
            make_config(provider="anthropic", model="claude-sonnet-4-5", api_key_env=None),
        )


def test_a_resolved_key_never_appears_in_the_model_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1.5, as a property rather than a promise.

    Asserting only that error messages are clean would be trivially true —
    they are raised before the key is ever read. The real exposure is a
    successfully built model getting logged, repr'd into a traceback, or
    printed by a debugger. LangChain wraps keys in SecretStr for exactly
    this reason; the test pins that behavior rather than trusting it.
    """
    secret = "sk-do-not-leak-this-value"
    monkeypatch.setenv("TEST_KEY_VAR", secret)

    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="qwen3-coder-30b",
            base_url="http://localhost:8000/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert secret not in repr(model)
    assert secret not in str(model)


def test_a_model_reporting_no_tool_calling_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """C1.7. Patched at the profile level because no shipped model reports
    tool_calling=False, and the branch must still be covered."""
    import rudra.llm.factory as factory

    real_init = factory.init_chat_model

    def fake_init(spec, **kwargs):
        model = real_init(spec, **kwargs)
        return model.model_copy(update={"profile": {"tool_calling": False}})

    monkeypatch.setattr(factory, "init_chat_model", fake_init)

    with pytest.raises(ModelCapabilityError) as excinfo:
        build_model("planner", make_config())

    assert "planner" in str(excinfo.value)


def test_an_absent_profile_is_not_treated_as_no_tool_calling() -> None:
    """Every local model reports profile=None. Refusing there would ban
    Ollama outright, so absent must mean "unknown", never "unsupported"."""
    model = build_model("planner", make_config())

    assert model.profile is None  # precondition: this is the absent case


def test_unknown_roles_resolve_through_default() -> None:
    """Step 9 calls build_model("reviewer") before reviewer config exists."""
    model = build_model("reviewer", make_config(model="qwen3:32b"))

    assert model.model == "qwen3:32b"


def test_apply_provider_profile_is_still_available() -> None:
    """A1.37: beta deepagents API, same hazard class as the U.4 monkeypatch."""
    from rudra.llm.factory import _apply_provider_profile

    assert callable(_apply_provider_profile())


# --- OPEN-115: what the built client itself will do --------------------------
#
# `build_kwargs` emitting the value is not enough: deepagents' provider
# profile rewrites kwargs on the way to the constructor (A1.37), so the
# number that matters is the one on the client object that sends requests.


@pytest.mark.parametrize(
    ("provider", "model_id", "base_url"),
    [
        ("openai_compatible", "qwen3-coder-30b", "http://localhost:8000/v1"),
        ("openai", "gpt-4o", None),
    ],
)
def test_the_built_openai_clients_make_no_retries_of_their_own(
    monkeypatch: pytest.MonkeyPatch, provider: str, model_id: str, base_url: str | None
) -> None:
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider=provider, model=model_id, base_url=base_url, api_key_env="TEST_KEY_VAR"
        ),
    )

    assert model.root_client.max_retries == 0
    assert model.root_async_client.max_retries == 0


def test_the_built_anthropic_clients_make_no_retries_of_their_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="anthropic",
            model="claude-sonnet-4-5",
            base_url=None,
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert model._client.max_retries == 0
    assert model._async_client.max_retries == 0


def test_the_built_google_model_asks_for_a_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google builds its retry options per request from this field."""
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="google",
            model="gemini-2.0-flash",
            base_url=None,
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert model.max_retries == 1
