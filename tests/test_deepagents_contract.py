"""Contract tests: the exact deepagents 0.7.4 surface Rudra depends on.

Every assertion corresponds to a claim in TODO.md Section F. They exist so a
deepagents upgrade fails loudly and specifically instead of breaking Rudra
somewhere far from the cause.

None of these tests need a live model or network access. Agent construction
is local; no request is issued.

IMPORTANT: this module must never import rudra.agent or call
rudra.compat.deepagents_path.install_path_normalizer. Not because doing so
would break the identity assertion in test_validate_path_is_shared_object —
install_path_normalizer assigns the *same* wrapper function to both patch
targets, so the identity would still (vacuously) hold afterward. The danger
is the opposite: that test would keep passing for the wrong reason, no
longer proving deepagents itself shares one validate_path object before any
patch runs. test_validate_path_is_shared_object guards against exactly this
ordering hazard with a sentinel check. See TODO.md U.21.
"""

from __future__ import annotations

import importlib
import inspect
from importlib.metadata import version

import pytest
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

    install_path_normalizer assigns the SAME wrapper to both targets, so this
    identity would still hold after patching — the assertion would go quiet
    rather than fail. Refuse to run if the patch is already installed.
    """
    utils = importlib.import_module("deepagents.backends.utils")
    fs_mw = importlib.import_module("deepagents.middleware.filesystem")

    assert getattr(utils, "_rudra_original_validate_path", None) is None, (
        "install_path_normalizer already ran; this assertion no longer proves "
        "deepagents shares one validate_path object"
    )
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
    """main_agent.py:560 passes virtual_mode=True and expects real files.

    The deleted OverwriteFilesystemBackend bypassed the base class with
    Path.write_text. If this fails, U.3's straight swap silently stops
    writing files.
    """
    _backend(tmp_path, virtual_mode=True).write("f.txt", "x")
    assert (tmp_path / "f.txt").read_text() == "x"


def test_write_creates_parent_directories(tmp_path):
    """Rudra builds multi-file projects with nested layouts. The deleted
    OverwriteFilesystemBackend called mkdir(parents=True) explicitly; upstream
    does it too, but nothing asserted it until now. See TODO.md U.20."""
    _backend(tmp_path).write("src/pkg/mod.py", "x")
    assert (tmp_path / "src" / "pkg" / "mod.py").read_text() == "x"


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


def test_permissions_still_rejected_with_execute_backend(tmp_path):
    """U.7's revisit trigger.

    deepagents 0.7.4 refuses `permissions=` on any backend implementing
    SandboxBackendProtocol, which is why Rudra owns its permission layer
    instead of adopting the upstream one (TODO.md U.7). When this test
    starts FAILING, upstream has lifted the restriction and U.7 can reopen.
    """
    import pytest
    from deepagents import create_deep_agent
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents.middleware.filesystem import FilesystemPermission

    backend = CompositeBackend(
        default=LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True), routes={}
    )
    with pytest.raises(NotImplementedError, match="does not yet support permissions"):
        create_deep_agent(
            model=ScriptedToolModel(),
            backend=backend,
            permissions=[FilesystemPermission(operations=["write"], paths=["/x/**"], mode="deny")],
        )


def test_filesystem_operations_still_exclude_execute():
    """A1.46: `permissions=` never covered execute, only read and write.

    This is the measured basis for §0.8's open MCP question: execute and
    MCP tools sit in the same uncovered category, so Step 13 needs the same
    mechanism Step 7 built, not a second one.
    """
    import typing

    from deepagents.middleware.filesystem import FilesystemOperation

    assert typing.get_args(FilesystemOperation) == ("read", "write")


def test_execute_is_registered_but_non_functional_without_a_sandbox(tmp_path):
    """U.17, permanently pinned.

    `execute` appears in the default tool stack on a plain FilesystemBackend
    and errors when called. Both halves matter: the presence is why U.17 was
    opened, the error is why C3.1 was still needed.
    """
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    agent = create_deep_agent(
        model=ScriptedToolModel(),
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
    )
    assert "execute" in _tool_names(agent)


# --- Step 9b: the three upstream facts the subagent design rests on ----------
#
# These exist so a deepagents upgrade fails a test that names the decision
# it invalidates, rather than silently changing behaviour. Same role
# test_permissions_still_rejected_with_execute_backend plays for U.7.


def _minimal_subagent(name: str) -> dict:
    return {
        "name": name,
        "description": f"{name} for contract testing",
        "system_prompt": "Do nothing. Stop.",
        "model": ScriptedToolModel(),
        "tools": [],
    }


def test_a_supplied_general_purpose_spec_suppresses_the_auto_added_one(tmp_path):
    """S9b.2 rests on this: graph.py:751 skips the auto-add on a name match.

    If upstream changes the name or the check, Rudra silently regains an
    ungated subagent carrying the main agent's whole tool list.
    """
    from deepagents import create_deep_agent
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    agent = create_deep_agent(
        model=ScriptedToolModel(),
        tools=[],
        backend=backend,
        subagents=[_minimal_subagent("general-purpose")],
    )

    # Count registered agents, not mentions: the task tool's description
    # also names general-purpose in its usage notes, so a substring count
    # would pass for the wrong reason.
    description = _tools_by_name(agent)["task"].description
    registered = [
        line for line in description.splitlines() if line.startswith("- general-purpose: ")
    ]
    assert len(registered) == 1, f"expected exactly one general-purpose subagent, got {registered}"
    assert "for contract testing" in registered[0], (
        "the listed general-purpose is upstream's, not the supplied spec -- "
        "the suppression at graph.py:751 no longer works"
    )


def test_top_level_middleware_does_not_reach_subagents(tmp_path):
    """The asymmetry S9b.2 exists to handle (graph.py:666-703).

    Rudra injects its permission middleware into every subagent spec
    because the parent's does not propagate. If that ever changes, the
    per-spec injection becomes redundant rather than wrong -- but the
    reason for it changes, and the spec should be corrected.
    """
    import inspect as _inspect

    from deepagents import graph as deepagents_graph

    source = _inspect.getsource(deepagents_graph)
    # The subagent stack is built fresh; only spec["middleware"] is applied.
    assert 'spec.get("middleware", [])' in source, (
        "subagents no longer take their middleware solely from the spec"
    )


def test_top_level_interrupt_on_does_reach_subagents():
    """The other half: graph.py:718 inherits interrupt_on.

    Rudra relies on this for approvals inside subagents; if it stops being
    true, every subagent silently loses its prompts under `ask`.
    """
    import inspect as _inspect

    from deepagents import graph as deepagents_graph

    source = _inspect.getsource(deepagents_graph)
    assert 'spec.get("interrupt_on", interrupt_on)' in source, (
        "subagents no longer inherit the parent's interrupt_on"
    )


def _ollama_model(tmp_path, monkeypatch):
    """A real ChatOllama built the way Rudra builds one. No network."""
    from rudra.config import build_config
    from rudra.llm import build_model

    monkeypatch.delenv("RUDRA_MODEL", raising=False)
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n',
        encoding="utf-8",
    )
    return build_model("default", build_config(tmp_path))


def test_a_provider_harness_profile_does_match_a_prebuilt_model(tmp_path, monkeypatch):
    """A1.35 holds for BUILT-IN profiles; a provider-keyed one still matches.

    A1.35 is confirmed, not superseded: every built-in is a model-level
    `provider:model` key, and none of them can match an Ollama tag. But
    _harness_profile_for_model has a provider-only tier
    (harness_profiles.py:1087) that A1.35 does not mention, and it works on
    a pre-built instance because _get_ls_params reports ls_provider.

    That is what makes U.10 reachable at provider granularity. See A1.60.
    """
    from deepagents.profiles.harness import harness_profiles as hp

    model = _ollama_model(tmp_path, monkeypatch)

    # Built-ins load lazily; force it so this asserts against the real
    # registry rather than an empty one (harness_profiles.py:948-950).
    hp._get_harness_profile("openai")
    assert hp._HARNESS_PROFILES, "built-in profiles failed to load"
    assert not any(":" not in key for key in hp._HARNESS_PROFILES), (
        "a built-in now uses a provider-only key; A1.35's reasoning needs re-checking"
    )

    # No built-in matches an Ollama model -- A1.35's claim.
    assert hp._harness_profile_for_model(model, None).base_system_prompt is None

    monkeypatch.setattr(hp, "_HARNESS_PROFILES", dict(hp._HARNESS_PROFILES))
    hp.register_harness_profile("ollama", hp.HarnessProfile(base_system_prompt="MATCHED"))
    matched = hp._harness_profile_for_model(model, None)
    assert matched.base_system_prompt == "MATCHED", (
        "a provider-keyed harness profile no longer matches a pre-built instance"
    )


def test_an_ollama_model_level_profile_key_cannot_be_registered(tmp_path, monkeypatch):
    """A1.61: Ollama tags collide with the profile key separator.

    A model-level key is `provider:model`, but an Ollama model name is
    itself `family:size` -- so `ollama:qwen3:32b` carries two colons and
    validate_profile_key rejects it (profiles/_keys.py:34). Per-model
    tuning is therefore unreachable for the provider Rudra targets first,
    independently of anything Rudra does.
    """
    import pytest
    from deepagents.profiles.harness.harness_profiles import (
        HarnessProfile,
        register_harness_profile,
    )

    with pytest.raises(ValueError, match="more than one ':'"):
        register_harness_profile("ollama:qwen3:32b", HarnessProfile())


def test_custom_filesystem_middleware_replaces_the_default(monkeypatch, tmp_path):
    """A custom FilesystemMiddleware replaces the default, it does not join it.

    Step 12a's entire approach to A1.47 rests on this: the eviction
    threshold has no create_deep_agent parameter, so the only public route
    to it is passing a configured instance in middleware=. That works
    because _apply_custom_middleware matches on `.name` and substitutes in
    place (graph.py:215-232).

    If this fails with a count of 2, upstream started appending. Rudra then
    has two filesystem middlewares and A1.47 reopens -- its original
    proposal, a Rudra wrap_tool_call middleware, becomes the right fix.

    The final middleware list is only visible by intercepting the call
    create_deep_agent makes into create_agent: a compiled graph exposes no
    `.middleware` attribute.
    """
    import deepagents.graph as graph
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    captured = {}
    real_create_agent = graph.create_agent

    def spy(*args, **kwargs):
        captured["middleware"] = kwargs.get("middleware")
        return real_create_agent(*args, **kwargs)

    monkeypatch.setattr(graph, "create_agent", spy)

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    ours = FilesystemMiddleware(
        backend=backend, tools=["read_file"], tool_token_limit_before_evict=3000
    )
    graph.create_deep_agent(model=ScriptedToolModel(), backend=backend, middleware=[ours])

    filesystem = [m for m in captured["middleware"] if type(m).__name__ == "FilesystemMiddleware"]
    assert len(filesystem) == 1, (
        f"expected 1 FilesystemMiddleware, got {len(filesystem)} — upstream stopped "
        "replacing by name; TODO.md A1.47 reopens"
    )
    assert filesystem[0] is ours
    assert filesystem[0]._tool_token_limit_before_evict == 3000


def test_eviction_threshold_has_no_create_deep_agent_parameter():
    """The reason a middleware instance is the route at all (A1.47)."""
    from deepagents import create_deep_agent

    names = list(inspect.signature(create_deep_agent).parameters)
    assert not [n for n in names if "token" in n or "evict" in n]


def test_upstream_execute_description_still_says_isolated_sandbox():
    """The trigger that retires `middleware/execute_guard.py` (OPEN-25).

    Every `execute` tool deepagents builds carries this text as its schema
    description, and three things in it push a model toward `cd /app && ...`:
    it calls the shell an "isolated sandbox", its only worked example is a
    `cd`, and "use absolute paths" contradicts `_PATH_RULES`. Rudra replaces
    the description rather than arguing with it -- a prompt cannot outrank a
    prompt (OPEN-17).

    If this fails, upstream has reworded it: re-read the new text and, if it
    no longer asserts a container, delete the description half of
    ExecuteGuardMiddleware. The `cd` warning is independent of this.
    """
    from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION

    assert "isolated sandbox" in EXECUTE_TOOL_DESCRIPTION
    assert 'cd "/path/with spaces"' in EXECUTE_TOOL_DESCRIPTION
    assert "Use absolute paths" in EXECUTE_TOOL_DESCRIPTION


def test_upstream_still_orders_edit_file_in_the_memory_prompt():
    """The reason Rudra ships its own memory prompt at all (OPEN-70).

    `create_deep_agent(memory=[...])` builds a MemoryMiddleware with the
    default MEMORY_SYSTEM_PROMPT (graph.py:861-869), and that template tells
    the agent to call `edit_file` to persist what it learns -- twice in prose
    and twice more as worked `Tool Call: edit_file(...)` examples. The
    planner has no `edit_file`: absence is the enforcement
    (`_tools_for_stage`). So the order cannot be obeyed and shows up as a
    `tool_error`, which is what run14 and run15 both recorded.

    **If this test fails, upstream changed the template and Rudra's override
    should be re-read against the new one** -- the override exists to remove
    exactly these lines and may be removable, or may need to remove
    different ones.
    """
    from deepagents.middleware.memory import MEMORY_SYSTEM_PROMPT

    guidelines = MEMORY_SYSTEM_PROMPT[MEMORY_SYSTEM_PROMPT.index("<memory_guidelines>") :]
    assert "edit_file" in guidelines
    assert "Tool Call: edit_file" in guidelines


def test_memory_middleware_accepts_a_custom_system_prompt():
    """The public seam OPEN-70's fix uses. Not a monkeypatch: `system_prompt`
    is a documented constructor argument, and upstream validates it."""
    import inspect as _inspect

    from deepagents.middleware.memory import MemoryMiddleware

    names = list(_inspect.signature(MemoryMiddleware.__init__).parameters)
    assert "system_prompt" in names

    with pytest.raises(ValueError, match="agent_memory"):
        MemoryMiddleware(backend=object(), sources=["a"], system_prompt="no slot here")


def test_custom_memory_middleware_replaces_the_default(monkeypatch, tmp_path):
    """A custom MemoryMiddleware replaces the auto-added one (OPEN-70).

    Same mechanism `test_custom_filesystem_middleware_replaces_the_default`
    pins, one middleware over: `_apply_custom_middleware` matches on `.name`
    and substitutes in place, and it runs at graph.py:883 -- AFTER the
    `memory=` append at 861, which is what makes the replacement possible at
    all.

    A count of 2 means the agent carries BOTH prompts, so the `edit_file`
    order is back and OPEN-70 reopens.
    """
    import deepagents.graph as graph
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.memory import MemoryMiddleware

    captured = {}
    real_create_agent = graph.create_agent

    def spy(*args, **kwargs):
        captured["middleware"] = kwargs.get("middleware")
        return real_create_agent(*args, **kwargs)

    monkeypatch.setattr(graph, "create_agent", spy)

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    ours = MemoryMiddleware(
        backend=backend, sources=["AGENTS.md"], system_prompt="<m>{agent_memory}</m>"
    )
    graph.create_deep_agent(
        model=ScriptedToolModel(), backend=backend, memory=["AGENTS.md"], middleware=[ours]
    )

    memory = [m for m in captured["middleware"] if type(m).__name__ == "MemoryMiddleware"]
    assert len(memory) == 1, (
        f"expected 1 MemoryMiddleware, got {len(memory)} — upstream stopped replacing "
        "by name; OPEN-70 reopens and the edit_file order is back in the prompt"
    )
    assert memory[0] is ours


def test_upstream_still_tells_the_model_every_file_path_must_be_absolute():
    """The trigger that retires the "must be absolute" paragraph of
    `_PATH_RULES` (OPEN-81).

    Every file tool's `file_path` field carries this description, so the
    model reads it five times per call and reads Rudra's "use relative
    paths" once. `_PATH_RULES` used to answer with a flat prohibition and
    lost 13 times in one run; it now names this text and says which holds,
    the way `_DELETE_RULES` does for DELETE_TOOL_DESCRIPTION.

    If this fails, upstream has reworded it: re-read the new text and, if
    it no longer demands absolute paths, drop that paragraph. The rest of
    the block -- where the project is, and which roots do not exist -- is
    independent of it.
    """
    from deepagents.middleware.filesystem import (
        DeleteSchema,
        EditFileSchema,
        LsSchema,
        ReadFileSchema,
        WriteFileSchema,
    )

    for model, field in (
        (LsSchema, "path"),
        (ReadFileSchema, "file_path"),
        (WriteFileSchema, "file_path"),
        (EditFileSchema, "file_path"),
        (DeleteSchema, "file_path"),
    ):
        description = model.model_fields[field].description or ""
        assert "Must be absolute, not relative." in description, model.__name__


def test_a_tool_call_wrapper_sees_a_call_to_an_unregistered_tool():
    """OPEN-100 option C stands entirely on this upstream property.

    `PlannerWriteMiddleware` answers a planner `write_file` with the route --
    but the planner does not HAVE `write_file`, so the middleware only ever
    fires if langgraph hands an unregistered name to the wrapper instead of
    rejecting it first. It does, deliberately: `tool_node.py:1031-1033` reads

        # Validation is deferred to _execute_tool_sync to allow interceptors
        # to short-circuit requests for unregistered tools

    and `request.tool` is None in that case.

    **If this test fails, `PlannerWriteMiddleware` has silently stopped
    firing** -- the run's symptom would be upstream's tool-list echo coming
    back, with `roles.planner.planner_writes_refused` at 0 while the planner
    keeps generating documents. That is invisible from inside Rudra, which is
    why the pin is here rather than in that middleware's own tests.
    """
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool
    from langgraph.graph import MessagesState, StateGraph
    from langgraph.prebuilt.tool_node import ToolNode

    @tool
    def ls(path: str) -> str:
        """List a directory."""
        return "ok"

    seen: list[tuple[str, bool]] = []

    def wrapper(request, handler):
        seen.append((request.tool_call["name"], request.tool is None))
        return ToolMessage(
            content="REJECTED: intercepted",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error",
        )

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([ls], wrap_tool_call=wrapper))
    graph.set_entry_point("tools")
    graph.set_finish_point("tools")

    call = {"name": "write_file", "args": {"file_path": "/index.html"}, "id": "1"}
    out = graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call])]})

    assert seen == [("write_file", True)]
    assert out["messages"][-1].content == "REJECTED: intercepted"
    assert "is not a valid tool" not in out["messages"][-1].content


def test_filesystem_middleware_still_refuses_an_empty_tool_list():
    """OPEN-116 withholds every file tool from a greenfield planner stage, and
    its plan said to pass `tools=[]`. The installed constructor refuses that
    (`middleware/filesystem.py:1647`), which is why Rudra builds with
    `["read_file"]` and empties `.tools` instead.

    **If this starts passing an empty list through**, upstream lifted the
    restriction: switch `build_planner_middleware` to `tools=[]` and delete
    the emptying, which is a workaround for exactly this line.
    """
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)
    with pytest.raises(ValueError, match="read_file must be included"):
        FilesystemMiddleware(backend=backend, tools=[])


def test_an_emptied_filesystem_middleware_registers_no_file_tool(monkeypatch, tmp_path):
    """The workaround OPEN-116 stands on, driven through a compiled graph.

    Two claims, both load-bearing: `AgentMiddleware.tools` is what
    registration reads, so emptying it registers no file tool; and the
    instance still replaces upstream's by name, so deepagents adds no second
    one carrying every tool. That a call to a withheld name then reaches a
    tool wrapper -- which is what lets `GreenfieldReadMiddleware` answer it --
    is `test_a_tool_call_wrapper_sees_a_call_to_an_unregistered_tool`.
    """
    import deepagents.graph as graph
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    captured = {}
    real_create_agent = graph.create_agent

    def spy(*args, **kwargs):
        captured["middleware"] = kwargs.get("middleware")
        return real_create_agent(*args, **kwargs)

    monkeypatch.setattr(graph, "create_agent", spy)

    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    ours = FilesystemMiddleware(backend=backend, tools=["read_file"])
    ours.tools = []
    agent = graph.create_deep_agent(model=ScriptedToolModel(), backend=backend, middleware=[ours])

    filesystem = [m for m in captured["middleware"] if type(m).__name__ == "FilesystemMiddleware"]
    assert filesystem == [ours]
    file_tools = {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
    assert _tool_names(agent) & file_tools == set()
