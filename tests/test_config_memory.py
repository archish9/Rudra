"""[memory] is a real section now (C8.2, Step 14a).

The reserved-section test is the one that matters most: RESERVED_SECTIONS
raises a hard error naming the implementing step, so leaving `memory` in it
would make every project config that uses the feature fail to load.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.config.loader import ConfigError, build_config


def _write(root: Path, body: str) -> None:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(body, encoding="utf-8")


def test_memory_section_is_no_longer_reserved(tmp_path: Path) -> None:
    _write(tmp_path, '[memory]\nbackend = "chroma"\n')
    cfg = build_config(tmp_path)
    assert cfg.memory.backend == "chroma"


def test_memory_backend_defaults_to_chroma(tmp_path: Path) -> None:
    cfg = build_config(tmp_path)
    assert cfg.memory.backend == "chroma"


def test_unknown_memory_key_is_fatal_and_suggests(tmp_path: Path) -> None:
    _write(tmp_path, '[memory]\nbackedn = "chroma"\n')
    with pytest.raises(ConfigError) as exc:
        build_config(tmp_path)
    assert "backedn" in str(exc.value)
    assert "backend" in str(exc.value)


def test_unknown_memory_backend_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path, '[memory]\nbackend = "postgres"\n')
    with pytest.raises(ConfigError) as exc:
        build_config(tmp_path)
    assert "postgres" in str(exc.value)


def test_there_is_no_enabled_key(tmp_path: Path) -> None:
    """S14.2: memory is mandatory. A key that cannot be turned off is worse
    than no key (A1.47)."""
    _write(tmp_path, "[memory]\nenabled = false\n")
    with pytest.raises(ConfigError):
        build_config(tmp_path)
