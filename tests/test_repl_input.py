"""The REPL's input layer, testable without a terminal (C9.4).

Lifted out of cli.py, which was 1500+ lines and about to gain a completer,
a history file and multiline handling. Everything here is a plain function
of (text, project_path) so none of it needs prompt_toolkit running.
"""

from __future__ import annotations

from pathlib import Path

from rudra.cli_repl import (
    REPL_COMMANDS,
    build_completer,
    expand_mentions,
    history_path,
    wants_more_input,
)

# --- the command table ---------------------------------------------------


def test_the_command_table_is_the_single_source_of_truth():
    """/help and the completer must not be able to disagree about which
    commands exist, so they read the same dict."""
    assert set(REPL_COMMANDS) >= {"/help", "/exit", "/tree"}
    assert all(text for text in REPL_COMMANDS.values()), "every command needs help text"


def test_help_renders_from_that_table(capsys):
    from rudra.cli import _print_help

    _print_help()
    printed = capsys.readouterr().out
    for command in REPL_COMMANDS:
        assert command in printed


# --- @-mentions ----------------------------------------------------------


def test_a_mention_expands_to_a_path(tmp_path: Path):
    (tmp_path / "parser.py").write_text("x = 1")

    out = expand_mentions("fix @parser.py please", tmp_path)

    assert "parser.py" in out
    assert "@parser.py" not in out


def test_a_mention_that_matches_no_file_is_left_alone(tmp_path: Path):
    """An email address in a prompt is not a file reference, and rewriting
    it would corrupt what the user typed."""
    assert expand_mentions("mail bob@example.com", tmp_path) == "mail bob@example.com"


def test_a_mention_of_a_nested_path_works(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1")

    assert "src/app.py" in expand_mentions("read @src/app.py", tmp_path)


def test_several_mentions_all_expand(tmp_path: Path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")

    out = expand_mentions("compare @a.py and @b.py", tmp_path)

    assert "@" not in out


def test_a_mention_escaping_the_project_is_not_expanded(tmp_path: Path):
    """`@../../etc/passwd` is not a project file reference. The backend
    confines what the agent may read; this stops the REPL from politely
    rewriting the request into an absolute path first."""
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("x")
    project = tmp_path / "proj"
    project.mkdir()

    assert expand_mentions("read @../secret.txt", project) == "read @../secret.txt"


# --- multiline -----------------------------------------------------------


def test_a_trailing_backslash_continues_the_line():
    assert wants_more_input("write a function that \\")
    assert not wants_more_input("write a function")


def test_a_blank_line_does_not_continue():
    assert not wants_more_input("")


# --- history -------------------------------------------------------------


def test_history_lives_in_the_volatile_subtree(tmp_path: Path):
    """Per project, because a Rust project's prompts are not a Python
    project's; volatile (D15), because it is convenience, not a fact about
    the project."""
    path = history_path(tmp_path)

    assert path.parent.name == "run"
    assert ".rudra" in str(path)


# --- completion ----------------------------------------------------------


def _offered(completer, text):
    from prompt_toolkit.document import Document

    document = Document(text, cursor_position=len(text))
    return {completion.text for completion in completer.get_completions(document, None)}


def test_the_completer_offers_every_repl_command(tmp_path: Path):
    assert _offered(build_completer(tmp_path), "/") >= set(REPL_COMMANDS)


def test_the_completer_offers_project_files_after_an_at_sign(tmp_path: Path):
    (tmp_path / "parser.py").write_text("x = 1")

    assert any("parser.py" in text for text in _offered(build_completer(tmp_path), "fix @par"))


def test_the_completer_offers_nothing_for_ordinary_prose(tmp_path: Path):
    """A completion menu popping up mid-sentence is worse than none."""
    assert _offered(build_completer(tmp_path), "write a parser") == set()
