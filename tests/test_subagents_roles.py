"""tester and reviewer as first-class model roles."""

from __future__ import annotations

from rudra.config import build_config
from rudra.config.schema import BUILTIN_ROLES


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


BASE = (
    '[model.default]\nprovider = "ollama"\n'
    'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
)


def test_the_new_roles_are_builtin():
    assert BUILTIN_ROLES == ("default", "planner", "coder", "tester", "reviewer")


def test_unset_roles_inherit_the_default_model(tmp_path, monkeypatch):
    # One inheritance rule, no named exceptions (spec S9b.5).
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE))
    assert cfg.models["tester"].model == "qwen3:32b"
    assert cfg.models["reviewer"].model == "qwen3:32b"


def test_an_explicit_section_overrides_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE + '\n[model.reviewer]\nmodel = "qwen3:72b"\n'))
    assert cfg.models["reviewer"].model == "qwen3:72b"
    assert cfg.models["tester"].model == "qwen3:32b"


def test_every_registry_role_resolves_to_a_model(tmp_path, monkeypatch):
    from rudra.subagents.registry import REGISTRY

    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE))
    for spec in REGISTRY.values():
        assert cfg.models[spec.role].model
