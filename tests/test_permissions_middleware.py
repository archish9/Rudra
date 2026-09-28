"""The deny short-circuit (Step 7 spec §4.1).

Driven through a real compiled graph so the assertion is that the file was
not written, not merely that a function returned something.
"""

from __future__ import annotations

from types import SimpleNamespace

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
    one entry per mutating tool. It matters since A1.53: the middleware now
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
    assert "in this project's configuration" in message.content


# --- OPEN-163: a call the gate cannot judge is told why, not blamed on a rule --
#
# `decide` fails closed on a mutating call with no usable path (CR-B2, CR-B7),
# and that is right. What it SAID was not: `<unresolvable-path>` reached the
# user-rule branch, so run 1808f56c8329's coder -- which had sent `write_file`
# with `content` and no `file_path`, 159 s of generated body -- was told it
# "matches the deny rule '<unresolvable-path>' in this project's
# configuration. Do not retry this call." The project's `deny` was empty, the
# missing argument was never named, and re-sending WITH it is the correction.


def _audit(tmp_path):
    import json

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _assert_not_blamed_on_a_rule(text):
    assert "configuration" not in text
    assert "Do not retry" not in text
    assert "<unresolvable-path>" not in text


def test_a_write_with_no_path_is_told_the_argument_is_missing(tmp_path):
    (message,) = build(tmp_path, tool="write_file", args={"content": "x"})
    assert message.status == "error"
    assert "without a file_path argument (it received: content)" in message.content
    assert "Nothing was written." in message.content
    assert "Send the call again with file_path set" in message.content
    assert "the same content" in message.content
    _assert_not_blamed_on_a_rule(message.content)
    # The record is unchanged: still a fail-closed denial, as the docs say.
    (entry,) = _audit(tmp_path)
    assert (entry["decision"], entry["source"], entry["rule"]) == (
        "deny",
        "fail-closed",
        "<unresolvable-path>",
    )


def test_a_write_with_no_arguments_says_so(tmp_path):
    (message,) = build(tmp_path, tool="write_file", args={})
    assert "without a file_path argument (it received no arguments)" in message.content
    _assert_not_blamed_on_a_rule(message.content)


def test_a_null_path_reads_as_a_missing_one(tmp_path):
    (message,) = build(tmp_path, tool="write_file", args={"file_path": None, "content": "x"})
    assert "without a file_path argument (it received: content)" in message.content


def test_a_path_that_is_not_a_string_is_not_called_missing(tmp_path):
    """`gated_arg` answers None for a non-string too (CR-B2's fail-closed case,
    `test_permissions_rules.py`), and "called without a file_path" would be false."""
    (message,) = build(tmp_path, tool="write_file", args={"file_path": 123, "content": "x"})
    assert message.status == "error"
    assert "file_path that is not a string (it was int)" in message.content
    assert "without" not in message.content
    assert "Send the call again with file_path set" in message.content
    _assert_not_blamed_on_a_rule(message.content)


def test_an_edit_and_a_delete_with_no_path_name_their_own_outcome(tmp_path):
    (edit,) = build(tmp_path, tool="edit_file", args={"old_string": "a", "new_string": "b"})
    assert "edit_file was called without a file_path argument" in edit.content
    assert "(it received: new_string, old_string)" in edit.content
    assert "Nothing was changed." in edit.content
    assert "the same content" not in edit.content
    _assert_not_blamed_on_a_rule(edit.content)

    (delete,) = build(tmp_path, tool="delete", args={})
    assert "Nothing was deleted." in delete.content
    _assert_not_blamed_on_a_rule(delete.content)


def test_a_path_that_cannot_be_resolved_is_told_so(tmp_path):
    """CR-B7's branch: the argument is there, but a NUL byte cannot be resolved."""
    (message,) = build(tmp_path, tool="write_file", args={"file_path": "a\x00b", "content": "x"})
    assert message.status == "error"
    assert "could not be resolved" in message.content
    assert "Send the call again with a plain project path." in message.content
    assert "without" not in message.content
    _assert_not_blamed_on_a_rule(message.content)
    assert not list(tmp_path.glob("a*b"))


def _unregistered_denial(tmp_path, tool, args):
    """The gate on a stack whose agent does NOT hold `tool`.

    One gate middleware is inserted on every stack (`subagents/build.py:486`,
    `agent/planner_agent.py:735`), and the planner stages, the reviewer and
    general-purpose hold no write tool. langgraph hands such a call over with
    `request.tool` None -- pinned in `tests/test_deepagents_contract.py`, and
    read the same way by `middleware/tool_route.py`.
    """
    engine = PermissionEngine(
        mode="auto", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    middleware = RudraPermissionMiddleware(
        engine, AuditLog(tmp_path / "audit.jsonl"), "auto", interrupt_tools=MUTATING_TOOLS
    )

    request = SimpleNamespace(tool_call={"name": tool, "args": args, "id": "c"}, tool=None)
    reached = []
    result = middleware.wrap_tool_call(request, lambda request: reached.append(request))
    assert not reached, "a call with no path must still be denied (CR-B2)"
    return result


def test_an_agent_without_the_tool_is_not_told_to_send_it_again(tmp_path):
    """OPEN-15: naming a route through a tool the agent lacks is a false answer,
    and on the planner it invites generating the body again (OPEN-100)."""
    message = _unregistered_denial(tmp_path, "write_file", {"content": "x"})
    assert message.status == "error"
    assert "without a file_path argument" in message.content
    assert "write_file is not one of this agent's tools" in message.content
    assert "Nothing was written." in message.content
    assert "Send the call again" not in message.content
    _assert_not_blamed_on_a_rule(message.content)


def test_an_agent_with_the_tool_is_told_to_send_it_again(tmp_path):
    """The same call through the direct seam, with `request.tool` set."""
    engine = PermissionEngine(
        mode="auto", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    middleware = RudraPermissionMiddleware(
        engine, AuditLog(tmp_path / "audit.jsonl"), "auto", interrupt_tools=MUTATING_TOOLS
    )

    request = SimpleNamespace(
        tool_call={"name": "write_file", "args": {"content": "x"}, "id": "c"}, tool=object()
    )
    message = middleware.wrap_tool_call(request, lambda request: None)
    assert "Send the call again with file_path set" in message.content
    assert "not one of this agent's tools" not in message.content


def test_an_allowed_write_reaches_the_disk(tmp_path):
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "ok.txt", "content": "hello"}
    )
    assert message.status != "error"
    assert (tmp_path / "ok.txt").read_text() == "hello"


def test_the_floor_denies_even_in_auto_mode(tmp_path):
    """CR-B4: the escape has to be a real one.

    `/etc/hosts` used to stand in for "outside the root", but every backend
    Rudra builds is virtual_mode=True, so that path is `<project>/etc/hosts`
    and the host's file is never touched. Traversal still leaves the root,
    and that is what the floor is for.
    """
    (message,) = build(
        tmp_path, tool="write_file", args={"file_path": "../../etc/hosts", "content": "x"}
    )
    assert message.status == "error"
    assert "outside-root" in message.content


def test_an_ask_decision_is_not_handled_here(tmp_path):
    """ask is interrupt_on's job for a REGISTERED tool.

    Registration is the load-bearing word since A1.53 -- an ask for a name
    with no interrupt entry is denied here instead, which the test below
    covers.
    """
    (message,) = build(
        tmp_path, mode="ask", tool="write_file", args={"file_path": "ok.txt", "content": "y"}
    )
    assert message.status != "error"


def test_an_ask_for_an_unregistered_tool_is_denied_through_a_real_graph(tmp_path):
    """A1.53, end to end rather than against the middleware in isolation."""
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
    """add_tasks is not a backend tool; it must not be gated (spec §4.6).

    Step 9c replaced update_plan with the ledger tools that superseded it.
    """
    engine = PermissionEngine(
        mode="plan", allow=(), deny=(), floor_disable=(), project_root=tmp_path
    )
    middleware = RudraPermissionMiddleware(engine, AuditLog(tmp_path / "a.jsonl"), "plan")
    calls = []

    class Request:
        tool_call = {"name": "add_tasks", "args": {"descriptions": ["write it"]}, "id": "c"}

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
