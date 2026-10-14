# tests/test_providers.py
from rudra.llm.providers import PROVIDERS


def test_vaayu_entry_is_local_openai_wire():
    entry = PROVIDERS["vaayu"]
    assert entry.lc_prefix == "openai"  # OpenAI-compatible wire
    assert entry.needs_api_key is False  # local, like ollama
    assert entry.default_base_url == "http://localhost:8000/v1"
    assert entry.max_output_kwarg == "max_tokens"
    assert entry.supports_timeout is True
    assert entry.chunk_timeout_kwarg == "stream_chunk_timeout"
    # /chat/completions, not /responses; usage on streamed calls.
    assert entry.extra_kwargs["use_responses_api"] is False
    assert entry.extra_kwargs["stream_usage"] is True
    assert entry.no_client_retries == {"max_retries": 0}
