"""One batching-hint read per distinct endpoint across the configured roles."""

from __future__ import annotations

from dataclasses import replace

from rudra.config import ModelConfig
from rudra.llm.batching import resolve_endpoint_concurrency
from tests.test_llm_factory import make_config

BASE = "http://localhost:8000/v1"
OTHER = "http://localhost:9000/v1"
OLLAMA_BASE = "http://localhost:11434"


def record(calls, value):
    def fetch(base_url, *, timeout, api_key=None):
        calls.append(base_url)
        return value

    return fetch


def _role(cfg, **overrides):
    return replace(cfg.model_for("default"), **overrides)


def _cfg(**roles):
    cfg = make_config(provider="vaayu", base_url=BASE, model="m", api_key="k")
    models: dict[str, ModelConfig] = {"default": cfg.model_for("default")}
    for name, overrides in roles.items():
        models[name] = _role(cfg, **overrides)
    return replace(cfg, models=models)


def test_dedups_shared_endpoint():
    calls: list[str] = []
    cfg = _cfg(planner={}, coder={"model": "other-model"})
    limits = resolve_endpoint_concurrency(cfg, fetch=record(calls, 3))
    assert calls == [BASE]
    assert limits == {BASE: 3}


def test_distinct_endpoints_each_fetched():
    calls: list[str] = []
    cfg = _cfg(planner={}, coder={"base_url": OTHER})
    limits = resolve_endpoint_concurrency(cfg, fetch=record(calls, 2))
    assert sorted(calls) == sorted([BASE, OTHER])
    assert limits == {BASE: 2, OTHER: 2}


def test_non_openai_provider_not_fetched():
    calls: list[str] = []
    cfg = make_config()  # ollama
    limits = resolve_endpoint_concurrency(cfg, fetch=record(calls, 9))
    assert calls == []
    assert limits == {OLLAMA_BASE: 1}


def test_missing_key_does_not_raise():
    calls: list[str] = []
    cfg = make_config(
        provider="vaayu", base_url=BASE, model="m", api_key=None, api_key_env="NO_SUCH_VAR_X"
    )
    limits = resolve_endpoint_concurrency(cfg, fetch=record(calls, 4))
    assert limits == {BASE: 4}
