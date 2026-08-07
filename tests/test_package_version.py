"""Guards A1.1 — rudra.__version__ drifted from pyproject.toml's [project] version.

__init__.py hardcoded "0.1.0" while pyproject declared "0.2.0", so both
`rudra --version` (cli.py:153) and the startup banner (cli.py:83) printed a
number that had been wrong since the 0.2.0 bump. The fix reads installed
package metadata, making pyproject.toml the single source; this test asserts
the two can never disagree again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import rudra

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_version_matches_pyproject() -> None:
    assert rudra.__version__ == _declared_version(), (
        f"rudra.__version__ is {rudra.__version__!r} but pyproject.toml declares "
        f"{_declared_version()!r}. If you just bumped the version, re-run `uv sync` "
        "so the installed metadata catches up."
    )


def test_version_is_read_from_package_metadata() -> None:
    """The drift returns the moment someone reassigns a literal as the primary source."""
    source = Path(rudra.__file__).read_text(encoding="utf-8")
    assert "importlib.metadata" in source, (
        "__init__.py must derive __version__ from installed metadata, not a literal"
    )
