"""The volatile files moved to run/; the durable ones did not.

The dangerous failure mode is a path that moved in code while an
agent-facing prompt still names the old one — the coder would then write
where nothing reads. See TODO.md A1.25 for how that class surfaced before.

Step 9c removed the class rather than guarding it: the ledger replaced
PLAN.md and current_task.md, and it is reached only through tools, so no
prompt names a state path any more.
"""

from pathlib import Path

from rudra.state.paths import rudra_paths


def _tools(project_path: Path) -> dict:
    from rudra.loop.ledger import Ledger
    from rudra.loop.tools import create_ledger_tools

    ledger = Ledger()
    path = rudra_paths(project_path).ledger_json
    return {tool.name: tool for tool in create_ledger_tools(ledger, path)}


def test_the_ledger_is_written_under_run(tmp_path: Path) -> None:
    _tools(tmp_path)["add_tasks"].invoke({"descriptions": ["write the parser"]})
    assert rudra_paths(tmp_path).ledger_json.exists()
    assert not (tmp_path / ".rudra" / "ledger.json").exists()


def test_read_ledger_reads_the_run_location(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["write the parser"]})
    assert "write the parser" in tools["read_ledger"].invoke({})


def test_the_planner_prompt_names_no_state_paths(tmp_path: Path) -> None:
    """Step 9c's version of this guard, and a stronger one.

    The old risk was a prompt naming a path that had moved. The ledger is
    reached only through tools, so the prompt names no path at all -- there
    is nothing left to drift.
    """
    from rudra.agent.planner_agent import build_planner_prompt

    prompt = build_planner_prompt("build it", tmp_path)
    assert ".rudra" not in prompt
    assert "PLAN.md" not in prompt
    assert "ledger.json" not in prompt


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


def test_the_artifacts_route_matches_the_paths_module(tmp_path) -> None:
    """The composite's route prefix and the on-disk directory must agree.

    Two independent spellings of the same location — a route string in
    main_agent.build_backend and a Path in state.paths — is exactly the
    drift this module exists to catch.
    """
    from rudra.agent.main_agent import build_backend
    from rudra.config.loader import build_config, reset_config
    from rudra.state.paths import ensure_layout, rudra_paths

    reset_config()
    try:
        ensure_layout(tmp_path)
        backend = build_backend(build_config(tmp_path), tmp_path)
        assert "/artifacts/" in backend.routes
        assert rudra_paths(tmp_path).artifacts.is_dir()
    finally:
        reset_config()
