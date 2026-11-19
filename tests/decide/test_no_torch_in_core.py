"""Core install must stay torch-free; laya is reachable only via the extra."""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
FORBIDDEN = {"torch", "transformers"}


def _name(requirement: str) -> str:
    return re.split(r"[\s\[<>=!~;(]", requirement.strip(), maxsplit=1)[0].lower()


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_core_dependencies_exclude_torch_and_transformers():
    deps = _load()["project"]["dependencies"]
    assert not {_name(d) for d in deps} & FORBIDDEN


def test_decide_extra_exists_and_lists_laya_mlx():
    extra = _load()["project"]["optional-dependencies"]["decide"]
    assert "laya-mlx" in {_name(d) for d in extra}
