"""Locks the two real behavioral changes of the deepagents 0.7.4 upgrade
(U.3, U.14) that no test previously imported rudra.agent to check.

  - planner_agent.py wires FixWriteParamsMiddleware, first in the list, so it
    cleans tool args before anything else sees them (U.14).
  - main_agent.py does not construct the deleted OverwriteFilesystemBackend
    (U.3).

Step 7 extended this to the permission gate: when one is supplied it must
be middleware[0] — ahead of FixWriteParamsMiddleware, so a denial lands
before anything rewrites the call's arguments — and its interrupt_on map
must reach create_deep_agent. Those are asserted by recording the real
call rather than by reading the source, because Step 7 moved the middleware
list into a local and the previous AST assertion broke while the property
it named still held.

IMPORTANT: this module must never call
rudra.compat.deepagents_path.install_path_normalizer, directly or
transitively, or it will break the ordering-sensitive guard in
tests/test_deepagents_contract.py::test_validate_path_is_shared_object when
the whole suite runs together (see that test and this module's own docstring
there for why).

Importing rudra.agent.planner_agent is safe: reading its source shows it
never imports rudra.compat.deepagents_path and never calls
install_path_normalizer at module level — confirmed here by grepping the
imported module's own source, not merely asserted.

rudra.agent.main_agent DOES call install_path_normalizer, but only inside the
body of the async create_main_agent() factory. Importing the module (which
this file does, to read its source) does not execute that factory, so it
stays safe. Actually proving the FilesystemBackend(virtual_mode=True) wiring
would require calling create_main_agent() — which patches — so that
assertion is made via source inspection (ast) instead, per the task's
explicit fallback for exactly this situation.
"""

from __future__ import annotations

import ast
import importlib
import inspect

import pytest

from rudra.config.loader import reset_config


def test_planner_agent_module_does_not_call_install_path_normalizer():
    """Verifies the safety claim this module's docstring relies on, rather
    than merely asserting it."""
    module = importlib.import_module("rudra.agent.planner_agent")
    source = inspect.getsource(module)
    assert "install_path_normalizer" not in source
    assert "deepagents_path" not in source


def test_planner_wires_fix_write_params_middleware_first():
    """U.14. Checked by building the stack, not by parsing the call site.

    This used to assert that `middleware=` was a list literal whose first
    element was FixWriteParamsMiddleware. Step 6 moved the stack behind
    `build_planner_middleware` so `[compat]` can gate TaskAnchorMiddleware
    (C1.8), which made the AST shape wrong while the property it guarded
    was still true. Calling the builder tests the property directly and
    survives the next refactor of the call site.
    """
    from rudra.agent.planner_agent import build_planner_middleware

    for task_anchor in (False, True):
        stack = build_planner_middleware("a task", compat_task_anchor=task_anchor)
        names = [type(m).__name__ for m in stack]
        assert names[0] == "FixWriteParamsMiddleware", (
            f"planner's first middleware is {names[0]!r}, expected "
            "FixWriteParamsMiddleware — it must clean tool args before "
            "anything else in the chain sees them (U.14)"
        )


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch, tmp_path):
    """Isolate every test here from the developer's own environment.

    Stripping the variables is `tests/conftest.py`'s job now, and is no longer
    repeated here. What remains is specific to this file: the agent factories
    call the process-global `get_config()` (`coder_agent.py:76`,
    `planner_agent.py`), which resolves its root to `Path.cwd()` and so reads
    the *repo's* `.env` no matter which `project_path` the factory was handed
    (A1.52). Running from a tmp cwd is what makes these tests hermetic; the
    variable-stripping alone is not enough, because `load_dotenv` re-imports
    them mid-test.

    Scoped to this file rather than conftest deliberately — several other
    tests read repo files through relative paths and need the real cwd.
    """
    monkeypatch.chdir(tmp_path)
    reset_config()
    yield
    reset_config()


def _record_create_deep_agent(monkeypatch, module_name: str) -> dict:
    """Capture the kwargs the agent factory hands to create_deep_agent.

    Behavioral rather than syntactic: Step 7 moved the middleware list into
    a local so the gate could be inserted, and the previous AST assertion
    broke while the property it named still held. Recording the real call
    survives any spelling.
    """
    module = importlib.import_module(module_name)
    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(module, "build_model", lambda *a, **k: object())
    return captured


def test_planner_passes_its_built_stack_to_create_deep_agent(monkeypatch, tmp_path):
    """The builder is only meaningful if create_deep_agent actually gets it."""
    from rich.console import Console

    from rudra.agent.planner_agent import build_planner_middleware, create_planner_agent

    captured = _record_create_deep_agent(monkeypatch, "rudra.agent.planner_agent")
    create_planner_agent(
        task="t",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
    )

    expected = [type(m).__name__ for m in build_planner_middleware("t")]
    assert [type(m).__name__ for m in captured["middleware"]] == expected


def test_planner_puts_the_permission_gate_first(monkeypatch, tmp_path):
    """A denial must land before any middleware rewrites the call's args."""
    from rich.console import Console

    from rudra.agent.planner_agent import create_planner_agent
    from rudra.config.loader import build_config, reset_config
    from rudra.permissions import build_gate

    reset_config()
    try:
        gate = build_gate(build_config(tmp_path), tmp_path)
        captured = _record_create_deep_agent(monkeypatch, "rudra.agent.planner_agent")
        create_planner_agent(
            task="t",
            project_path=tmp_path,
            filesystem_backend=object(),
            checkpointer=None,
            console=Console(quiet=True),
            gate=gate,
        )
        assert captured["middleware"][0] is gate.middleware
        assert captured["interrupt_on"] is gate.interrupt_on
    finally:
        reset_config()


def _subagent_context(tmp_path, cfg, gate):
    """A SubagentContext with a throwaway backend, for construction tests."""
    from rich.console import Console

    from rudra.subagents import SubagentContext

    return SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=gate,
        console=Console(quiet=True),
        cfg=cfg,
    )


def test_coder_puts_the_permission_gate_first(monkeypatch, tmp_path):
    """Step 9c: the coder is a registry entry built by subagents/build.py."""
    from rudra.config.loader import build_config, reset_config
    from rudra.permissions import build_gate
    from rudra.subagents.build import build_agent
    from rudra.subagents.registry import REGISTRY

    reset_config()
    try:
        cfg = build_config(tmp_path)
        gate = build_gate(cfg, tmp_path)
        captured = _record_create_deep_agent(monkeypatch, "rudra.subagents.build")
        build_agent(REGISTRY["coder"], _subagent_context(tmp_path, cfg, gate))
        assert captured["middleware"][0] is gate.middleware
        assert captured["interrupt_on"] is gate.interrupt_on
    finally:
        reset_config()


def test_no_gate_means_no_interrupt_config(monkeypatch, tmp_path):
    """A caller without a gate must still build a working agent."""
    from rudra.config.loader import build_config, reset_config
    from rudra.subagents.build import build_agent
    from rudra.subagents.registry import REGISTRY

    reset_config()
    try:
        cfg = build_config(tmp_path)
        captured = _record_create_deep_agent(monkeypatch, "rudra.subagents.build")
        build_agent(REGISTRY["coder"], _subagent_context(tmp_path, cfg, None))
        assert captured["interrupt_on"] is None
    finally:
        reset_config()


def test_main_agent_constructs_filesystem_backend_with_virtual_mode():
    module = importlib.import_module("rudra.agent.main_agent")
    source = inspect.getsource(module)
    tree = ast.parse(source)

    # A historical prose mention of the deleted class in a comment is fine
    # (TODO.md A4.9) — what must be gone is any import of it or any attempt
    # to construct one. Check the AST, not a bare substring match.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name for alias in node.names}
            assert "OverwriteFilesystemBackend" not in names, (
                "main_agent.py still imports the deleted OverwriteFilesystemBackend (U.3)"
            )
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "OverwriteFilesystemBackend"
        ):
            raise AssertionError(
                "main_agent.py still constructs the deleted OverwriteFilesystemBackend (U.3)"
            )

    fs_backend_call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FilesystemBackend":
            fs_backend_call = node
            break
    assert fs_backend_call is not None, (
        "no FilesystemBackend(...) construction found in main_agent.py"
    )

    virtual_mode_kw = next(
        (kw for kw in fs_backend_call.keywords if kw.arg == "virtual_mode"), None
    )
    assert virtual_mode_kw is not None, (
        "FilesystemBackend(...) in main_agent.py has no virtual_mode= kwarg"
    )
    assert (
        isinstance(virtual_mode_kw.value, ast.Constant) and virtual_mode_kw.value.value is True
    ), "main_agent.py's FilesystemBackend is not constructed with virtual_mode=True"


# --- Step 8: the git and test-runner tools ---


def _planner_tool_names(monkeypatch, tmp_path, **kwargs) -> list[str]:
    from rich.console import Console

    from rudra.agent.planner_agent import create_planner_agent

    captured = _record_create_deep_agent(monkeypatch, "rudra.agent.planner_agent")
    create_planner_agent(
        task="t",
        project_path=tmp_path,
        filesystem_backend=object(),
        checkpointer=None,
        console=Console(quiet=True),
        **kwargs,
    )
    return [tool.name for tool in captured["tools"]]


def test_the_planner_registers_the_ledger_tools(monkeypatch, tmp_path):
    """Step 9c: the planner declares work and nothing else.

    git_diff and run_tests moved to the reviewer and tester subagents --
    the loop runs the gate itself, so the planner has no reason to.
    """
    names = set(_planner_tool_names(monkeypatch, tmp_path))
    assert {"add_tasks", "drop_task", "read_ledger"} <= names
    assert "git_diff" not in names
    assert "run_tests" not in names


def test_every_wrapped_execute_name_is_actually_registered():
    """Otherwise the engine allows a name that reaches no tool (A1.53's shape).

    Step 9c moved these off the planner onto the subagents that use them,
    so the invariant is checked against the registry now. A1.64.
    """
    from rudra.permissions.rules import WRAPPED_EXECUTE_TOOLS
    from rudra.subagents.registry import REGISTRY

    registered = {name for spec in REGISTRY.values() for name in spec.rudra_tools}
    assert WRAPPED_EXECUTE_TOOLS <= registered


def test_the_interaction_tools_survive_the_ledger_switch(monkeypatch, tmp_path):
    """Step 9c replaced the planning tools; 10a replaced these two.

    Step 10b moved them onto the clarify stage: the planner is three
    agents now, and only the first of them establishes facts. Asserted
    against that stage rather than relaxed, because the property is the
    same one -- it just has an owner now.
    """
    names = set(_planner_tool_names(monkeypatch, tmp_path, stage="clarify"))
    assert "record_fact" in names
    assert "ask_user" in names
    assert "save_project_context" not in names


def test_an_unattended_planner_has_no_ask_user(monkeypatch, tmp_path):
    """S10a.5: the tool is absent, not refusing."""
    names = set(_planner_tool_names(monkeypatch, tmp_path, stage="clarify", interactive=False))
    assert "record_fact" in names
    assert "ask_user" not in names


def test_the_coder_still_has_no_rudra_tools(monkeypatch, tmp_path):
    """The coder writes files; it does not run tests or read diffs.

    Step 9c keeps that split: the loop runs the gate, the tester writes
    tests, the reviewer reads the diff.
    """
    from rudra.config.loader import build_config, reset_config
    from rudra.subagents.build import build_agent
    from rudra.subagents.registry import REGISTRY

    reset_config()
    try:
        cfg = build_config(tmp_path)
        captured = _record_create_deep_agent(monkeypatch, "rudra.subagents.build")
        build_agent(REGISTRY["coder"], _subagent_context(tmp_path, cfg, None))
        assert captured["tools"] == []
    finally:
        reset_config()


def test_the_tester_prompt_tells_the_model_not_to_commit():
    """S8.5's prompt half, re-pointed by Step 9c (A1.63).

    It lived on the planner because that is where the execute-capable
    tools were. The tester carries execute now, so the instruction has to
    live where the capability does.
    """
    from rudra.subagents.registry import TESTER

    assert "git commit" in TESTER.system_prompt
    assert "run_tests" in TESTER.system_prompt


# --- Step 10c: the approval gate ---


def _fake_plan():
    async def fake_plan(request, *, context, planner, ledger=None):
        from rudra.loop.ledger import Ledger

        ledger = ledger if ledger is not None else Ledger()
        await planner(ledger, request, stage="breakdown", reason="initial")
        return ledger

    return fake_plan


def _record_work(monkeypatch):
    ran: list[str] = []

    async def fake_work(request, *, context, planner, ledger):
        from rudra.agent.main_agent import AgentResult

        ran.append("ran")
        return AgentResult(success=True, message="done")

    monkeypatch.setattr("rudra.agent.main_agent.work", fake_work)
    return ran


def _agent(tmp_path, *, approve=None, tasks=("write it",)):
    """A RudraAgent with every model and backend faked away."""
    from rich.console import Console

    from rudra.agent.main_agent import AgentContext, RudraAgent
    from rudra.facts import FactStore
    from rudra.loop.ledger import Ledger

    ledger = Ledger()

    async def planner_callback(
        run_ledger, request, *, stage, reason="initial", task=None, feedback=""
    ):
        planner_callback.calls.append((stage, reason, feedback))
        if stage == "breakdown" and reason == "initial":
            for description in tasks:
                run_ledger.add(description)

    planner_callback.calls = []

    context = AgentContext(project_path=tmp_path, task="build it", console=Console(quiet=True))
    agent = RudraAgent(
        context=context,
        planner_agent=object(),
        session_id="s1",
        db_conn=None,
        loop_context=object(),
        planner_callback=planner_callback,
        ledger=ledger,
        facts=FactStore(),
        approve=approve,
    )
    return agent, planner_callback


async def test_auto_mode_never_asks_for_approval(monkeypatch, tmp_path):
    """--auto must not pause. Nobody is there to answer."""
    import rudra.agent.main_agent as main_agent

    def explode(console):
        raise AssertionError("--auto must not prompt for plan approval")

    monkeypatch.setattr(main_agent, "ask_approval", explode)
    monkeypatch.setattr(main_agent, "plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, _ = _agent(tmp_path)
    await agent.run()

    assert worked == ["ran"], "the work must still run"


async def test_cancel_stops_before_any_work(monkeypatch, tmp_path):
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, _ = _agent(tmp_path, approve=lambda console: PlanAnswer(PlanDecision.CANCEL))
    result = await agent.run()

    assert worked == []
    assert result.success is True, "declining a plan is not a failure"


async def test_a_revision_re_enters_breakdown_then_executes(monkeypatch, tmp_path):
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    answers = [
        PlanAnswer(PlanDecision.REVISE, "drop the tests task"),
        PlanAnswer(PlanDecision.APPROVE),
    ]
    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, planner = _agent(tmp_path, approve=lambda console: answers.pop(0))
    await agent.run()

    assert ("breakdown", "revision", "drop the tests task") in planner.calls
    assert worked == ["ran"]


async def test_revisions_are_capped(monkeypatch, tmp_path):
    from rudra.agent.main_agent import MAX_REVISIONS
    from rudra.loop.plan_view import PlanAnswer, PlanDecision

    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    agent, planner = _agent(
        tmp_path, approve=lambda console: PlanAnswer(PlanDecision.REVISE, "again")
    )
    await agent.run()

    revisions = [call for call in planner.calls if call[1] == "revision"]
    assert len(revisions) == MAX_REVISIONS
    assert worked == [], "an unapproved plan must not execute"


async def test_plan_mode_presents_and_stops(monkeypatch, tmp_path):
    from rudra.config.loader import build_config, reset_config

    monkeypatch.setattr("rudra.agent.main_agent.plan", _fake_plan())
    worked = _record_work(monkeypatch)

    rudra_dir = tmp_path / ".rudra"
    rudra_dir.mkdir(parents=True, exist_ok=True)
    (rudra_dir / "config.toml").write_text('[permissions]\nmode = "plan"\n', encoding="utf-8")
    reset_config()
    try:
        cfg = build_config(tmp_path)
        monkeypatch.setattr("rudra.agent.main_agent.get_config", lambda *a, **k: cfg)

        agent, _ = _agent(tmp_path)
        result = await agent.run()
    finally:
        reset_config()

    assert worked == [], "--plan must never execute"
    assert result.success is True
