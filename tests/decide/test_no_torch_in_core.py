"""Core install must stay torch-free.

laya is a documented sidecar install (CLAUDE.md, decide/), not a project
dependency or extra: laya-mlx pins numpy<2 and laya[onnx] pulls torch.
"""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
FORBIDDEN = {"torch", "transformers"}


def _name(requirement: str) -> str:
    return re.split(r"[\s\[<>=!~;(]", requirement.strip(), maxsplit=1)[0].lower()


def test_core_dependencies_exclude_torch_and_transformers():
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    assert not {_name(d) for d in deps} & FORBIDDEN
