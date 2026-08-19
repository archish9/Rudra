"""C1.1's acceptance criterion, as a test rather than a claim.

Rudra is provider-agnostic exactly when no module outside rudra.llm knows
which provider package exists. A stray `from langchain_ollama import
ChatOllama` re-introduces the lock-in this step removes, and would otherwise
be caught only by someone reading the diff.

The agent-construction tests at the bottom close a gap this step exposed:
migrating config.py to ModelConfig broke create_planner_agent and
the coder subagent at runtime, and the whole suite stayed green, because
nothing had ever constructed either one. Construction does no network I/O —
only .invoke()/.stream() would — so there is no reason not to cover it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import rudra

PROVIDER_PACKAGES = (
    "langchain_ollama",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_google_genai",
)

SRC = Path(rudra.__file__).parent


@pytest.fixture
def coded_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build agents against the shipped defaults, not the developer's .env.

    Config.load() reads `<cwd>/.env`, so running from the repo root builds
    these agents against whatever backend the developer happens to have
    configured — which, for anyone using a hosted provider, means the test
    fails on a missing API key while passing in CI. Starting in an empty
    directory pins the coded defaults: ollama, no key required, no network.
    """
    from rudra.config import reset_config

    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


def python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_there_are_python_files_to_check() -> None:
    """Guards against the glob silently matching nothing."""
    assert len(python_files()) > 10


@pytest.mark.parametrize("package", PROVIDER_PACKAGES)
def test_no_module_imports_a_provider_package_directly(package: str) -> None:
    """Both import forms count.

    Checking only `import <pkg>` would miss `from langchain_ollama import
    ChatOllama`, which is the form Rudra actually used — the substring
    `import langchain_ollama` does not occur in it. That near-miss is why
    this parses the module instead of grepping it.
    """
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == package or name.startswith(f"{package}.") for name in names):
                offenders.append(str(path.relative_to(SRC)))
                break

    assert offenders == [], (
        f"{package} is imported outside the model factory by: {offenders}. "
        f"Ask rudra.llm.build_model(role) for a model instead."
    )


def test_the_factory_is_the_only_caller_of_init_chat_model() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in python_files()
        if "init_chat_model" in path.read_text(encoding="utf-8")
    ]

    assert offenders == ["llm/factory.py"]


def test_the_planner_agent_can_be_constructed(tmp_path: Path, coded_defaults) -> None:
    """Regression guard: config.py's rewrite broke this at runtime while the
    suite stayed green, because no test had ever called it."""
    from rich.console import Console

    from rudra.agent.planner_agent import create_planner_agent

    agent = create_planner_agent(
        task="write a hello world script",
        project_path=tmp_path,
        filesystem_backend=None,
        checkpointer=None,
        console=Console(),
    )

    assert agent is not None


def test_the_coder_agent_can_be_constructed(coded_defaults, tmp_path) -> None:
    """Same guard for the coder half, which is a subagent since Step 9c."""
    from dataclasses import dataclass
    from typing import Any

    from rich.console import Console

    from rudra.config import get_config
    from rudra.subagents.build import build_agent
    from rudra.subagents.registry import REGISTRY

    @dataclass
    class Ctx:
        project_path: Any
        backend: Any
        gate: Any
        console: Any
        cfg: Any
        checkpointer: Any = None

    agent = build_agent(
        REGISTRY["coder"],
        Ctx(
            project_path=tmp_path,
            backend=None,
            gate=None,
            console=Console(quiet=True),
            cfg=get_config(),
        ),
    )

    assert agent is not None


# mempalace follows the same rule as a provider package and for the same
# reason: one import site means one place that knows the vendor's API, so
# a 3.7 -> 3.8 change is one file's problem. store.py is that site.
MEMORY_PACKAGE = "mempalace"

# Two files, and the second one earned its place rather than drifting into
# it. `memory/store.py` is the API surface. `memory/prefetch.py` exists
# *because* it calls the vendor: C8.5 warms the embedding model through
# mempalace's own get_embedding_function so Rudra never reimplements a
# cache layout that can drift under it (D12 named the wrong cache, which is
# what happens when the layout is described rather than executed).
#
# Adding a third entry here should require the same kind of argument.
MEMORY_MODULES = ("memory/store.py", "memory/prefetch.py")


def test_only_the_memory_store_imports_mempalace() -> None:
    """The C8.1 boundary, as a test rather than a claim.

    Deliberately stricter than the provider rule above: those forbid the
    import outside a *package*, this pins it to one *file*. A second
    importer inside rudra/memory/ would be just as much a second place
    that knows mempalace's API.
    """
    offenders = []
    for path in python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            hit = any(
                name == MEMORY_PACKAGE or name.startswith(f"{MEMORY_PACKAGE}.") for name in names
            )
            if hit and not any(str(path).endswith(name) for name in MEMORY_MODULES):
                offenders.append(f"{path}:{node.lineno}")
    allowed = ", ".join(MEMORY_MODULES)
    assert not offenders, f"mempalace may only be imported by {allowed}, found: {offenders}"


def test_every_mempalace_importer_imports_it_lazily() -> None:
    """No module-scope mempalace import, anywhere -- including store.py.

    chromadb's import chain pulls onnxruntime, grpcio and opentelemetry.
    At module scope that lands on `rudra --version`, against D16's
    measured 0.59 s startup which C9.8 already wants to cut.
    """
    for name in MEMORY_MODULES:
        path = SRC / Path(name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:  # module scope only, not ast.walk
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n.startswith(MEMORY_PACKAGE) for n in names), (
                f"module-scope mempalace import at {name}:{node.lineno}"
            )
