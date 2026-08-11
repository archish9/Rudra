"""Tests for main_agent's stable helper functions (C0.5).

Deliberately NOT covered: RudraAgent.run(), per-file dispatch, the retry loop.
Step 9 (C6.1-C6.6) replaces the orchestration loop wholesale, so tests against
it would be written to be deleted. These helpers are the seams that survive.

Fixtures span the D18 target stacks so no Python assumption calcifies.
"""

from __future__ import annotations

from pathlib import Path

from rudra.agent.main_agent import (
    _check_off_file,
    _ensure_agents_md,
    _write_tech_stack_file,
)
from rudra.state import ProjectContext


def test_check_off_file_ticks_the_matching_item(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] main.py\n- [ ] models.py\n", encoding="utf-8")

    _check_off_file(plan, "main.py")

    assert plan.read_text(encoding="utf-8") == "- [x] main.py\n- [ ] models.py\n"


def test_check_off_file_works_for_every_target_stack(tmp_path: Path):
    """D18: the checklist is filenames, whatever the language."""
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        "- [ ] src/main.rs\n- [ ] Cargo.toml\n- [ ] package.json\n- [ ] src/app/app.component.ts\n",
        encoding="utf-8",
    )

    for name in ("src/main.rs", "Cargo.toml", "package.json", "src/app/app.component.ts"):
        _check_off_file(plan, name)

    assert "- [ ]" not in plan.read_text(encoding="utf-8")


def test_check_off_file_ticks_only_the_first_occurrence(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("- [ ] a.py\n- [ ] a.py\n", encoding="utf-8")

    _check_off_file(plan, "a.py")

    assert plan.read_text(encoding="utf-8") == "- [x] a.py\n- [ ] a.py\n"


def test_check_off_file_leaves_the_plan_untouched_when_absent(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    original = "- [ ] main.py\n"
    plan.write_text(original, encoding="utf-8")

    _check_off_file(plan, "nonexistent.rs")

    assert plan.read_text(encoding="utf-8") == original


def test_ensure_agents_md_creates_the_file_once(tmp_path: Path):
    _ensure_agents_md(tmp_path, ProjectContext(primary_language="Rust"))
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()

    agents.write_text("hand-edited by the user\n", encoding="utf-8")
    _ensure_agents_md(tmp_path, ProjectContext(primary_language="Rust"))

    assert agents.read_text(encoding="utf-8") == "hand-edited by the user\n"


def test_ensure_agents_md_handles_no_project_context(tmp_path: Path):
    _ensure_agents_md(tmp_path, None)
    assert (tmp_path / "AGENTS.md").is_file()


def test_write_tech_stack_file_writes_and_returns_the_same_content(tmp_path: Path):
    returned = _write_tech_stack_file(tmp_path, ProjectContext(primary_language="Rust"))
    on_disk = (tmp_path / "tech_stack.md").read_text(encoding="utf-8")
    assert returned == on_disk


def test_write_tech_stack_file_records_a_non_python_stack(tmp_path: Path):
    """D18: a Rust project must not be rendered as a Python one."""
    content = _write_tech_stack_file(
        tmp_path, ProjectContext(primary_language="Rust", framework="clap")
    )
    assert "Rust" in content
    assert "clap" in content


def test_write_tech_stack_file_handles_no_project_context(tmp_path: Path):
    content = _write_tech_stack_file(tmp_path, None)
    assert isinstance(content, str)
    assert (tmp_path / "tech_stack.md").is_file()


# --- Step 8: the auto_branch hook ---


def _cfg_with_auto_branch(tmp_path: Path, *, enabled: bool):
    from rudra.config.loader import build_config

    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(
        f"[tools]\nauto_branch = {'true' if enabled else 'false'}\n", encoding="utf-8"
    )
    return build_config(tmp_path)


def test_maybe_auto_branch_is_a_no_op_when_disabled(tmp_path: Path, monkeypatch):
    from rich.console import Console

    from rudra.agent import main_agent

    def _spy(*args, **kwargs):
        raise AssertionError("auto_branch must not be called when the key is off")

    monkeypatch.setattr(main_agent, "auto_branch", _spy)
    main_agent._maybe_auto_branch(
        tmp_path,
        "task",
        cfg=_cfg_with_auto_branch(tmp_path, enabled=False),
        gate=None,
        console=Console(),
    )


def test_maybe_auto_branch_reports_the_branch_it_made(tmp_path: Path, monkeypatch, capsys):
    from rich.console import Console

    from rudra.agent import main_agent
    from rudra.git.core import BranchOutcome

    monkeypatch.setattr(
        main_agent, "auto_branch", lambda *a, **k: BranchOutcome("rudra/task", None)
    )
    main_agent._maybe_auto_branch(
        tmp_path,
        "task",
        cfg=_cfg_with_auto_branch(tmp_path, enabled=True),
        gate=None,
        console=Console(),
    )
    assert "rudra/task" in capsys.readouterr().out


def test_maybe_auto_branch_prints_the_skip_reason(tmp_path: Path, monkeypatch, capsys):
    """Silence would leave the user unable to tell whether it ran (A1.50)."""
    from rich.console import Console

    from rudra.agent import main_agent
    from rudra.git.core import BranchOutcome

    monkeypatch.setattr(
        main_agent,
        "auto_branch",
        lambda *a, **k: BranchOutcome(None, "the working tree has uncommitted changes"),
    )
    main_agent._maybe_auto_branch(
        tmp_path,
        "task",
        cfg=_cfg_with_auto_branch(tmp_path, enabled=True),
        gate=None,
        console=Console(),
    )
    assert "uncommitted" in capsys.readouterr().out


def test_maybe_auto_branch_never_raises_when_git_explodes(tmp_path: Path, monkeypatch):
    """A branch nobody could create must not end the run."""
    from rich.console import Console

    from rudra.agent import main_agent

    def _boom(*args, **kwargs):
        raise RuntimeError("git went wrong")

    monkeypatch.setattr(main_agent, "auto_branch", _boom)
    main_agent._maybe_auto_branch(
        tmp_path,
        "task",
        cfg=_cfg_with_auto_branch(tmp_path, enabled=True),
        gate=None,
        console=Console(),
    )
