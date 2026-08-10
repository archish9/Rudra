"""Session grants: `always` at an approval prompt, in memory only.

Never written to config. A run can widen what it is allowed to do for its
own lifetime, and can never widen what the user has persisted -- which is
also why Step 6 declined to build a TOML writer (S6.1).
"""

from __future__ import annotations

from rudra.permissions.rules import Rule, rule_matches


class SessionGrants:
    """Rules granted by the user during this process."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def add(self, rule: Rule) -> None:
        if rule not in self._rules:
            self._rules.append(rule)

    def matches(
        self, tool: str, absolute: tuple[str, ...], relative: tuple[str, ...]
    ) -> Rule | None:
        """The first granted rule matching this call, or None.

        Takes the same path spellings `PermissionEngine` hands to
        `rule_matches`, so a grant matches exactly what an equivalent
        `allow` entry would.
        """
        for rule in self._rules:
            if rule_matches(rule, tool, absolute, relative):
                return rule
        return None

    def __len__(self) -> int:
        return len(self._rules)


__all__ = ["SessionGrants"]
