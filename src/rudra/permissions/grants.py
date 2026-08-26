"""Session grants: `always` and `auto-accept` at an approval prompt.

In memory only, never written to config. A session can widen what it is
allowed to do for its own lifetime, and can never widen what the user has
persisted -- which is also why Step 6 declined to build a TOML writer
(S6.1).

That lifetime is the SESSION, not the run (OPEN-30). `build_gate` accepts
an existing instance, and `_repl_session` builds one before its loop, so a
grant taken in the first turn is still granted in the fourth. Before that
the object was rebuilt inside every `create_main_agent` call and `always`
meant "always, until you press enter again".
"""

from __future__ import annotations

from rudra.permissions.rules import Rule, rule_matches


class SessionGrants:
    """Rules granted by the user during this process."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self.approve_all = False
        """`auto-accept`: every gated call is consented to for this session.

        Deliberately NOT a rule in `_rules`. A rule has to match a path or a
        command, and this is the absence of a question rather than a very
        wide answer -- `Rule(tool, "*")` per tool would have to enumerate
        the tools and would be matched by `rule_matches`, which is where
        `permissive=` and command splitting live (CR-B1). The engine reads
        this flag directly instead.
        """

    def grant_all(self) -> None:
        """Stop asking for the rest of this session.

        One way only. Nothing in the prompt turns it back off, because a
        run that has already written files cannot un-write them and a
        false sense of "I turned it off" is worse than restarting Rudra.
        """
        self.approve_all = True

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
            # permissive: a grant is consent, and consent to `pytest` is not
            # consent to whatever a `;` chains after it (CR-B1).
            if rule_matches(rule, tool, absolute, relative, permissive=True):
                return rule
        return None

    def __len__(self) -> int:
        return len(self._rules)


__all__ = ["SessionGrants"]
