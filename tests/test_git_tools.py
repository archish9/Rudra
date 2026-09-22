"""The one model-facing git tool, and the containment its path argument needs."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from rudra.config.loader import build_config
from rudra.permissions.rules import PermissionEngine
from rudra.tools.git_tools import create_git_tools
from tests.conftest_git import (
    AutoGate,
    DenyShellGate,
    RecordingAskGate,
    StrictAskGate,
    git,
    make_repo,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


def _tool(project_path: Path, gate):
    tools = create_git_tools(
        project_path, gate=gate, console=Console(), cfg=build_config(project_path)
    )
    assert [t.name for t in tools] == ["git_diff"]
    return tools[0]


def test_git_diff_returns_the_working_tree_diff(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = _tool(repo, AutoGate(tmp_path)).invoke({})
    assert "+changed" in text


def test_git_diff_in_a_plain_directory_explains_rather_than_failing(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    text = _tool(plain, AutoGate(tmp_path)).invoke({})
    assert "not a git repository" in text.lower()


def test_git_diff_says_so_when_there_is_nothing_to_show(repo: Path, tmp_path: Path):
    text = _tool(repo, AutoGate(tmp_path)).invoke({})
    assert "no changes" in text.lower()


def test_git_diff_can_be_limited_to_one_path(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    text = _tool(repo, AutoGate(tmp_path)).invoke({"path": "a.txt"})
    assert "a.txt" in text
    assert "b.txt" not in text


def test_git_diff_inside_the_project_does_not_prompt(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = _tool(repo, StrictAskGate(tmp_path)).invoke({"path": "a.txt"})
    assert "+changed" in text


def test_a_relative_escape_reaches_the_gate(repo: Path, tmp_path: Path):
    """The model-supplied path is the one way into a bypassed command."""
    gate = RecordingAskGate(tmp_path, answer="reject")
    text = _tool(repo, gate).invoke({"path": "../../elsewhere"})
    assert gate.prompted, "an out-of-root path must reach the gate"
    assert gate.prompted[0]["name"] == "execute"
    assert "not permitted" in text.lower()


def test_an_absolute_path_is_virtual_and_does_not_reach_the_gate(repo: Path, tmp_path: Path):
    """OPEN-148: `/etc` is the project's `etc/`, as it is for `read_file` --
    never the machine's. Only a real escape reaches the gate, and
    `test_a_relative_escape_reaches_the_gate` pins that."""
    text = _tool(repo, StrictAskGate(tmp_path)).invoke({"path": "/etc"})
    assert text == "No changes."


def test_an_approved_outside_path_is_allowed_through(repo: Path, tmp_path: Path):
    """Containment routes the call to the gate; it does not hard-refuse."""
    gate = RecordingAskGate(tmp_path, answer="approve")
    _tool(repo, gate).invoke({"path": "../../elsewhere"})
    assert gate.prompted, "the decision belongs to the gate, not to this tool"


def test_a_dotdot_path_that_stays_inside_does_not_prompt(repo: Path, tmp_path: Path):
    """Resolved, not string-matched: src/../a.txt is inside the project."""
    (repo / "src").mkdir()
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = _tool(repo, StrictAskGate(tmp_path)).invoke({"path": "src/../a.txt"})
    assert "+changed" in text


def test_the_diff_is_capped(repo: Path, tmp_path: Path):
    from rudra.tools.git_tools import MAX_DIFF_LINES

    (repo / "a.txt").write_text(
        "\n".join(str(n) for n in range(MAX_DIFF_LINES * 3)) + "\n", encoding="utf-8"
    )
    text = _tool(repo, AutoGate(tmp_path)).invoke({})
    assert "truncated" in text.lower()
    assert len(text.splitlines()) <= MAX_DIFF_LINES + 5


def test_staged_changes_can_be_requested(repo: Path, tmp_path: Path):
    from tests.conftest_git import git

    (repo / "a.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    text = _tool(repo, AutoGate(tmp_path)).invoke({"staged": True})
    assert "+staged" in text


# --- A1.68: an untracked-only tree is not "no changes" ---


def test_git_diff_names_untracked_files_instead_of_claiming_nothing_changed(
    repo: Path, tmp_path: Path
):
    """The greenfield case: every file is new, so `git diff` shows nothing.

    Reporting "No changes in the working tree" there is false, and it is
    what left the reviewer silent on the runs that most needed it.
    """
    (repo / "parser.py").write_text("def parse(): ...\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    text = _tool(repo, AutoGate(tmp_path)).invoke({})

    assert "parser.py" in text
    assert "src/main.rs" in text
    assert "no changes in the working tree" not in text.lower()


def test_git_diff_still_says_no_changes_on_a_genuinely_clean_tree(repo: Path, tmp_path: Path):
    text = _tool(repo, AutoGate(tmp_path)).invoke({})
    assert "no changes" in text.lower()


def test_git_diff_prefers_the_real_diff_when_there_is_one(repo: Path, tmp_path: Path):
    """A tracked edit still returns a diff, untracked files notwithstanding."""
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    text = _tool(repo, AutoGate(tmp_path)).invoke({})

    assert "+changed" in text
    assert "untracked.py" in text, "the model must still learn the new file exists"


def test_git_diff_does_not_list_build_output_as_untracked_work(repo: Path, tmp_path: Path):
    """A Rust target/ holds thousands of files; naming them is noise (A1.66)."""
    for directory in ("target", "node_modules", "__pycache__"):
        built = repo / directory
        built.mkdir()
        (built / "artifact.bin").write_text("generated\n", encoding="utf-8")
    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")

    text = _tool(repo, AutoGate(tmp_path)).invoke({})

    assert "real.py" in text
    for noise in ("target/", "node_modules/", "__pycache__/"):
        assert noise not in text


def test_the_untracked_listing_is_capped(repo: Path, tmp_path: Path):
    """Bounded output is the whole reason this tool exists rather than execute."""
    for index in range(120):
        (repo / f"file{index}.py").write_text("x = 1\n", encoding="utf-8")

    text = _tool(repo, AutoGate(tmp_path)).invoke({})

    assert len(text.splitlines()) < 80
    assert "more" in text.lower()


# --- OPEN-147: Rudra state never reaches the diff ---

KEY = "sk-open147-0123456789abcdef"


def _tracked_state_with_a_key(repo: Path) -> None:
    """A committed Rudra state directory -- `config.toml` is documented as
    worth committing -- with a literal key then added and not committed."""
    configs = [repo / ".rudra" / "config.toml", repo / "nested" / ".Rudra" / "config.toml"]
    for config in configs:
        config.parent.mkdir(parents=True)
        config.write_text('[model.default]\nmodel = "m"\n', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rudra state")
    for config in configs:
        text = config.read_text(encoding="utf-8")
        config.write_text(f'{text}api_key = "{KEY}"\n', encoding="utf-8")
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")


@pytest.mark.parametrize("gate", [AutoGate, DenyShellGate], ids=["allow-shell", "bare-auto"])
@pytest.mark.parametrize(
    "args",
    [{}, {"staged": True}, {"path": ".rudra/config.toml"}, {"path": "nested/.Rudra/config.toml"}],
    ids=["tree", "staged", "state-file", "nested-state-file"],
)
def test_git_diff_never_shows_rudra_state(repo: Path, tmp_path: Path, gate, args):
    """OPEN-147: OPEN-117 took `.rudra/` out of every read tool because
    `config.toml` may hold an `api_key`. In-root `git_diff` runs read-only git
    that no rule or mode reaches (`git/core.py:144`), and it showed the key --
    to the reviewer, whose first step is to call it."""
    _tracked_state_with_a_key(repo)
    if args.get("staged"):
        git(repo, "add", "-A")

    text = _tool(repo, gate(tmp_path)).invoke(args)

    assert KEY not in text
    if "path" not in args:
        assert "+changed" in text  # the user's own change is still shown


# --- OPEN-148: a path is read as every tool reads it ---


@pytest.mark.parametrize(
    "gate", [AutoGate, DenyShellGate, StrictAskGate], ids=["allow-shell", "bare-auto", "ask"]
)
def test_a_virtual_absolute_path_is_the_projects_own_file(repo: Path, tmp_path: Path, gate):
    """OPEN-148: `/a.txt` is `a.txt` to every tool (CR-B4). Read as the host's,
    it took the gated branch -- refused under --auto, a prompt under ask, and
    "No changes." under shell over a file that had changed."""
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    text = _tool(repo, gate(tmp_path)).invoke({"path": "/a.txt"})
    assert "+changed" in text


def test_the_host_spelling_of_a_project_file_is_that_file(repo: Path, tmp_path: Path):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    host = str((repo / "a.txt").resolve())
    text = _tool(repo, StrictAskGate(tmp_path)).invoke({"path": host})
    assert "+changed" in text


def test_a_git_failure_on_the_gated_branch_is_not_no_changes(repo: Path, tmp_path: Path):
    """git exits 128 on a path outside the repository; that is not a clean diff."""
    text = _tool(repo, AutoGate(tmp_path)).invoke({"path": "../outside.txt"})
    assert text != "No changes."
    assert "outside repository" in text


# --- OPEN-146: what the docs now say, pinned from the code side ---


class _SpyGate(AutoGate):
    """`AutoGate` with `execute:git*` denied, counting what reaches the engine."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.engine = PermissionEngine(
            mode="auto",
            allow=(),
            deny=("execute:git*",),
            floor_disable=(),
            project_root=tmp_path,
            shell_in_auto=True,
        )
        self.asked: list[tuple[str, dict]] = []
        decide = self.engine.decide

        def spy(tool: str, args: dict):
            self.asked.append((tool, args))
            return decide(tool, args)

        self.engine.decide = spy


def test_in_root_git_diff_meets_no_rule(repo: Path, tmp_path: Path):
    """OPEN-146: inside the project `git_diff` runs read-only git
    (`git/core.py:31`, `:144`), so no `execute:` rule reaches it -- what the
    docs now say. A path outside the project is decided."""
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    gate = _SpyGate(tmp_path)
    tool = _tool(repo, gate)

    assert "+changed" in tool.invoke({})
    assert gate.asked == []
    assert "not permitted" in tool.invoke({"path": "../outside.txt"}).lower()
    assert [name for name, _ in gate.asked] == ["execute"]
