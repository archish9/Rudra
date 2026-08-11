"""The deny short-circuit (Step 7 spec §4.1).

Driven through a real compiled graph so the assertion is that the file was
not written, not merely that a function returned something.
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from rudra.permissions.audit import AuditLog
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import MUTATING_TOOLS, PermissionEngine


class OneCallModel(BaseChatModel):
    """Emits one scripted tool call, then stops. No network, no provider."""

    tool_name: str = "write_file"
    tool_args: dict = {}
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "one-call"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": self.tool_name, "args": self.tool_args, "id": "call-1"}],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def build(tmp_path, *, mode="auto", allow=(), deny=(), tool, args, interrupt_tools=None):
    """One gated tool call through a real graph.

    `interrupt_tools` mirrors what `build_gate` registers in production --
    one entry per mutating tool. It matters since A1.51: the middleware now
    denies an `ask` for a name that has no prompt behind it, so a harness
    passing an empty set would deny calls a real run allows.
    """
    engine = PermissionEngine(
        mode=mode, allow=allow, deny=deny, floor_disable=(), project_root=tmp_path
    )
    audit = AuditLog(tmp_path / "audit.jsonl")
    registered = MUTATING_TOOLS if interrupt_tools is None else interrupt_tools
    agent = create_deep_agent(
        model=OneCallModel(tool_name=tool, tool_args=args),
        backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        middleware=[RudraPermissionMiddleware(engine, audit, mode, interrupt_tools=registered)],
    )
    result = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
    return [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]


def test_a_denied_write_never_reaches_the_disk(tmp_path):
    (message,) = build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert message.status == "error"
    assert not (tmp_path / "secret.txt").exists()


def test_the_denial_message_names_the_rule_that_fired(tmp_path):
    (message,) = build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert "write_file:secret.txt" in message.content


def test_an_allowed_write_reaches_the_disk(tmp_path):
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "ok.txt", "content": "hello"}
    )
    assert message.status != "error"
    assert (tmp_path / "ok.txt").read_text() == "hello"


def test_the_floor_denies_even_in_auto_mode(tmp_path):
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "/etc/hosts", "content": "x"}
    )
    assert message.status == "error"
    assert "outside-root" in message.content


def test_an_ask_decision_is_not_handled_here(tmp_path):
    """ask is interrupt_on's job for a REGISTERED tool.

    Registration is the load-bearing word since A1.51 -- an ask for a name
    with no interrupt entry is denied here instead, which the test below
    covers.
    """
    (message,) = build(
        tmp_path, mode="ask", tool="write_file", args={"file_path": "ok.txt", "content": "y"}
    )
    assert message.status != "error"


def test_an_ask_for_an_unregistered_tool_is_denied_through_a_real_graph(tmp_path):
    """A1.51, end to end rather than against the middleware in isolation."""
    (message,) = build(
        tmp_path,
        mode="ask",
        tool="write_file",
        args={"file_path": "ok.txt", "content": "y"},
        interrupt_tools=frozenset(),
    )
    assert message.status == "error"
    assert not (tmp_path / "ok.txt").exists()


def test_a_denial_is_written_to_the_audit_log(tmp_path):
    build(
        tmp_path,
        deny=("write_file:secret.txt",),
        tool="write_file",
        args={"file_path": "secret.txt", "content": "x"},
    )
    assert (tmp_path / "audit.jsonl").exists()
    assert "secret.txt" in (tmp_path / "audit.jsonl").read_text()


def test_control_plane_tools_pass_through_untouched(tmp_path):
    """update_plan is not a backend tool; it must not be gated (spec §4.6)."""
    engine = PermissionEngine(
        mode="plan", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    middleware = RudraPermissionMiddleware(engine, AuditLog(tmp_path / "a.jsonl"), "plan")
    calls = []

    class Request:
        tool_call = {"name": "update_plan", "args": {"plan_markdown": "- [ ] a.py"}, "id": "c"}

    middleware.wrap_tool_call(Request(), lambda request: calls.append(request) or "handled")
    assert calls, "control-plane tool was gated when it must pass through"


def test_plan_mode_denies_a_write_through_the_real_graph(tmp_path):
    (message,) = build(
        tmp_path, mode="plan", tool="write_file", args={"file_path": "ok.txt", "content": "y"}
    )
    assert message.status == "error"
    assert not (tmp_path / "ok.txt").exists()


def test_an_allowed_mutation_is_audited(tmp_path):
    """Auto mode never denies and never prompts.

    A deny-only middleware therefore left the unattended run with an empty
    audit log — the run whose record matters most. Found by the Step 7
    acceptance run, which produced a file and no audit lines at all.
    """
    import json

    build(tmp_path, tool="write_file", args={"file_path": "ok.txt", "content": "hello"})
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line]
    assert [e["tool"] for e in entries] == ["write_file"]
    assert entries[0]["decision"] == "allow"
    assert entries[0]["mode"] == "auto"


def test_allowed_reads_stay_out_of_the_audit_log(tmp_path):
    """The filter lives in AuditLog; the middleware must not duplicate it."""
    build(tmp_path, tool="read_file", args={"file_path": "ok.txt"})
    assert not (tmp_path / "audit.jsonl").exists()
