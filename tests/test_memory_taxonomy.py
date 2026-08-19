r"""The project name, turned into something a palace will accept.

mempalace's sanitize_name (config.py:73) enforces
^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$ -- begins and ends
alphanumeric, 128 chars max, no path separators. This module is a pure
re-implementation of that contract so a bad project name fails at store
construction with a Rudra error, rather than inside a mempalace call
stack on the first write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.memory.taxonomy import ROOMS, TaxonomyError, wing_for


def test_an_ordinary_project_name_passes_through() -> None:
    assert wing_for(Path("/home/a/code/RudraAnvil")) == "RudraAnvil"


def test_hyphens_and_dots_are_kept() -> None:
    assert wing_for(Path("/tmp/my-app.v2")) == "my-app.v2"


def test_a_leading_underscore_is_trimmed_not_rejected() -> None:
    assert wing_for(Path("/tmp/_scratch")) == "scratch"


def test_a_trailing_dot_is_trimmed() -> None:
    assert wing_for(Path("/tmp/my-app.")) == "my-app"


def test_an_over_long_name_is_truncated_to_the_limit() -> None:
    wing = wing_for(Path("/tmp") / ("a" * 200))
    assert len(wing) == 128


def test_a_name_with_nothing_usable_is_a_loud_error() -> None:
    with pytest.raises(TaxonomyError) as exc:
        wing_for(Path("/tmp/___"))
    assert "___" in str(exc.value)


def test_the_rooms_are_the_four_from_entry() -> None:
    assert ROOMS == ("decisions", "tasks", "blockers", "preferences")


@pytest.mark.parametrize("name", ["RudraAnvil", "my-app.v2", "a b c", "o'brien-tools"])
def test_every_result_satisfies_mempalaces_own_regex(name: str) -> None:
    """The contract this module claims to mirror, checked against the real one."""
    import re

    safe = re.compile(r"^(?:[^\W_]|[^\W_][\w .'-]{0,126}[^\W_])$")
    assert safe.match(wing_for(Path("/tmp") / name))
