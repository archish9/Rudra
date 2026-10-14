"""build_model threads a per-stream session_id into OpenAI-wire providers only."""

from __future__ import annotations

import pytest

from rudra.llm import build_model
from tests.test_llm_factory import make_config


@pytest.fixture(autouse=True)
def _openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # vaayu needs no key from Rudra, but the openai client refuses to
    # construct without one in the environment.
    monkeypatch.setenv("OPENAI_API_KEY", "test")


def _cfg(provider: str, **kw):
    return make_config(provider=provider, model="m", base_url="http://localhost:8000/v1", **kw)


def test_session_id_sets_extra_body_on_vaayu() -> None:
    model = build_model("coder", _cfg("vaayu"), session_id="run-7-coder-t1")
    assert model.extra_body == {"session_id": "run-7-coder-t1"}


def test_session_id_ignored_on_ollama() -> None:
    model = build_model("coder", make_config(), session_id="run-7-coder-t1")
    assert getattr(model, "extra_body", None) in (None, {})


def test_no_session_id_leaves_model_unchanged() -> None:
    model = build_model("coder", _cfg("vaayu"))
    assert getattr(model, "extra_body", None) in (None, {})


def test_vaayu_constructs_without_any_api_key_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    build_model("coder", _cfg("vaayu"))


def test_session_id_merges_into_existing_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    from rudra.llm import factory

    real = factory.init_chat_model

    def with_extra_body(spec, **kwargs):
        return real(spec, **kwargs).model_copy(update={"extra_body": {"foo": "bar"}})

    monkeypatch.setattr(factory, "init_chat_model", with_extra_body)
    model = build_model("coder", _cfg("vaayu"), session_id="s1")
    assert model.extra_body == {"foo": "bar", "session_id": "s1"}


def test_session_id_not_sent_to_canonical_openai() -> None:
    # Hosted OpenAI rejects unknown body params; it caches prefixes itself.
    model = build_model("coder", _cfg("openai", api_key="k"), session_id="x")
    assert getattr(model, "extra_body", None) in (None, {})


def test_session_id_sets_extra_body_on_openai_compatible() -> None:
    model = build_model("coder", _cfg("openai_compatible", api_key="k"), session_id="x")
    assert model.extra_body == {"session_id": "x"}
