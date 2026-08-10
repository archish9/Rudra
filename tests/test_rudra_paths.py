"""The D15 .rudra/ layout: durable files at the root, volatile ones under run/."""

from pathlib import Path

from rudra.state.paths import GITIGNORE_BODY, ensure_layout, rudra_paths


def test_rudra_paths_creates_nothing(tmp_path: Path) -> None:
    """A1.43: asking where things go must not put anything on disk."""
    paths = rudra_paths(tmp_path)
    assert paths.root == tmp_path / ".rudra"
    assert not (tmp_path / ".rudra").exists()


def test_durable_files_sit_at_the_root(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    assert paths.config_toml == tmp_path / ".rudra" / "config.toml"
    assert paths.agents_md == tmp_path / ".rudra" / "AGENTS.md"
    assert paths.project_json == tmp_path / ".rudra" / "project.json"
    assert paths.memory_export == tmp_path / ".rudra" / "memory" / "export"


def test_volatile_files_sit_under_run(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    run = tmp_path / ".rudra" / "run"
    assert paths.run == run
    assert paths.checkpoints_db == run / "checkpoints.db"
    assert paths.plan_md == run / "PLAN.md"
    assert paths.current_task_md == run / "current_task.md"
    assert paths.tech_stack_md == run / "tech_stack.md"
    assert paths.session_id_txt == run / "session_id.txt"
    assert paths.logs == run / "logs"
    assert paths.memory_palace == tmp_path / ".rudra" / "memory" / "palace"


def test_ensure_layout_creates_directories_and_gitignore(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path)
    assert paths.run.is_dir()
    assert paths.logs.is_dir()
    assert paths.memory_export.is_dir()
    assert paths.memory_palace.is_dir()
    gitignore = paths.root / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == GITIGNORE_BODY


def test_gitignore_scopes_only_the_rudra_directory(tmp_path: Path) -> None:
    """D15: Rudra never edits the project's own .gitignore."""
    ensure_layout(tmp_path)
    assert not (tmp_path / ".gitignore").exists()
    body = (tmp_path / ".rudra" / ".gitignore").read_text(encoding="utf-8")
    assert "run/" in body
    assert "memory/palace/" in body
    # No absolute or parent-escaping patterns.
    assert ".." not in body


def test_ensure_layout_is_idempotent(tmp_path: Path) -> None:
    ensure_layout(tmp_path)
    (tmp_path / ".rudra" / "run" / "PLAN.md").write_text("- [x] main.py", encoding="utf-8")
    ensure_layout(tmp_path)
    assert (tmp_path / ".rudra" / "run" / "PLAN.md").read_text(encoding="utf-8") == "- [x] main.py"


def test_ensure_layout_preserves_a_user_edited_gitignore(tmp_path: Path) -> None:
    """Rewriting on every run would silently discard a user's additions."""
    paths = ensure_layout(tmp_path)
    gitignore = paths.root / ".gitignore"
    gitignore.write_text(GITIGNORE_BODY + "scratch/\n", encoding="utf-8")
    ensure_layout(tmp_path)
    assert "scratch/" in gitignore.read_text(encoding="utf-8")


def test_ensure_layout_does_not_touch_durable_files(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "project.json").write_text('{"language": "rust"}', encoding="utf-8")
    (rudra / "AGENTS.md").write_text("# notes", encoding="utf-8")
    ensure_layout(tmp_path)
    assert (rudra / "project.json").read_text(encoding="utf-8") == '{"language": "rust"}'
    assert (rudra / "AGENTS.md").read_text(encoding="utf-8") == "# notes"
