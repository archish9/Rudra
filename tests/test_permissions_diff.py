"""What the user sees before approving a write (Step 7 spec §6.4).

This is what closes A1.16 -- files silently overwritten, no diff, no
confirm. A prompt that shows nothing does not close it.
"""

from __future__ import annotations

from rudra.permissions.diff import render


def test_a_new_file_reports_size_rather_than_a_diff(tmp_path):
    preview = render("write_file", {"file_path": "new.py", "content": "a\nb\nc\n"}, tmp_path)
    assert "new file" in preview.header
    assert "3 lines" in preview.header


def test_a_new_file_shows_its_first_lines(tmp_path):
    preview = render(
        "write_file", {"file_path": "new.py", "content": "import os\nprint(1)\n"}, tmp_path
    )
    assert "import os" in preview.body


def test_a_new_file_shows_at_most_ten_lines(tmp_path):
    content = "".join(f"line{n}\n" for n in range(50))
    preview = render("write_file", {"file_path": "new.py", "content": content}, tmp_path)
    assert preview.body.count("\n") <= 11
    assert preview.truncated


def test_an_overwrite_produces_a_unified_diff(tmp_path):
    (tmp_path / "app.py").write_text("old line\n", encoding="utf-8")
    preview = render("write_file", {"file_path": "app.py", "content": "new line\n"}, tmp_path)
    assert "-old line" in preview.body
    assert "+new line" in preview.body


def test_an_overwrite_header_carries_the_change_counts(tmp_path):
    (tmp_path / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
    preview = render("write_file", {"file_path": "app.py", "content": "a\nB\nc\nd\n"}, tmp_path)
    assert "+2" in preview.header and "-1" in preview.header
    assert "overwrite" in preview.header


def test_an_unchanged_overwrite_says_so(tmp_path):
    (tmp_path / "app.py").write_text("same\n", encoding="utf-8")
    preview = render("write_file", {"file_path": "app.py", "content": "same\n"}, tmp_path)
    assert "no change" in preview.header


def test_a_long_diff_is_capped(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"old{n}\n" for n in range(100)), encoding="utf-8")
    preview = render(
        "write_file",
        {"file_path": "app.py", "content": "".join(f"new{n}\n" for n in range(100))},
        tmp_path,
    )
    assert preview.truncated
    assert "more changed lines" in preview.body


def test_full_defeats_the_cap(tmp_path):
    (tmp_path / "app.py").write_text("".join(f"old{n}\n" for n in range(100)), encoding="utf-8")
    args = {"file_path": "app.py", "content": "".join(f"new{n}\n" for n in range(100))}
    capped = render("write_file", args, tmp_path)
    full = render("write_file", args, tmp_path, full=True)
    assert len(full.body) > len(capped.body)
    assert not full.truncated


def test_edit_file_diffs_the_two_strings(tmp_path):
    preview = render(
        "edit_file",
        {"file_path": "app.py", "old_string": "def run():", "new_string": "def run(argv):"},
        tmp_path,
    )
    assert "-def run():" in preview.body
    assert "+def run(argv):" in preview.body


def test_delete_reports_the_current_size(tmp_path):
    (tmp_path / "gone.py").write_text("a\nb\n", encoding="utf-8")
    preview = render("delete", {"file_path": "gone.py"}, tmp_path)
    assert "delete" in preview.header
    assert "2 lines" in preview.header


def test_execute_shows_the_command_and_cwd(tmp_path):
    preview = render("execute", {"command": "pytest -q"}, tmp_path)
    assert "pytest -q" in preview.body
    assert str(tmp_path) in preview.body


def test_binary_content_reports_size_and_does_not_render(tmp_path):
    preview = render("write_file", {"file_path": "blob.bin", "content": "\x00\x01"}, tmp_path)
    assert "binary" in preview.header.lower()
    assert preview.body == ""


def test_oversized_content_reports_size_and_does_not_render(tmp_path):
    huge = "x" * (2 * 1024 * 1024)
    preview = render("write_file", {"file_path": "big.txt", "content": huge}, tmp_path)
    assert "too large" in preview.header.lower()
    assert preview.body == ""


def test_an_unreadable_existing_file_degrades_to_a_size_header(tmp_path):
    """A diff we cannot compute must not abort the approval prompt."""
    (tmp_path / "sub").mkdir()
    preview = render("write_file", {"file_path": "sub", "content": "x"}, tmp_path)
    assert preview.body == ""
    assert preview.header
