"""Contract tests: the exact deepagents 0.7.4 surface Rudra depends on.

Every assertion corresponds to a claim in TODO.md Section F. They exist so a
deepagents upgrade fails loudly and specifically instead of breaking Rudra
somewhere far from the cause.

None of these tests need a live model or network access. Agent construction
is local; no request is issued.

IMPORTANT: this module must never import rudra.agent or call
rudra.compat.deepagents_path.install_path_normalizer. That function
monkeypatches validate_path, which would invalidate the identity assertion in
test_validate_path_is_shared_object.
"""

from __future__ import annotations

import importlib
import inspect
from importlib.metadata import version

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

EXPECTED_DEEPAGENTS_VERSION = "0.7.4"


def _tools_by_name(agent) -> dict:
    """Extract the bound tool map from a compiled deepagents graph.

    langgraph exposes the ToolNode as a graph node whose ``bound`` attribute
    carries ``tools_by_name``. If this stops working the langgraph internals
    moved, which is itself worth failing on.
    """
    for node in getattr(agent, "nodes", {}).values():
        tools_by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
        if tools_by_name:
            return tools_by_name
    raise AssertionError(
        "Could not locate a ToolNode via agent.nodes[*].bound.tools_by_name. "
        "langgraph internals changed; update _tools_by_name."
    )


def _tool_names(agent) -> set[str]:
    """The names of the agent's bound tools."""
    return set(_tools_by_name(agent))


class ScriptedToolModel(BaseChatModel):
    """A chat model that emits one canned write_file call, then stops.

    Contract tests must not depend on a provider being reachable or on a
    particular model tag existing on the machine, and they must never issue
    a network request. This drives the real compiled graph — real ToolNode,
    real backend — from a script.

    deepagents hard-requires tool calling, so ``bind_tools`` must work;
    returning self is enough because the emitted calls are fixed.
    """

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "wired.txt", "content": "ok"},
                        "id": "call-1",
                    }
                ],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


# --- U.4 / C0.8: version pin -------------------------------------------------


def test_deepagents_version_is_exactly_pinned():
    assert version("deepagents") == EXPECTED_DEEPAGENTS_VERSION


# --- U.4: validate_path monkeypatch targets ----------------------------------


def test_validate_path_exists_in_backends_utils():
    utils = importlib.import_module("deepagents.backends.utils")
    assert callable(getattr(utils, "validate_path", None))


def test_validate_path_is_shared_object():
    """middleware/filesystem.py does `from ...utils import validate_path`.

    Patching one module is not enough — this identity is exactly why
    compat/deepagents_path.py patches both targets.
    """
    utils = importlib.import_module("deepagents.backends.utils")
    fs_mw = importlib.import_module("deepagents.middleware.filesystem")
    assert getattr(fs_mw, "validate_path", None) is utils.validate_path


# --- U.13: private API task_anchor depends on --------------------------------


def test_private_append_to_system_message_exists():
    module = importlib.import_module("deepagents.middleware._utils")
    fn = getattr(module, "append_to_system_message", None)
    assert callable(fn)
    assert len(inspect.signature(fn).parameters) >= 2


# --- all six Rudra middlewares depend on this import path --------------------


def test_agent_middleware_base_class_import_path():
    types_mod = importlib.import_module("langchain.agents.middleware.types")
    assert hasattr(types_mod, "AgentMiddleware")


# --- U.3: FilesystemBackend.write semantics ----------------------------------


def _backend(tmp_path, **kwargs):
    from deepagents.backends.filesystem import FilesystemBackend

    return FilesystemBackend(root_dir=str(tmp_path), **kwargs)


def test_write_overwrites_existing_file(tmp_path):
    """The entire reason compat/overwrite_backend.py existed, now upstream."""
    backend = _backend(tmp_path)

    first = backend.write("f.txt", "a")
    assert getattr(first, "error", None) is None

    second = backend.write("f.txt", "b")
    assert getattr(second, "error", None) is None

    assert (tmp_path / "f.txt").read_text() == "b"


def test_write_does_not_strip_markdown_fences(tmp_path):
    """Proves FixWriteParamsMiddleware's fence-stripping stays load-bearing."""
    fenced = '```python\nprint("hi")\n```'
    _backend(tmp_path).write("g.py", fenced)
    assert (tmp_path / "g.py").read_text() == fenced


def test_virtual_mode_writes_through_to_disk(tmp_path):
    """main_agent.py:556 passes virtual_mode=True and expects real files.

    The deleted OverwriteFilesystemBackend bypassed the base class with
    Path.write_text. If this fails, U.3's straight swap silently stops
    writing files.
    """
    _backend(tmp_path, virtual_mode=True).write("f.txt", "x")
    assert (tmp_path / "f.txt").read_text() == "x"


# --- U.6: create_deep_agent surface ------------------------------------------


def test_create_deep_agent_accepts_rudras_kwargs():
    from deepagents import create_deep_agent

    params = inspect.signature(create_deep_agent).parameters
    for name in (
        "model",
        "tools",
        "system_prompt",
        "backend",
        "checkpointer",
        "middleware",
        "memory",
    ):
        assert name in params, f"create_deep_agent lost the {name!r} parameter"


def test_create_deep_agent_routes_writes_to_the_passed_backend(tmp_path):
    """0.7.4 types `backend` as BackendProtocol only — no BackendFactory.

    Asserts on behavior, not on tool names. Tool-name presence cannot tell
    "the tmp_path-rooted instance I passed was wired through" apart from
    "the kwarg was ignored and a default backend was substituted" — both
    yield identical names.

    The tool cannot be invoked directly: deepagents' write_file takes an
    injected ``ToolRuntime`` that only langgraph's ToolNode supplies inside
    a running graph (verified — a bare ``.invoke`` raises TypeError on
    0.7.4). So the graph is actually run, driven by ScriptedToolModel.
    No network request is issued. See TODO.md U.19.
    """
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=ScriptedToolModel(),
        tools=[],
        backend=_backend(tmp_path),
    )
    assert {"read_file", "write_file", "edit_file"} <= _tool_names(agent)

    agent.invoke({"messages": [{"role": "user", "content": "write it"}]})

    assert (tmp_path / "wired.txt").read_text() == "ok"


# --- U.5: TodoListMiddleware no longer auto-added ----------------------------


def test_default_stack_has_no_write_todos_tool(tmp_path):
    """Rudra replaced write_todos with planning_tools.py:3 — losing the
    auto-added TodoListMiddleware removes a redundant tool. Documented, not
    mourned."""
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=ScriptedToolModel(),
        tools=[],
        backend=_backend(tmp_path),
    )
    assert "write_todos" not in _tool_names(agent)
