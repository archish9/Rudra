"""The terminal approval prompt (Step 7 spec §6.3).

The reader is injected, so every branch is exercised without a TTY.
"""

from __future__ import annotations

import json
import shlex

from rich.console import Console

from rudra.permissions.approval import (
    MAX_APPROVAL_ROUNDS,
    decide_action_requests,
    suggest_grant,
)
from rudra.permissions.audit import AuditLog
from rudra.permissions.grants import SessionGrants
from rudra.permissions.rules import PermissionEngine, Rule


def scripted(*keys):
    """A reader returning each key in turn, then raising if over-consumed."""
    remaining = list(keys)

    def read() -> str:
        if not remaining:
            raise AssertionError("prompt asked for more input than the test scripted")
        return remaining.pop(0)

    return read


def run(tmp_path, requests, keys, *, mode="ask", grants=None, engine=None):
    out_path = tmp_path / "out.txt"
    with out_path.open("w", encoding="utf-8") as handle:
        console = Console(file=handle, width=100)
        audit = AuditLog(tmp_path / "audit.jsonl")
        decisions = decide_action_requests(
            requests,
            engine=engine
            or PermissionEngine(
                mode=mode, allow=(), deny=(), floor_disable=(), project_root=tmp_path
            ),
            grants=grants if grants is not None else SessionGrants(),
            audit=audit,
            console=console,
            project_root=tmp_path,
            mode=mode,
            reader=scripted(*keys),
        )
    output = out_path.read_text(encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"
    entries = (
        [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
        if audit_path.exists()
        else []
    )
    return decisions, output, entries


def request(name="execute", **args):
    return {"name": name, "args": args or {"command": "pytest -q"}, "description": "d"}


# -- suggest_grant ---------------------------------------------------------


def test_a_grant_for_execute_covers_the_first_word():
    assert suggest_grant("execute", "pytest -q tests/") == Rule("execute", "pytest*")


def test_a_grant_for_a_file_tool_covers_that_exact_path():
    assert suggest_grant("write_file", "src/app.py") == Rule("write_file", "src/app.py")


def test_a_grant_with_no_argument_covers_the_whole_tool():
    assert suggest_grant("execute", None) == Rule("execute", None)


# -- decisions -------------------------------------------------------------


def test_approve_returns_an_approve_decision(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["a"])
    assert decisions == [{"type": "approve"}]


def test_reject_returns_a_reject_decision_with_a_message(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["r"])
    assert decisions[0]["type"] == "reject"
    assert decisions[0]["message"]


def test_always_approves_this_call(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["A"])
    assert decisions == [{"type": "approve"}]


def test_always_records_a_session_grant(tmp_path):
    grants = SessionGrants()
    run(tmp_path, [request()], ["A"], grants=grants)
    assert len(grants) == 1


def test_a_granted_call_does_not_prompt_again(tmp_path):
    """The whole point of `always`: a fix loop prompts once, not twelve times."""
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    run(tmp_path, [request()], ["A"], grants=grants, engine=engine)
    # No key is scripted; a second prompt would raise from `scripted`.
    decisions, _, _ = run(
        tmp_path, [request(command="pytest -q tests/x")], [], grants=grants, engine=engine
    )
    assert decisions == [{"type": "approve"}]


def test_the_diff_is_shown_for_a_write(tmp_path):
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    _, output, _ = run(
        tmp_path, [request("write_file", file_path="app.py", content="new\n")], ["a"]
    )
    assert "-old" in output and "+new" in output


def test_d_shows_the_full_diff_then_prompts_again(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"o{n}\n" for n in range(60)), encoding="utf-8")
    decisions, output, _ = run(
        tmp_path,
        [request("write_file", file_path="app.py", content="".join(f"n{n}\n" for n in range(60)))],
        ["d", "a"],
    )
    assert decisions == [{"type": "approve"}]
    # The capped render says "more changed lines"; the full one does not.
    assert output.count("more changed lines") == 1


def test_an_unrecognised_key_reprompts(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["z", "a"])
    assert decisions == [{"type": "approve"}]


def test_each_request_in_a_batch_gets_its_own_decision(tmp_path):
    decisions, _, _ = run(
        tmp_path, [request(command="pytest -q"), request(command="ruff check")], ["a", "r"]
    )
    assert [d["type"] for d in decisions] == ["approve", "reject"]


def test_an_approval_is_audited(tmp_path):
    _, _, entries = run(tmp_path, [request()], ["a"])
    assert entries[0]["decision"] == "approve"
    assert entries[0]["source"] == "prompt"


def test_a_rejection_is_audited(tmp_path):
    _, _, entries = run(tmp_path, [request()], ["r"])
    assert entries[0]["decision"] == "reject"


def test_a_grant_is_audited_as_a_session_grant(tmp_path):
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    _, _, entries = run(tmp_path, [request()], ["A"], grants=grants, engine=engine)
    assert any(entry["source"] == "session-grant" for entry in entries)


def test_a_request_the_engine_already_allows_is_auto_approved(tmp_path):
    """interrupt_on's `when` can over-fire in batch mode; re-check per call."""
    engine = PermissionEngine(
        mode="ask", allow=("execute:pytest*",), deny=(), floor_disable=(), project_root=tmp_path
    )
    decisions, _, _ = run(tmp_path, [request()], [], engine=engine)
    assert decisions == [{"type": "approve"}]


def test_a_request_the_engine_denies_is_auto_rejected(tmp_path):
    engine = PermissionEngine(
        mode="ask", allow=(), deny=("execute:pytest*",), floor_disable=(), project_root=tmp_path
    )
    decisions, _, _ = run(tmp_path, [request()], [], engine=engine)
    assert decisions[0]["type"] == "reject"


def test_a_grant_taken_mid_batch_applies_to_the_rest_of_the_batch(tmp_path):
    """Otherwise `always` still prompts for every sibling call."""
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    decisions, _, _ = run(
        tmp_path,
        [request(command="pytest -q"), request(command="pytest -q tests/x")],
        ["A"],
        grants=grants,
        engine=engine,
    )
    assert [d["type"] for d in decisions] == ["approve", "approve"]


def test_max_approval_rounds_is_a_backstop_not_a_working_limit():
    assert MAX_APPROVAL_ROUNDS >= 50


# -- auto-accept (OPEN-30) -------------------------------------------------


def test_the_menu_offers_auto_accept(tmp_path):
    _, output, _ = run(tmp_path, [request()], ["a"])
    assert "Auto-accept" in output


def test_auto_accept_approves_this_call(tmp_path):
    decisions, _, _ = run(tmp_path, [request()], ["!"])
    assert decisions == [{"type": "approve"}]


def test_auto_accept_sets_the_flag_on_the_grants(tmp_path):
    grants = SessionGrants()
    run(tmp_path, [request()], ["!"], grants=grants)
    assert grants.approve_all is True


def test_auto_accept_adds_no_rule(tmp_path):
    """It is the absence of a question, not a very wide grant."""
    grants = SessionGrants()
    run(tmp_path, [request()], ["!"], grants=grants)
    assert len(grants) == 0


def test_after_auto_accept_the_rest_of_the_batch_does_not_prompt(tmp_path):
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    decisions, _, _ = run(
        tmp_path,
        [request(command="pytest -q"), request("write_file", file_path="a.py", content="x")],
        ["!"],
        grants=grants,
        engine=engine,
    )
    assert [d["type"] for d in decisions] == ["approve", "approve"]


def test_after_auto_accept_a_later_call_does_not_prompt(tmp_path):
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    run(tmp_path, [request()], ["!"], grants=grants, engine=engine)
    # No key is scripted; a second prompt would raise from `scripted`.
    decisions, _, _ = run(
        tmp_path, [request("delete", file_path="a.py")], [], grants=grants, engine=engine
    )
    assert decisions == [{"type": "approve"}]


def test_auto_accept_is_audited(tmp_path):
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )
    _, _, entries = run(tmp_path, [request()], ["!"], grants=grants, engine=engine)
    # "allow", like `always` -- the engine now allows it, so the record is
    # what the engine decided, not which key was pressed.
    assert entries[0]["decision"] == "allow"
    assert entries[0]["source"] == "session-grant-all"


def test_auto_accept_says_what_still_applies(tmp_path):
    """Escalating privilege silently is the thing to avoid."""
    _, output, _ = run(tmp_path, [request()], ["!"])
    lowered = output.lower()
    assert "auto-accept" in lowered
    assert "deny" in lowered and "floor" in lowered


def test_a_denied_call_is_still_refused_after_auto_accept(tmp_path):
    grants = SessionGrants()
    engine = PermissionEngine(
        mode="ask",
        allow=(),
        deny=("execute:curl*",),
        floor_disable=(),
        project_root=tmp_path,
        grants=grants,
    )
    run(tmp_path, [request()], ["!"], grants=grants, engine=engine)
    decisions, _, _ = run(
        tmp_path, [request(command="curl http://x")], [], grants=grants, engine=engine
    )
    assert decisions[0]["type"] == "reject"


# -- OPEN-144: a command grant is a literal prefix of the command as written --


def _granted(tmp_path, command):
    """An ask-mode engine holding exactly the grant `A` would add for `command`."""
    grants = SessionGrants()
    grants.add(suggest_grant("execute", command))
    return PermissionEngine(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=grants
    )


def _effect(engine, command):
    return engine.decide("execute", {"command": command}).effect


def test_a_grant_for_a_quoted_path_names_the_whole_path(tmp_path):
    """The gate's commands are `shlex.join`ed, so a project under a path with
    a space arrives quoted. Split on whitespace, the first word was
    `'/Users/a/My`, and `A` covered every command under every `My*` directory
    -- the project's own `python -c` included."""
    pytest_bin = "/Users/a/My Projects/app/.venv/bin/pytest"
    command = shlex.join([pytest_bin])
    engine = _granted(tmp_path, command)

    assert suggest_grant("execute", command) == Rule("execute", f"{command}*")
    assert _effect(engine, command) == "allow"
    assert _effect(engine, shlex.join([pytest_bin, "-q", "tests/x"])) == "allow"
    for other in (
        shlex.join(["/Users/a/My Projects/app/.venv/bin/python", "-c", "import os"]),
        shlex.join(["/Users/a/My Documents/x.sh"]),
    ):
        assert _effect(engine, other) == "ask", other


def test_a_grant_keeps_the_model_s_own_quoting(tmp_path):
    """Double quotes, as a model writes them, are kept as written: re-quoting
    the word would mint a grant that misses the command it was minted for."""
    command = '"/Users/a/My Projects/app/.venv/bin/pytest" -q'
    engine = _granted(tmp_path, command)
    assert suggest_grant("execute", command) == Rule(
        "execute", '"/Users/a/My Projects/app/.venv/bin/pytest"*'
    )
    assert _effect(engine, command) == "allow"
    assert _effect(engine, '"/Users/a/My Documents/x.sh"') == "ask"


def test_glob_characters_in_the_first_word_are_matched_literally(tmp_path):
    """A `*`, `?` or `[` in a path is part of its name. Unescaped, `a*b`
    granted a sibling directory and `proj[1]` granted nothing at all -- not
    even the command it was minted for."""
    for directory, sibling in (
        ("/tmp/a*b", "/tmp/a-other-b"),
        ("/tmp/a?b", "/tmp/axb"),
        ("/tmp/proj[1]", "/tmp/proj1"),
    ):
        command = shlex.join([f"{directory}/.venv/bin/pytest"])
        engine = _granted(tmp_path, command)
        assert _effect(engine, command) == "allow", command
        assert _effect(engine, f"'{sibling}/.venv/bin/pytest'") == "ask", sibling


def test_an_empty_command_grants_nothing(tmp_path):
    """The patternless fallback covered every command for the session."""
    for command in ("", "   "):
        grant = suggest_grant("execute", command)
        assert grant.pattern is not None, repr(command)
        assert _effect(_granted(tmp_path, command), "pytest -q") == "ask", repr(command)


def test_an_open_quote_in_the_first_word_grants_only_the_text_shown(tmp_path):
    command = "'/Users/a/My Projects/pytest -q"
    assert suggest_grant("execute", command) == Rule("execute", command)
    assert _effect(_granted(tmp_path, command), "'/Users/a/My Documents/x.sh'") == "ask"


def test_plain_first_words_keep_their_grant():
    """Pins that hold before and after: the documented shape, a Windows path
    a model wrote unquoted, and an open quote AFTER the first word."""
    assert suggest_grant("execute", "pytest -q tests/") == Rule("execute", "pytest*")
    assert suggest_grant("execute", r"C:\p\.venv\Scripts\pytest.exe -q") == Rule(
        "execute", r"C:\p\.venv\Scripts\pytest.exe*"
    )
    assert suggest_grant("execute", "pytest 'tests/x") == Rule("execute", "pytest*")
