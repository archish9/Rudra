"""CLI permission surface: the TTY gate and honest messaging (spec §6.5)."""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config.loader import reset_config

runner = CliRunner()


class _AgentLaunched(RuntimeError):
    """Raised in place of a real agent run."""


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)

    # A real run calls install_path_normalizer, which monkeypatches
    # deepagents process-wide and breaks the ordering-sensitive guard in
    # test_deepagents_contract.py::test_validate_path_is_shared_object when
    # the whole suite runs together (TODO.md U.21). These tests are about
    # the gate that runs BEFORE the agent, so stub the factory out.
    async def _never_launch(*args, **kwargs):
        raise _AgentLaunched

    monkeypatch.setattr("rudra.cli.create_main_agent", _never_launch)

    reset_config()
    yield
    reset_config()


def test_ask_mode_without_a_tty_exits_two(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert result.exit_code == 2


def test_the_message_names_both_escape_hatches(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert "--auto" in result.output
    assert "permissions.mode" in result.output


def test_no_model_is_constructed_when_the_tty_check_fails(tmp_path, monkeypatch):
    """Failing at second zero is the point — it must cost no inference."""
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    built = []
    monkeypatch.setattr("rudra.llm.factory.build_model", lambda *a, **k: built.append(1))
    runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert built == []


def test_nothing_is_written_to_disk_when_the_tty_check_fails(tmp_path, monkeypatch):
    """The check must precede ensure_layout, not merely precede inference."""
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    runner.invoke(app, ["-d", str(tmp_path), "write a hello world script"])
    assert not (tmp_path / ".rudra").exists()


def test_auto_mode_needs_no_tty(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "--auto", "x"])
    assert result.exit_code != 2


def test_plan_mode_needs_no_tty(tmp_path, monkeypatch):
    """plan mode makes no changes, so it needs no approvals."""
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    result = runner.invoke(app, ["-d", str(tmp_path), "--plan", "x"])
    assert result.exit_code != 2


def test_a_tty_lets_ask_mode_proceed(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: True)
    result = runner.invoke(app, ["-d", str(tmp_path), "x"])
    assert result.exit_code != 2


def test_the_run_notice_describes_what_the_mode_does(tmp_path):
    """Asserted on rendered output, not source.

    A grep for "NOT ENFORCED" matches the docstring explaining why the old
    wording is gone — prose A4.9 says to keep. What matters is what reaches
    the user.
    """
    from rudra.cli import _permission_notice
    from rudra.config.loader import build_config

    for mode, expected in (
        ("ask", "prompting"),
        ("auto", "without prompting"),
        ("plan", "no project changes"),
    ):
        rudra = tmp_path / ".rudra"
        rudra.mkdir(parents=True, exist_ok=True)
        (rudra / "config.toml").write_text(f'[permissions]\nmode = "{mode}"\n', encoding="utf-8")
        reset_config()
        notice = _permission_notice(build_config(tmp_path))
        assert expected in notice
        assert "NOT ENFORCED" not in notice
        assert "Step 7" not in notice


def test_doctor_reports_the_permission_mode(tmp_path):
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "permission" in result.output.lower()


def test_doctor_does_not_claim_permissions_are_unenforced(tmp_path):
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "not enforced" not in result.output.lower()


def test_doctor_reports_whether_shell_is_enabled(tmp_path):
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "shell" in result.output.lower()


def test_the_flag_help_no_longer_mentions_step_seven():
    result = runner.invoke(app, ["--help"])
    assert "Step 7" not in result.output


def test_a_disabled_floor_rule_is_announced_at_run_start(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: True)
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    (rudra / "config.toml").write_text(
        '[permissions]\nfloor_disable = ["git-dir"]\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["-d", str(tmp_path), "x"])
    assert "git-dir" in result.output


def test_doctor_never_swallows_a_section_name(tmp_path):
    """A1.48 again, in the doctor table this time.

    Rich parses `[tools]` as a style tag wherever it appears, not only in
    the error path fixed earlier. Any console string naming a config
    section has to be escaped, so this asserts the rendered output rather
    than trusting each call site to have remembered.
    """
    result = runner.invoke(app, ["doctor", "--offline", "-d", str(tmp_path)])
    assert "[tools]" in result.output


def test_shell_in_auto_is_visible_to_config_list(tmp_path):
    from rudra.cli import _flatten
    from rudra.config.loader import build_config

    keys = {key for key, _ in _flatten(build_config(tmp_path))}
    assert "tools.shell_in_auto" in keys


def test_allow_shell_flag_reaches_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    runner.invoke(app, ["-d", str(tmp_path), "--auto", "--allow-shell", "x"])
    from rudra.config.loader import get_config

    assert get_config().tools.shell_in_auto is True


def test_auto_without_the_flag_leaves_shell_opted_out(tmp_path, monkeypatch):
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: False)
    runner.invoke(app, ["-d", str(tmp_path), "--auto", "x"])
    from rudra.config.loader import get_config

    assert get_config().tools.shell_in_auto is False


# -- auto-accept in the REPL panel (OPEN-30) -------------------------------


def test_the_notice_says_nothing_about_auto_accept_by_default(tmp_path):
    from rudra.cli import _permission_notice
    from rudra.config.loader import build_config
    from rudra.permissions.grants import SessionGrants

    reset_config()
    assert "auto-accept" not in _permission_notice(build_config(tmp_path), SessionGrants())


def test_the_notice_stops_promising_prompts_once_auto_accept_is_on(tmp_path):
    """The REPL panel prints this every turn; after `!` it would be a lie."""
    from rudra.cli import _permission_notice
    from rudra.config.loader import build_config
    from rudra.permissions.grants import SessionGrants

    grants = SessionGrants()
    grants.grant_all()
    reset_config()
    assert "auto-accept" in _permission_notice(build_config(tmp_path), grants)


def test_the_notice_still_works_with_no_grants_at_all(tmp_path):
    """Single-shot has no session object to pass."""
    from rudra.cli import _permission_notice
    from rudra.config.loader import build_config

    reset_config()
    assert "permissions:" in _permission_notice(build_config(tmp_path))
