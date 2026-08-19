"""Tool ids and visibility.

An MCP tool is addressed as `server__tool` (C4.5). The separator is fixed
rather than configurable because it is also what `config.py` forbids inside a
server name -- one rule, checked where the name enters.

`allow`/`deny` here mean *visibility*, not permission. The permission engine
decides whether a call runs; this module decides whether a given agent can
see and name the id at all. Keeping the two apart is what stops a config file
from becoming a second policy authority (Step 8 spec S8.7).

Imports nothing from Rudra.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

SEPARATOR = "__"


def tool_id(server: str, tool: str) -> str:
    """The id an agent uses to name one server's tool."""
    return f"{server}{SEPARATOR}{tool}"


def split_id(value: str) -> tuple[str, str]:
    """`("kala", "system_bootstrap")` from `"kala__system_bootstrap"`.

    Splits once from the left: a server name can never contain the
    separator (config.py rejects it), while a tool name routinely contains
    single underscores.
    """
    server, found, tool = value.partition(SEPARATOR)
    if not found or not server or not tool:
        msg = f"{value!r} is not a tool id; expected the form server__tool."
        raise ValueError(msg)
    return server, tool


def matches(pattern: str, value: str) -> bool:
    """Glob match, case-sensitive -- tool ids are not case-insensitive."""
    return fnmatchcase(value, pattern)


def filter_ids(
    ids: Iterable[str], *, allow: Sequence[str] = (), deny: Sequence[str] = ()
) -> tuple[str, ...]:
    """The ids that survive, sorted.

    Empty `allow` means "everything", matching how `[permissions] allow`
    reads. `deny` beats `allow`, as it does everywhere else in Rudra.
    """
    surviving = []
    for value in ids:
        if any(matches(pattern, value) for pattern in deny):
            continue
        if allow and not any(matches(pattern, value) for pattern in allow):
            continue
        surviving.append(value)
    return tuple(sorted(surviving))


__all__ = ["SEPARATOR", "filter_ids", "matches", "split_id", "tool_id"]
