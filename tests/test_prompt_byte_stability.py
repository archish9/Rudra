"""A subagent's system prompt must render byte-identically for identical state.

The Vaayu prefix cache keys on the prompt bytes; a re-render that differs
(unsorted listing, timestamp) cold-prefills every turn of a stream.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rudra.facts import FactStore
from rudra.subagents.build import _prompt_for
from rudra.subagents.registry import REGISTRY


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_prompt_renders_identically_twice(name, tmp_path):
    for rel in ("b/two.py", "a/one.py", "c.txt", "a/z.py"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n")
    facts = FactStore()
    context = SimpleNamespace(project_path=tmp_path, facts=facts)
    spec = REGISTRY[name]
    a = _prompt_for(spec, context, "do the thing")
    b = _prompt_for(spec, context, "do the thing")
    assert a == b
