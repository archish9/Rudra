"""AGENTS.md as a living document (Step 12c, C7.3).

Pure string transforms: the file is markdown, and every operation here is
"change one section, leave the others exactly as they were". Editing it by
regex over the whole file is how the Session Log would eat the Architecture
Notes.
"""

from __future__ import annotations

from rudra.context.agents_md import (
    SESSION_LOG_ENTRIES,
    append_session_entry,
    format_entry,
    replace_section,
    section_body,
)

DOC = """# Project Memory

## Project Facts
language = Rust

## Project Structure
(not yet built)

## Architecture Notes
(none yet)

## Session Log
(no sessions yet)
"""


def test_section_body_reads_one_section():
    assert section_body(DOC, "Project Facts").strip() == "language = Rust"


def test_section_body_of_an_absent_section_is_empty():
    assert section_body(DOC, "Nonexistent") == ""


def test_replace_section_changes_only_that_section():
    updated = replace_section(DOC, "Architecture Notes", "The parser is hand-rolled.")

    assert "The parser is hand-rolled." in updated
    assert section_body(updated, "Project Facts").strip() == "language = Rust"
    assert section_body(updated, "Session Log").strip() == "(no sessions yet)"


def test_replace_section_keeps_the_heading_order():
    updated = replace_section(DOC, "Project Structure", "src/main.rs")
    headings = [line for line in updated.splitlines() if line.startswith("## ")]

    assert headings == [
        "## Project Facts",
        "## Project Structure",
        "## Architecture Notes",
        "## Session Log",
    ]


def test_replace_section_on_an_absent_section_appends_it():
    updated = replace_section(DOC, "New Section", "body")
    assert section_body(updated, "New Section").strip() == "body"


def test_the_first_entry_replaces_the_placeholder():
    """ "(no sessions yet)" must not survive alongside a real entry."""
    updated = append_session_entry(DOC, "- 2026-08-19 t1 write the parser")

    assert "(no sessions yet)" not in updated
    assert "- 2026-08-19 t1 write the parser" in updated


def test_entries_accumulate_newest_last():
    updated = append_session_entry(DOC, "- first")
    updated = append_session_entry(updated, "- second")
    body = section_body(updated, "Session Log")

    assert body.index("- first") < body.index("- second")


def test_the_log_is_capped_oldest_dropped_first():
    """S12.4: AGENTS.md reaches the planner through memory=, so an
    uncapped log is a per-run tax that grows without bound."""
    text = DOC
    for index in range(SESSION_LOG_ENTRIES + 5):
        text = append_session_entry(text, f"- entry {index}")

    body = section_body(text, "Session Log")
    lines = [line for line in body.splitlines() if line.startswith("- ")]

    assert len(lines) == SESSION_LOG_ENTRIES
    assert "- entry 0" not in body
    assert f"- entry {SESSION_LOG_ENTRIES + 4}" in body


def test_capping_leaves_the_other_sections_alone():
    text = DOC
    for index in range(SESSION_LOG_ENTRIES + 5):
        text = append_session_entry(text, f"- entry {index}")

    assert section_body(text, "Project Facts").strip() == "language = Rust"


def test_a_multi_line_entry_counts_as_one():
    """An entry is a bullet plus its indented files, not N lines."""
    entry = format_entry("2026-08-19", "t1", "write the parser", ("src/a.py", "src/b.py"))
    text = append_session_entry(DOC, entry)
    body = section_body(text, "Session Log")

    assert len([line for line in body.splitlines() if line.startswith("- ")]) == 1
    assert "src/a.py" in body
    assert "src/b.py" in body


def test_format_entry_survives_a_task_that_touched_nothing():
    entry = format_entry("2026-08-19", "t1", "investigate", ())
    assert "t1" in entry
    assert "investigate" in entry


def test_module_imports_nothing_from_rudra():
    import ast
    import pathlib

    import rudra.context.agents_md as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [name for name in imported if name.startswith("rudra")]
