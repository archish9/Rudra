"""Guards for the deepagents internals Rudra depends on.

Rudra reaches past deepagents' public API in exactly two places:

  * ``compat/deepagents_path.py`` monkeypatches ``validate_path`` in both
    ``deepagents.backends.utils`` and ``deepagents.middleware.filesystem``
    (TODO.md U.4).
  * ``middleware/task_anchor.py`` imports
    ``deepagents.middleware._utils.append_to_system_message`` — a
    leading-underscore module with no stability guarantee (TODO.md U.13).

Left unguarded, a deepagents bump breaks Rudra somewhere far from the cause.
These helpers make it break at the point of contact, with a message naming
the ledger item to re-verify.

``pyproject.toml`` pins ``deepagents`` exactly for the same reason.
"""

from __future__ import annotations

import importlib
from importlib.metadata import version
from typing import Any

EXPECTED_DEEPAGENTS_VERSION = "0.7.4"

_UPGRADE_HINT = (
    "Rudra reaches into deepagents internals here. Re-verify the monkeypatch "
    "and private-import sites, then update EXPECTED_DEEPAGENTS_VERSION in "
    "src/rudra/compat/version_guard.py and tests/test_deepagents_contract.py "
    "together."
)


class DeepagentsCompatError(RuntimeError):
    """A deepagents internal Rudra depends on has moved or disappeared."""


def require_deepagents_version(todo_ref: str) -> None:
    """Raise unless the installed deepagents matches the exact pin.

    Args:
        todo_ref: The TODO.md item to name in the error, e.g. ``"U.4"``.
    """
    found = version("deepagents")
    if found != EXPECTED_DEEPAGENTS_VERSION:
        raise DeepagentsCompatError(
            f"Rudra pins deepagents=={EXPECTED_DEEPAGENTS_VERSION} but found "
            f"{found}. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        )


def require_deepagents_attr(module_path: str, attr: str, todo_ref: str) -> Any:
    """Import ``module_path`` and return ``attr``, or raise legibly.

    Args:
        module_path: Fully qualified module, e.g.
            ``"deepagents.middleware._utils"``.
        attr: Attribute name to fetch from it.
        todo_ref: The TODO.md item to name in the error, e.g. ``"U.13"``.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise DeepagentsCompatError(
            f"deepagents {version('deepagents')} has no module {module_path!r}, "
            f"which Rudra depends on. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        ) from exc

    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise DeepagentsCompatError(
            f"deepagents {version('deepagents')} has no {module_path}.{attr}, "
            f"which Rudra depends on. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        ) from exc
