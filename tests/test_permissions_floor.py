"""The four named deny-floor rules (Step 7 spec §4.5, OPEN-117)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.permissions.floor import FLOOR_RULE_NAMES, floor_hit


def test_floor_rule_names_are_the_four_documented_ones():
    assert FLOOR_RULE_NAMES == ("outside-root", "git-dir", "rudra-state", "catastrophic-command")


@pytest.mark.parametrize(
    "relative",
    [".rudra/config.toml", ".rudra/run/ledger.json", ".RUDRA/config.toml", "sub/.rudra/facts.json"],
)
def test_writes_under_rudra_state_violate_rudra_state(tmp_path, relative):
    """OPEN-117: an agent that can rewrite `.rudra/config.toml` under --auto
    rewrites the NEXT run's `[permissions]`. Case-folded, because on the
    default macOS and Windows filesystems `.RUDRA` is the same directory."""
    assert floor_hit("write_file", tmp_path / relative, None, tmp_path) == "rudra-state"
    assert floor_hit("delete", tmp_path / relative, None, tmp_path) == "rudra-state"


def test_a_name_resembling_rudra_state_is_not_a_violation(tmp_path):
    assert floor_hit("write_file", tmp_path / "docs" / ".rudra-notes.md", None, tmp_path) is None


def test_reading_rudra_state_is_not_a_floor_violation(tmp_path):
    """Reads are hidden by the backend, not refused by the floor (OPEN-117)."""
    assert floor_hit("read_file", tmp_path / ".rudra" / "config.toml", None, tmp_path) is None


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


def test_the_floor_does_not_gate_shell_writes(tmp_path):
    """A1.49, pinned so nobody assumes otherwise.

    The path rules apply to write_file/edit_file/delete. A shell command
    that writes outside the project is NOT a floor violation — measured in
    the Step 7 acceptance run, where a denied model wrote the file with
    `echo > /abs/path` instead. Asserting the real behaviour beats implying
    a protection that is not there.
    """
    assert floor_hit("execute", None, "echo hi > /etc/passwd", tmp_path) is None
    assert floor_hit("execute", None, "cp secrets.txt /tmp/", tmp_path) is None
