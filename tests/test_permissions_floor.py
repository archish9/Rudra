"""The three named deny-floor rules (Step 7 spec §4.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.permissions.floor import FLOOR_RULE_NAMES, floor_hit


def test_floor_rule_names_are_the_three_documented_ones():
    assert FLOOR_RULE_NAMES == ("outside-root", "git-dir", "catastrophic-command")


@pytest.mark.parametrize("relative", ["src/app.py", "README.md", "a/b/c/d.txt"])
def test_writes_inside_the_project_are_not_a_violation(tmp_path, relative):
    assert floor_hit("write_file", tmp_path / relative, None, tmp_path) is None


@pytest.mark.parametrize("outside", ["/etc/hosts", "/tmp/elsewhere/x.py"])
def test_writes_outside_the_project_root_violate_outside_root(tmp_path, outside):
    assert floor_hit("write_file", Path(outside), None, tmp_path) == "outside-root"


def test_traversal_out_of_the_project_violates_outside_root(tmp_path):
    escaped = (tmp_path / ".." / "sibling.txt").resolve()
    assert floor_hit("write_file", escaped, None, tmp_path) == "outside-root"


def test_writes_under_git_dir_violate_git_dir(tmp_path):
    assert floor_hit("write_file", tmp_path / ".git" / "HEAD", None, tmp_path) == "git-dir"


def test_nested_git_dir_also_violates(tmp_path):
    target = tmp_path / "vendor" / "dep" / ".git" / "config"
    assert floor_hit("write_file", target, None, tmp_path) == "git-dir"


def test_a_file_merely_named_git_is_not_a_violation(tmp_path):
    assert floor_hit("write_file", tmp_path / "git" / "notes.md", None, tmp_path) is None


def test_delete_is_gated_by_the_same_path_rules(tmp_path):
    assert floor_hit("delete", Path("/etc/hosts"), None, tmp_path) == "outside-root"


def test_read_file_is_never_a_floor_violation(tmp_path):
    """The floor governs destruction, not reading. Reads are ungated (§4.3)."""
    assert floor_hit("read_file", Path("/etc/hosts"), None, tmp_path) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "  rm   -rf   /  ",
        "sudo rm -rf /",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
    ],
)
def test_catastrophic_commands_are_denied(tmp_path, command):
    assert floor_hit("execute", None, command, tmp_path) == "catastrophic-command"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "rm -rf ./node_modules",
        "pytest -q",
        "git status",
        "dd if=in.bin of=out.bin",
    ],
)
def test_ordinary_commands_are_not_denied(tmp_path, command):
    assert floor_hit("execute", None, command, tmp_path) is None
