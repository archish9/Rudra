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


def _cfg(provider: str):
    return make_config(provider=provider, model="m", base_url="http://localhost:8000/v1")


def test_session_id_sets_extra_body_on_vaayu() -> None:
    model = build_model("coder", _cfg("vaayu"), session_id="run-7-coder-t1")
    assert model.extra_body == {"session_id": "run-7-coder-t1"}


def test_session_id_ignored_on_ollama() -> None:
    model = build_model("coder", make_config(), session_id="run-7-coder-t1")
    assert getattr(model, "extra_body", None) in (None, {})


def test_no_session_id_leaves_model_unchanged() -> None:
    model = build_model("coder", _cfg("vaayu"))
    assert getattr(model, "extra_body", None) in (None, {})
