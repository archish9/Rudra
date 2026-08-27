"""Tests for main_agent's stable helper functions (C0.5).

These are the seams that survived Step 9c. The orchestration loop they
used to sit beside is gone -- the ledger, the fix loop and the gate live
in `rudra.loop` now, and are tested there. The _check_off_file cases that
were here went with the PLAN.md checklist they ticked.

Fixtures span the D18 target stacks so no Python assumption calcifies.
"""

from __future__ import annotations

from pathlib import Path

from rudra.agent.main_agent import _ensure_agents_md
from rudra.facts import FactStore


def _store() -> FactStore:
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    return store


def test_ensure_agents_md_creates_the_file_once(tmp_path: Path):
    _ensure_agents_md(tmp_path, _store())
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()

    agents.write_text("hand-edited by the user\n", encoding="utf-8")
    _ensure_agents_md(tmp_path, _store())

    assert agents.read_text(encoding="utf-8") == "hand-edited by the user\n"


def test_ensure_agents_md_renders_whatever_facts_exist(tmp_path: Path):
    """D18: a Rust project must not be rendered as a Python one."""
    _ensure_agents_md(tmp_path, _store())
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Rust" in content


def test_ensure_agents_md_handles_an_empty_store(tmp_path: Path):
    _ensure_agents_md(tmp_path, FactStore())
    assert (tmp_path / "AGENTS.md").is_file()


def test_ensure_agents_md_handles_no_facts_at_all(tmp_path: Path):
    _ensure_agents_md(tmp_path, None)
    assert (tmp_path / "AGENTS.md").is_file()


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


# --- OPEN-41: what a provider failure tells the user about resuming ---------


def _ledger_with(*statuses):
    from rudra.loop.ledger import Ledger, TaskStatus

    ledger = Ledger()
    for index, status in enumerate(statuses):
        task = ledger.add(f"task {index}")
        task.status = TaskStatus(status) if isinstance(status, str) else status
    return ledger


def _error():
    from rudra.llm.retry import ProviderUnavailable

    class _Status(Exception):
        status_code = 500

    return ProviderUnavailable("the model provider", 3, _Status())


def test_a_provider_failure_offers_continue_when_tasks_remain():
    """Only when it is TRUE. `Ledger.resumable()` is PENDING-only
    (ledger.py:91-100), so a ledger of DONE and BLOCKED tasks resumes to
    nothing and pointing the user at `--continue` would waste a run."""
    from rudra.agent.main_agent import _provider_failure_message

    text = _provider_failure_message(_error(), _ledger_with("pending", "done"))

    assert "rudra --continue" in text
    assert "1 task" in text


def test_a_provider_failure_offers_nothing_when_the_ledger_is_empty():
    """The measured case: all three of 2026-08-27's dead runs died in a
    planner stage, before `breakdown` had added a single task. `plan()`
    saves an empty ledger at engine.py:873, so the file exists and resumes
    to nothing."""
    from rudra.agent.main_agent import _provider_failure_message

    text = _provider_failure_message(_error(), _ledger_with())

    assert "--continue" not in text
    assert "Provider error" in text


def test_a_provider_failure_offers_nothing_when_every_task_is_finished():
    from rudra.agent.main_agent import _provider_failure_message

    text = _provider_failure_message(_error(), _ledger_with("done", "blocked"))

    assert "--continue" not in text
