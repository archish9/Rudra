"""The volatile files moved to run/; the durable ones did not.

The dangerous failure mode is a path that moved in code while an
agent-facing prompt still names the old one — the coder would then write
where nothing reads. See TODO.md A1.25 for how that class surfaced before.
"""

from pathlib import Path

from rudra.state.paths import rudra_paths


def _tools(project_path: Path) -> dict:
    from rudra.tools.planning_tools import create_planning_tools

    return {tool.name: tool for tool in create_planning_tools(project_path)}


def test_planning_tools_write_plan_under_run(tmp_path: Path) -> None:
    _tools(tmp_path)["update_plan"].invoke({"plan_markdown": "- [ ] main.py"})
    assert rudra_paths(tmp_path).plan_md.exists()
    assert not (tmp_path / ".rudra" / "PLAN.md").exists()


def test_read_plan_reads_the_run_location(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    tools["update_plan"].invoke({"plan_markdown": "- [ ] main.py"})
    assert "main.py" in tools["read_plan"].invoke({})


def test_task_assignment_lands_under_run(tmp_path: Path) -> None:
    _tools(tmp_path)["write_task_assignment"].invoke(
        {"file_path": "main.py", "instructions": "do it"}
    )
    assert rudra_paths(tmp_path).current_task_md.exists()
    assert not (tmp_path / ".rudra" / "current_task.md").exists()


def test_durable_files_did_not_move(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    assert paths.agents_md == tmp_path / ".rudra" / "AGENTS.md"
    assert paths.project_json == tmp_path / ".rudra" / "project.json"


def test_project_config_manager_still_uses_the_durable_location(tmp_path: Path) -> None:
    from rudra.state import ProjectConfigManager

    assert ProjectConfigManager(tmp_path).config_file == rudra_paths(tmp_path).project_json


def _sources() -> str:
    root = Path(__file__).resolve().parent.parent / "src" / "rudra"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))


def test_no_source_names_a_stale_volatile_path() -> None:
    """Prompt strings and code must agree. This is the whole risk of C0.9."""
    text = _sources()
    for stale in (
        ".rudra/PLAN.md",
        ".rudra/current_task.md",
        ".rudra/tech_stack.md",
        ".rudra/checkpoints.db",
        ".rudra/session_id.txt",
    ):
        assert stale not in text, f"{stale} moved to .rudra/run/ — update this reference"


def test_agents_md_reference_is_left_alone() -> None:
    """AGENTS.md is durable; the planner's memory= must still point at it."""
    assert ".rudra/AGENTS.md" in _sources()
