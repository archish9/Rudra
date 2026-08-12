"""Precedence and matching for the permission engine (Step 7 spec §4.3, §4.4)."""

from __future__ import annotations

import pytest

from rudra.permissions.rules import (
    ALL_GATED_TOOLS,
    PermissionEngine,
    Rule,
    gated_arg,
    parse_rule,
)


def engine(tmp_path, **kwargs):
    defaults = dict(
        mode="ask", allow=(), deny=(), floor_disable=(), project_root=tmp_path, grants=None
    )
    defaults.update(kwargs)
    return PermissionEngine(**defaults)


# -- parse_rule ------------------------------------------------------------


def test_a_bare_tool_name_parses_to_a_patternless_rule():
    assert parse_rule("write_file") == Rule(tool="write_file", pattern=None)


def test_tool_colon_pattern_parses_into_both_halves():
    assert parse_rule("execute:pytest*") == Rule(tool="execute", pattern="pytest*")


def test_a_pattern_may_itself_contain_a_colon():
    """`execute:git log --format=%H:%s` must not split on the second colon."""
    assert parse_rule("execute:git log --format=%H:%s") == Rule(
        tool="execute", pattern="git log --format=%H:%s"
    )


def test_an_unknown_tool_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown tool 'writefile'"):
        parse_rule("writefile:x")


def test_an_empty_pattern_is_rejected():
    with pytest.raises(ValueError, match="empty pattern"):
        parse_rule("execute:")


def test_every_gated_tool_name_parses():
    for tool in ALL_GATED_TOOLS:
        assert parse_rule(tool).tool == tool


# -- gated_arg -------------------------------------------------------------


def test_gated_arg_is_the_command_for_execute():
    assert gated_arg("execute", {"command": "pytest -q"}) == "pytest -q"


def test_gated_arg_is_the_path_for_file_tools():
    assert gated_arg("write_file", {"file_path": "src/app.py"}) == "src/app.py"


def test_gated_arg_is_none_when_the_expected_key_is_absent():
    assert gated_arg("write_file", {}) is None


# -- precedence ------------------------------------------------------------


def test_reads_are_allowed_silently_by_the_mode_default(tmp_path):
    decision = engine(tmp_path).decide("read_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("allow", "mode-default")


def test_mutations_ask_by_the_mode_default(tmp_path):
    decision = engine(tmp_path).decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("ask", "mode-default")


def test_auto_mode_allows_mutations(tmp_path):
    decision = engine(tmp_path, mode="auto").decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("allow", "mode-default")


def test_plan_mode_denies_mutations(tmp_path):
    decision = engine(tmp_path, mode="plan").decide("write_file", {"file_path": "src/app.py"})
    assert (decision.effect, decision.source) == ("deny", "mode-default")


def test_plan_mode_still_allows_reads(tmp_path):
    assert engine(tmp_path, mode="plan").decide("read_file", {"file_path": "a"}).effect == "allow"


def test_control_plane_tools_are_never_gated(tmp_path):
    """update_plan and friends write only under .rudra/run/ (spec §4.6)."""
    for tool in ("add_tasks", "drop_task", "ask_user"):
        decision = engine(tmp_path, mode="plan").decide(tool, {})
        assert (decision.effect, decision.source) == ("allow", "control-plane")


def test_an_allow_rule_beats_the_mode_default(tmp_path):
    decision = engine(tmp_path, allow=("execute:pytest*",)).decide(
        "execute", {"command": "pytest -q"}
    )
    assert (decision.effect, decision.source) == ("allow", "allow")


def test_a_deny_rule_beats_an_allow_rule(tmp_path):
    decision = engine(tmp_path, allow=("execute:git*",), deny=("execute:git push*",)).decide(
        "execute", {"command": "git push origin main"}
    )
    assert (decision.effect, decision.source) == ("deny", "deny")
    assert decision.rule == "execute:git push*"


def test_the_floor_beats_a_user_allow_rule(tmp_path):
    decision = engine(tmp_path, mode="auto", allow=("execute",)).decide(
        "execute", {"command": "rm -rf /"}
    )
    assert (decision.effect, decision.source) == ("deny", "floor")
    assert decision.rule == "<floor:catastrophic-command>"


def test_the_floor_holds_under_auto_mode(tmp_path):
    decision = engine(tmp_path, mode="auto").decide("write_file", {"file_path": "/etc/hosts"})
    assert (decision.effect, decision.source) == ("deny", "floor")


# -- floor_disable ---------------------------------------------------------


def test_a_disabled_floor_rule_stops_denying(tmp_path):
    decision = engine(tmp_path, mode="auto", floor_disable=("outside-root",)).decide(
        "write_file", {"file_path": "/tmp/sibling/out.txt"}
    )
    assert decision.effect == "allow"


def test_a_disabled_floor_rule_is_still_reported_in_the_source(tmp_path):
    """Turning a rule off changes what Rudra blocks, not what it tells you."""
    decision = engine(tmp_path, mode="auto", floor_disable=("outside-root",)).decide(
        "write_file", {"file_path": "/tmp/sibling/out.txt"}
    )
    assert decision.source == "floor-disabled"
    assert decision.rule == "<floor:outside-root>"


def test_disabling_one_floor_rule_leaves_the_others_armed(tmp_path):
    eng = engine(tmp_path, mode="auto", floor_disable=("outside-root",))
    assert eng.decide("execute", {"command": "rm -rf /"}).source == "floor"
    assert (
        eng.decide("write_file", {"file_path": str(tmp_path / ".git" / "HEAD")}).source == "floor"
    )


# -- matching --------------------------------------------------------------


def test_a_relative_pattern_matches_the_project_relative_path(tmp_path):
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": ".env"}
    )
    assert decision.effect == "deny"


def test_a_relative_pattern_does_not_match_the_same_name_elsewhere(tmp_path):
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": "config/.env"}
    )
    assert decision.effect != "deny"


def test_a_globstar_pattern_matches_at_any_depth(tmp_path):
    decision = engine(tmp_path, deny=("write_file:**/secrets/**",)).decide(
        "write_file", {"file_path": "a/b/secrets/key.txt"}
    )
    assert decision.effect == "deny"


def test_an_anchored_pattern_matches_the_absolute_path(tmp_path):
    decision = engine(
        tmp_path, mode="auto", floor_disable=("outside-root",), deny=("write_file:/etc/**",)
    ).decide("write_file", {"file_path": "/etc/hosts"})
    assert decision.effect == "deny"


def test_an_execute_pattern_matches_across_slashes(tmp_path):
    """Found by executing the plan. A command is not a path.

    Glob `*` refuses to cross `/`, so matching a command as a path made
    `execute:pytest*` fail against `pytest -q tests/x` — the exact case
    `always` exists to cover, and the one a fix loop hits every iteration.
    """
    eng = engine(tmp_path, allow=("execute:pytest*",))
    for command in ("pytest -q", "pytest -q tests/x", "pytest tests/a/b/c.py::test_x"):
        assert eng.decide("execute", {"command": command}).effect == "allow", command


def test_an_execute_deny_also_matches_across_slashes(tmp_path):
    eng = engine(tmp_path, deny=("execute:rm *",))
    assert eng.decide("execute", {"command": "rm -rf build/artifacts"}).effect == "deny"


def test_a_bare_tool_rule_matches_every_call_to_that_tool(tmp_path):
    decision = engine(tmp_path, deny=("execute",)).decide("execute", {"command": "ls"})
    assert decision.effect == "deny"


def test_traversal_is_resolved_before_matching(tmp_path):
    """`src/../.env` is `.env`; matching the model's string would miss it."""
    decision = engine(tmp_path, deny=("write_file:.env",)).decide(
        "write_file", {"file_path": "src/../.env"}
    )
    assert decision.effect == "deny"


def test_a_deny_on_a_symlinked_system_path_still_fires(tmp_path):
    """Found by executing the plan, on macOS where /etc -> /private/etc.

    Matching only the resolved spelling means the obvious rule silently
    never fires. Matching only the given spelling means traversal dodges
    it. Both are offered, so both cases hold.
    """
    eng = engine(
        tmp_path, mode="auto", floor_disable=("outside-root",), deny=("write_file:/etc/**",)
    )
    assert eng.decide("write_file", {"file_path": "/etc/hosts"}).effect == "deny"


def test_offering_both_spellings_does_not_weaken_traversal_protection(tmp_path):
    """The resolved spelling is always a candidate, so an escape still hits."""
    (tmp_path / "src").mkdir()
    eng = engine(tmp_path, deny=("write_file:.env",))
    for spelling in (".env", "src/../.env", "./.env"):
        assert eng.decide("write_file", {"file_path": spelling}).effect == "deny", spelling


# -- session grants --------------------------------------------------------


def test_a_session_grant_beats_the_mode_default(tmp_path):
    from rudra.permissions.grants import SessionGrants

    grants = SessionGrants()
    grants.add(Rule(tool="execute", pattern="pytest*"))
    decision = engine(tmp_path, grants=grants).decide("execute", {"command": "pytest -q"})
    assert (decision.effect, decision.source) == ("allow", "session-grant")


def test_a_deny_rule_beats_a_session_grant(tmp_path):
    from rudra.permissions.grants import SessionGrants

    grants = SessionGrants()
    grants.add(Rule(tool="execute", pattern="git*"))
    decision = engine(tmp_path, deny=("execute:git push*",), grants=grants).decide(
        "execute", {"command": "git push"}
    )
    assert (decision.effect, decision.source) == ("deny", "deny")


# -- unattended shell (A1.49) ---------------------------------------------


def test_auto_mode_denies_execute_unless_opted_in(tmp_path):
    """A1.49: nobody reads the command in unattended mode."""
    decision = engine(tmp_path, mode="auto").decide("execute", {"command": "pytest -q"})
    assert (decision.effect, decision.source) == ("deny", "auto-shell")


def test_auto_mode_allows_execute_once_opted_in(tmp_path):
    decision = engine(tmp_path, mode="auto", shell_in_auto=True).decide(
        "execute", {"command": "pytest -q"}
    )
    assert decision.effect == "allow"


def test_auto_mode_still_allows_filesystem_tools(tmp_path):
    """The backend genuinely confines these, so they stay available."""
    for tool, args in (
        ("write_file", {"file_path": "a.py"}),
        ("edit_file", {"file_path": "a.py"}),
        ("delete", {"file_path": "a.py"}),
    ):
        assert engine(tmp_path, mode="auto").decide(tool, args).effect == "allow", tool


def test_an_explicit_allow_rule_is_itself_opting_in(tmp_path):
    """Naming the command IS the opt-in — the check sits after allow rules."""
    decision = engine(tmp_path, mode="auto", allow=("execute:pytest*",)).decide(
        "execute", {"command": "pytest -q"}
    )
    assert decision.effect == "allow"


def test_ask_mode_is_unaffected_by_the_opt_in(tmp_path):
    """In ask mode the user reads the command, so the gap does not exist."""
    decision = engine(tmp_path, mode="ask").decide("execute", {"command": "pytest -q"})
    assert decision.effect == "ask"


def test_plan_mode_denies_execute_regardless(tmp_path):
    decision = engine(tmp_path, mode="plan", shell_in_auto=True).decide(
        "execute", {"command": "pytest -q"}
    )
    assert decision.effect == "deny"
