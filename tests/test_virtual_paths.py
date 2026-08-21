"""What a model-written path resolves to, on every platform (CR-B4, CR-X).

These run identically on Windows, macOS and Linux, which is the point:
`PureWindowsPath` parses Windows spellings on POSIX and `PurePosixPath`
parses POSIX spellings on Windows, so the answer never depends on the host
OS -- only on the shape of the path. A model guesses its path style from
training data, not from the machine it happens to be running on, so a mac
user's run sees `C:\\...` and a Windows user's run sees `/src/app.py`.
"""

from __future__ import annotations

import pytest

from rudra.compat.virtual_paths import (
    looks_windows_absolute,
    virtual_to_host,
    virtual_to_relative,
)


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        # Already relative -- the easy case, and the one the prompt asks for.
        ("src/app.py", "src/app.py"),
        # POSIX absolute. virtual_mode=True means the leading `/` is the
        # PROJECT root, so this is <project>/src/app.py -- not the host's.
        ("/src/app.py", "src/app.py"),
        # The case CR-B4 was about: a path that looks like a system file and
        # is not, because the backend confines it.
        ("/etc/passwd", "etc/passwd"),
        # Windows relative, backslash separated. PurePosixPath would read
        # this as ONE filename called "src\\app.py".
        (r"src\app.py", "src/app.py"),
        # Windows root-relative: no drive, so it is not drive-absolute.
        (r"\src\app.py", "src/app.py"),
        # Windows drive-absolute, naming a directory that is not our root:
        # the anchor is stripped and the rest kept.
        (r"C:\other\x.py", "other/x.py"),
        # UNC. The anchor is \\\\server\\share, so only the tail survives.
        (r"\\server\share\app.py", "app.py"),
        # Mixed separators, which models emit constantly.
        ("C:/other/x.py", "other/x.py"),
    ],
)
def test_every_spelling_resolves_to_a_project_relative_posix_path(spelling, expected, tmp_path):
    assert virtual_to_relative(spelling, tmp_path) == expected


def test_the_real_project_root_is_stripped_when_the_model_spells_it_out(tmp_path):
    """A model that echoes back the absolute path it was shown must not get
    <project>/<project>/src/app.py."""
    spelled_out = str(tmp_path / "src" / "app.py")

    assert virtual_to_relative(spelled_out, tmp_path) == "src/app.py"


def test_the_resolved_root_is_stripped_too(tmp_path):
    """A1.58's case: on macOS /tmp is a symlink to /private/tmp, so the root
    as given and the root as resolved differ for a directory people really
    work in. Both spellings must strip."""
    root = tmp_path / "proj"
    root.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows w/o privilege
        pytest.skip("symlinks not available")

    # Root given by its symlinked spelling; path given by its real one.
    assert virtual_to_relative(str(root / "src" / "app.py"), link) == "src/app.py"


def test_virtual_to_host_lands_inside_the_project(tmp_path):
    host = virtual_to_host("/etc/passwd", tmp_path)

    assert host == tmp_path / "etc" / "passwd"
    # The claim that matters: it is inside the project, so the backend's
    # confinement and the gate's floor now agree about it.
    assert host.is_relative_to(tmp_path)


def test_a_windows_absolute_path_is_recognised_by_shape_not_by_host_os():
    assert looks_windows_absolute(r"C:\x\y.py")
    assert looks_windows_absolute("C:/x/y.py")
    assert looks_windows_absolute(r"\\server\share\y.py")
    # No drive: root-relative, handled as a POSIX-style absolute instead.
    assert not looks_windows_absolute(r"\x\y.py")
    assert not looks_windows_absolute("/x/y.py")
    assert not looks_windows_absolute("x/y.py")


def test_an_empty_path_is_not_an_error(tmp_path):
    assert virtual_to_relative("", tmp_path) == ""


def test_traversal_is_preserved_for_the_floor_to_catch(tmp_path):
    """This function must NOT sanitise `..` away.

    Resolution is the floor's job and it happens after this, against the
    real filesystem -- collapsing `..` here textually would hide a genuine
    escape from the one check that catches it.
    """
    assert ".." in virtual_to_relative("../../etc/hosts", tmp_path)
