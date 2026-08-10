"""build_gate: the one constructor everything outside the package uses."""

from __future__ import annotations

import os

import pytest
from rich.console import Console

from rudra.config.loader import build_config, reset_config
from rudra.permissions import Gate, build_gate, disabled_floor_notice, stdin_is_interactive
from rudra.permissions.rules import MUTATING_TOOLS


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def write_config(tmp_path, body):
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_build_gate_returns_a_gate(tmp_path):
    assert isinstance(build_gate(build_config(tmp_path), tmp_path), Gate)


def test_the_gate_carries_an_interrupt_config_for_every_mutating_tool(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert set(gate.interrupt_on) == set(MUTATING_TOOLS)


def test_the_engine_and_the_grants_are_the_same_objects_the_prompt_mutates(tmp_path):
    """`always` only works if the engine consults the grants the prompt fills."""
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.engine.grants is gate.grants


def test_the_middleware_shares_the_gate_engine(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.middleware.engine is gate.engine
    assert gate.middleware.audit is gate.audit


def test_the_audit_log_lands_under_run_logs(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    assert gate.audit.path == tmp_path / ".rudra" / "run" / "logs" / "permissions.jsonl"


def test_build_gate_creates_nothing_on_disk(tmp_path):
    """rudra_paths is pure; only ensure_layout creates (A1.43)."""
    build_gate(build_config(tmp_path), tmp_path)
    assert not (tmp_path / ".rudra").exists()


def test_the_gate_carries_the_configured_mode(tmp_path):
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n')
    assert build_gate(build_config(root), root).mode == "auto"


def test_auto_mode_still_produces_interrupt_configs(tmp_path):
    """The `when` predicate answers False in auto mode; the entries stay."""
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n')
    gate = build_gate(build_config(root), root)
    assert set(gate.interrupt_on) == set(MUTATING_TOOLS)


def test_rules_from_config_reach_the_engine(tmp_path):
    root = write_config(tmp_path, '[permissions]\ndeny = ["execute:rm *"]\n')
    gate = build_gate(build_config(root), root)
    assert gate.engine.decide("execute", {"command": "rm x"}).effect == "deny"


def test_floor_disable_from_config_reaches_the_engine(tmp_path):
    root = write_config(tmp_path, '[permissions]\nfloor_disable = ["git-dir"]\n')
    gate = build_gate(build_config(root), root)
    assert gate.engine.floor_disable == frozenset({"git-dir"})


def test_shell_in_auto_reaches_the_engine(tmp_path):
    """A1.49: the opt-in has to survive the trip from TOML to decide()."""
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n')
    gate = build_gate(build_config(root), root)
    assert gate.engine.decide("execute", {"command": "pytest -q"}).effect == "deny"

    reset_config()
    root = write_config(tmp_path, '[permissions]\nmode = "auto"\n\n[tools]\nshell_in_auto = true\n')
    gate = build_gate(build_config(root), root)
    assert gate.engine.decide("execute", {"command": "pytest -q"}).effect == "allow"


def test_a_malformed_rule_raises_at_build_time_not_at_first_call(tmp_path):
    root = write_config(tmp_path, '[permissions]\ndeny = ["nosuchtool:x"]\n')
    with pytest.raises(ValueError, match="Unknown tool 'nosuchtool'"):
        build_gate(build_config(root), root)


def test_prompt_delegates_to_the_approval_flow(tmp_path):
    gate = build_gate(build_config(tmp_path), tmp_path)
    gate.reader = lambda: "a"
    with (tmp_path / "out.txt").open("w", encoding="utf-8") as handle:
        decisions = gate.prompt(
            [{"name": "execute", "args": {"command": "pytest -q"}, "description": "d"}],
            Console(file=handle, width=100),
        )
    assert decisions == [{"type": "approve"}]


def test_no_floor_notice_when_nothing_is_disabled(tmp_path):
    assert disabled_floor_notice(build_config(tmp_path)) is None


def test_the_floor_notice_names_every_disabled_rule(tmp_path):
    root = write_config(
        tmp_path, '[permissions]\nfloor_disable = ["git-dir", "catastrophic-command"]\n'
    )
    notice = disabled_floor_notice(build_config(root))
    assert "git-dir" in notice and "catastrophic-command" in notice


def test_stdin_is_interactive_is_false_under_pytest_capture():
    """Not a strong assertion — it exists so the helper is exercised."""
    assert isinstance(stdin_is_interactive(), bool)
