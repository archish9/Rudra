"""models test probes endpoints, not roles."""

from __future__ import annotations

from rudra.config import build_config
from rudra.llm.probe import roles_to_probe


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


BASE = (
    '[model.default]\nprovider = "ollama"\n'
    'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
)


def test_one_model_means_one_probe(tmp_path, monkeypatch):
    # Five roles on one endpoint must not fire five network probes.
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE))
    entries = roles_to_probe(cfg)
    assert len(entries) == 1
    roles, _ = entries[0]
    assert set(roles) == {"default", "planner", "coder", "tester", "reviewer"}


def test_a_distinct_role_gets_its_own_probe(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE + '\n[model.reviewer]\nmodel = "qwen3:72b"\n'))
    entries = roles_to_probe(cfg)
    assert len(entries) == 2
    by_model = {config.model: set(roles) for roles, config in entries}
    assert by_model["qwen3:72b"] == {"reviewer"}
    assert "planner" in by_model["qwen3:32b"]


def test_entries_are_ordered_stably(tmp_path, monkeypatch):
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(write_config(tmp_path, BASE + '\n[model.reviewer]\nmodel = "qwen3:72b"\n'))
    assert roles_to_probe(cfg) == roles_to_probe(cfg)


def test_a_different_base_url_is_a_distinct_endpoint(tmp_path, monkeypatch):
    # Same model name on two servers is two endpoints, not one.
    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    cfg = build_config(
        write_config(tmp_path, BASE + '\n[model.coder]\nbase_url = "http://other:11434"\n')
    )
    entries = roles_to_probe(cfg)
    assert len(entries) == 2
