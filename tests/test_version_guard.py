"""Unit tests for the deepagents internal-API guards.

Rudra monkeypatches deepagents internals (compat/deepagents_path.py, TODO
U.4) and imports a private module (middleware/task_anchor.py, TODO U.13).
Both must fail immediately and legibly on a version bump.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rudra.compat.version_guard import (
    EXPECTED_DEEPAGENTS_VERSION,
    DeepagentsCompatError,
    require_deepagents_attr,
    require_deepagents_version,
)


def test_expected_version_matches_the_contract_tests():
    from tests.test_deepagents_contract import (
        EXPECTED_DEEPAGENTS_VERSION as contract_version,
    )

    assert EXPECTED_DEEPAGENTS_VERSION == contract_version


def test_version_check_passes_on_the_pinned_version():
    from importlib.metadata import version

    require_deepagents_version("U.4")  # must not raise
    assert version("deepagents") == EXPECTED_DEEPAGENTS_VERSION


def test_version_check_raises_on_mismatch():
    with patch("rudra.compat.version_guard.version", return_value="0.9.9"):
        with pytest.raises(DeepagentsCompatError) as excinfo:
            require_deepagents_version("U.4")

    message = str(excinfo.value)
    assert "0.9.9" in message
    assert EXPECTED_DEEPAGENTS_VERSION in message
    assert "U.4" in message


def test_require_attr_returns_the_attribute():
    fn = require_deepagents_attr(
        "deepagents.middleware._utils", "append_to_system_message", "U.13"
    )
    assert callable(fn)


def test_require_attr_raises_on_missing_attribute():
    with pytest.raises(DeepagentsCompatError) as excinfo:
        require_deepagents_attr(
            "deepagents.middleware._utils", "no_such_function", "U.13"
        )

    message = str(excinfo.value)
    assert "no_such_function" in message
    assert "U.13" in message


def test_require_attr_raises_on_missing_module():
    with pytest.raises(DeepagentsCompatError) as excinfo:
        require_deepagents_attr("deepagents.no_such_module", "anything", "U.13")

    message = str(excinfo.value)
    assert "deepagents.no_such_module" in message
    assert "U.13" in message
