"""[tools] max_parallel_tasks: a local clamp on the parallel loop's fan-out."""

from __future__ import annotations

import os

import pytest

from rudra.config.loader import ConfigError, build_config, reset_config


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def _write(tmp_path, body: str):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_default_is_zero_meaning_honor_the_server(tmp_path):
    assert build_config(tmp_path).tools.max_parallel_tasks == 0


def test_a_configured_value_reads_through(tmp_path):
    root = _write(tmp_path, "[tools]\nmax_parallel_tasks = 2\n")
    assert build_config(root).tools.max_parallel_tasks == 2


def test_explicit_zero_is_valid(tmp_path):
    root = _write(tmp_path, "[tools]\nmax_parallel_tasks = 0\n")
    assert build_config(root).tools.max_parallel_tasks == 0


def test_negative_is_rejected(tmp_path):
    root = _write(tmp_path, "[tools]\nmax_parallel_tasks = -1\n")
    with pytest.raises(ConfigError, match="max_parallel_tasks"):
        build_config(root)


def test_bool_is_rejected(tmp_path):
    root = _write(tmp_path, "[tools]\nmax_parallel_tasks = true\n")
    with pytest.raises(ConfigError, match="max_parallel_tasks"):
        build_config(root)
