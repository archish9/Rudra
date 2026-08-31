"""Tests for main_agent's stable helper functions (C0.5).

These are the seams that survived Step 9c. The orchestration loop they
used to sit beside is gone -- the ledger, the fix loop and the gate live
in `rudra.loop` now, and are tested there. The _check_off_file cases that
were here went with the PLAN.md checklist they ticked.

Fixtures span the D18 target stacks so no Python assumption calcifies.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args, get_type_hints

from rudra.agent import planner_agent
from rudra.agent.main_agent import _ensure_agents_md
from rudra.agent.planner_agent import PLANNER_FS_TOOLS
from rudra.facts import FactStore


def _tools_the_planner_is_denied() -> set[str]:
    """deepagents' full filesystem tool set, minus what the planner keeps.

    Read off `PLANNER_FS_TOOLS`'s own annotation rather than restated here.
    The declaration is `list[Literal[...8 names]]` and the value is the
    four the planner may call, so the difference is the denied set and it
    grows by itself if deepagents adds a tool.
    """
    hint = get_type_hints(planner_agent)["PLANNER_FS_TOOLS"]
    every = set(get_args(get_args(hint)[0]))
    assert every >= set(PLANNER_FS_TOOLS), "the annotation stopped covering the value"
    return every - set(PLANNER_FS_TOOLS)


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


def test_the_template_names_no_tool_the_planner_does_not_have(tmp_path: Path):
    """OPEN-66. AGENTS.md is read by the planner and nothing else.

    `planner_agent.py` passes `memory=[".rudra/AGENTS.md"]`, so this file
    lands in the planner's system prompt -- and the planner's filesystem
    middleware is built with `tools=PLANNER_FS_TOOLS`, which is read-only
    by CR-C2's decision. The shipped template told it to
    "Update it using edit_file after completing any task", so on run14 the
    planner called `edit_file`, was told it was not a valid tool, read the
    file, and was told the same thing again (debug events 31-34).

    Both directions on purpose, which is what OPEN-36 used and OPEN-64's
    rule asks for: a template naming a tool the planner lacks is the
    defect, and a test enumerating today's four names would pass while
    saying nothing about tomorrow's fifth.
    """
    _ensure_agents_md(tmp_path, _store())
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    withheld = _tools_the_planner_is_denied()
    assert withheld, "the planner is denied nothing; this test would be vacuous"
    named = sorted(tool for tool in withheld if tool in content)
    assert not named, (
        f"AGENTS.md is the planner's prompt and names {named}, which "
        f"PLANNER_FS_TOOLS withholds. The planner obeys it and burns a call."
    )


def test_the_template_does_not_claim_an_agent_maintains_it(tmp_path: Path):
    """OPEN-66's other half. Since C7.3 the file is written by PYTHON.

    `record_task_in_memory` and `summarise_architecture` in
    `loop/engine.py` own it. No agent has been meant to update it since
    Step 14, so an instruction to do so is not merely unusable -- it is
    false about who maintains the file.
    """
    _ensure_agents_md(tmp_path, _store())
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Update it using" not in content
