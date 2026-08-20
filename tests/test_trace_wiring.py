"""`rudra.trace` imports nothing from Rudra's agent layer (Step 15a).

Same guard, same reason as facts/store.py, context/budget.py and
memory/entry.py: a renderer that can reach the loop is a renderer somebody
will eventually make a decision in. It must stay testable with no agent,
no model and no config.

Parsed with `ast` rather than imported, the way
tests/test_no_direct_provider_imports.py does it -- importing would prove
only that the import succeeded, not that it was absent.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN = (
    "rudra.agent",
    "rudra.loop",
    "rudra.subagents",
    "rudra.permissions",
    "rudra.config",
    "rudra.llm",
    "rudra.memory",
)

PROVIDERS = (
    "langchain_openai",
    "langchain_anthropic",
    "langchain_ollama",
    "langchain_google_genai",
    "deepagents",
)

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "rudra" / "trace"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _offenders(prefixes: tuple[str, ...]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for module in sorted(PACKAGE.glob("*.py")):
        bad = {name for name in _imported_modules(module) if name.startswith(prefixes)}
        if bad:
            found[module.name] = sorted(bad)
    return found


def test_the_package_is_not_empty():
    """A guard over zero files passes for the wrong reason."""
    assert {path.name for path in PACKAGE.glob("*.py")} >= {
        "__init__.py",
        "events.py",
        "render.py",
        "sink.py",
        "stream.py",
    }


def test_the_trace_package_never_reaches_into_the_agent_layer():
    assert _offenders(FORBIDDEN) == {}


def test_the_trace_package_imports_no_provider_or_agent_framework():
    assert _offenders(PROVIDERS) == {}
