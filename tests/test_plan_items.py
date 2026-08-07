"""Guards A1.7 — every `- [ ]` line was treated as a filename.

_parse_pending_files filtered only on the checkbox marker, so a planner
emitting `- [ ] Set up auth` handed the orchestrator the string "Set up auth"
as a target file. Since Step 2 deleted EnforceTargetFileMiddleware and the
success test is still "does the file exist on disk" (A1.8), the coder could
create a file literally named `Set up auth` and leave it behind.

Enforcement is split deliberately: update_plan blocks and explains, so the
model can correct itself; _parse_pending_files filters silently, because
PLAN.md is plain text that the user and edit_file can both rewrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.agent.main_agent import _parse_pending_files
from rudra.tools.planning_tools import create_planning_tools, looks_like_path


@pytest.mark.parametrize(
    "item",
    ["main.py", "src/models.py", "Makefile", "Dockerfile", ".gitignore", "requirements.txt"],
)
def test_real_filenames_are_accepted(item: str) -> None:
    assert looks_like_path(item)


@pytest.mark.parametrize(
    "item",
    [
        "Create main.py",
        "Set up auth",
        "Install dependencies",
        "Create FastAPI project structure",
        "Add tests.",
        "",
        "   ",
    ],
)
def test_prose_is_rejected(item: str) -> None:
    assert not looks_like_path(item)


def test_single_word_prose_is_a_known_accepted_gap() -> None:
    """N4: pinned deliberately so the gap is a recorded decision, not a surprise.

    Closing it needs a static allowlist of extensionless filenames (ruled out
    by TODO.md §0.5) or an LLM judgment (ruled out by D9). The real fix is
    C6.6's deterministic completion gate in Step 9.
    """
    assert looks_like_path("authentication")


def test_update_plan_rejects_prose_without_writing(tmp_path: Path) -> None:
    update_plan, _read_plan, _assign = create_planning_tools(tmp_path, task="build an app")

    result = update_plan.invoke(
        {"plan_markdown": "- [ ] main.py\n- [ ] Set up auth\n- [ ] Install dependencies"}
    )

    assert result.startswith("REJECTED:")
    assert "Set up auth" in result
    assert "Install dependencies" in result
    assert not (tmp_path / ".rudra" / "PLAN.md").exists(), "nothing may be written on rejection"


def test_update_plan_accepts_a_clean_plan(tmp_path: Path) -> None:
    update_plan, _read_plan, _assign = create_planning_tools(tmp_path, task="build an app")

    result = update_plan.invoke({"plan_markdown": "- [ ] main.py\n- [ ] models.py"})

    assert result.startswith("Plan saved.")
    assert (tmp_path / ".rudra" / "PLAN.md").read_text(encoding="utf-8") == (
        "- [ ] main.py\n- [ ] models.py"
    )


def test_parse_pending_files_skips_prose() -> None:
    """The defensive half: PLAN.md is user-editable, so update_plan is not the only writer."""
    plan = "- [ ] main.py\n- [ ] Set up auth\n- [x] done.py\n- [ ] src/models.py"
    assert _parse_pending_files(plan) == ["main.py", "src/models.py"]
