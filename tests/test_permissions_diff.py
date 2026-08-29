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


# --- OPEN-49: a delete preview that distinguishes what it is deleting ------
#
# `delete` reached the coder in OPEN-49, and it is not `write_file` with a
# smaller blast radius: upstream deletes a directory recursively and its own
# description RECOMMENDS doing so in one call (filesystem.py:1258-1265).
# Before this, every target that was not a readable text file rendered as a
# bare `delete  <path>` -- missing, binary and forty-file directory alike --
# so the one prompt that stands between a recursive delete and the user's
# project told them nothing about which of the three they were approving.


def test_a_directory_delete_says_it_is_a_directory(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a\n", encoding="utf-8")
    preview = render("delete", {"file_path": "src"}, tmp_path)
    assert "directory" in preview.header.lower()


def test_a_directory_delete_counts_what_goes_with_it(tmp_path):
    (tmp_path / "src" / "deep").mkdir(parents=True)
    for name in ("a.py", "b.py"):
        (tmp_path / "src" / name).write_text("x\n", encoding="utf-8")
    (tmp_path / "src" / "deep" / "c.py").write_text("x\n", encoding="utf-8")
    preview = render("delete", {"file_path": "src"}, tmp_path)
    assert "3 files" in preview.header


def test_an_empty_directory_delete_still_reports_a_count(tmp_path):
    (tmp_path / "empty").mkdir()
    preview = render("delete", {"file_path": "empty"}, tmp_path)
    assert "0 files" in preview.header


def test_a_directory_count_is_capped_so_the_prompt_cannot_stall(tmp_path):
    """A `node_modules`-shaped target must not make the user wait on a walk
    whose only purpose is one number in a header."""
    from rudra.permissions.diff import MAX_DELETE_WALK

    target = tmp_path / "big"
    target.mkdir()
    for n in range(MAX_DELETE_WALK + 5):
        (target / f"f{n}.txt").write_text("x", encoding="utf-8")
    preview = render("delete", {"file_path": "big"}, tmp_path)
    assert f"{MAX_DELETE_WALK}+ files" in preview.header


def test_deleting_a_path_that_is_not_there_says_so(tmp_path):
    """A no-op the user should not be asked to approve blind. It rendered
    identically to a binary file before."""
    preview = render("delete", {"file_path": "gone.py"}, tmp_path)
    assert "not found" in preview.header.lower()


def test_an_undecodable_file_delete_still_names_the_path(tmp_path):
    """The third case the old bare header collapsed together: readable
    neither as text nor as a directory, and still a real file the user is
    about to lose. The bytes are deliberately invalid UTF-8 -- `\\x00\\x01`
    decodes fine and would exercise the line-count branch instead.
    """
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    preview = render("delete", {"file_path": "blob.bin"}, tmp_path)
    assert "blob.bin" in preview.header
    assert "not found" not in preview.header.lower()
    assert "directory" not in preview.header.lower()
