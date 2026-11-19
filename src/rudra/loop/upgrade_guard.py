"""Whether a task is a dependency upgrade a past run already found harmful.

Pure functions over strings and records. Imports nothing from Rudra -- the
rule loop/bounds.py and decomposition.py follow -- so its tests need no
graph, no model and no fact store. The knowledge-graph lookup that produces
`AdverseUpgrade` records lives elsewhere; this module only decides.

Detection is conservative on purpose. A missed upgrade costs nothing (the
work just proceeds unguarded); a false detection blocks unrelated work. So
anything vague -- no package, two packages, "everything" -- is None.

The block rule is deliberately simple: block iff the package matches and the
request is unversioned or names exactly the adverse version. Version ORDERING
is a non-goal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

UPGRADE_VERBS = frozenset({"upgrade", "update", "bump"})

# Stripped from each end of a word, never from inside it: "3.0." is `3.0`.
_EDGE_PUNCTUATION = ".,;:!?()[]{}\"'`"

_PACKAGE = re.compile(r"[a-z][a-z0-9._-]*")
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")

# Words that follow an upgrade verb without naming a single package.
_NOT_A_PACKAGE = frozenset(
    {
        "all",
        "any",
        "every",
        "everything",
        "the",
        "a",
        "an",
        "our",
        "my",
        "these",
        "those",
        "this",
        "that",
        "to",
        "deps",
        "dependencies",
        "dependency",
        "packages",
        "package",
        "libraries",
        "library",
        "requirements",
        "versions",
        "version",
        "it",
        "them",
        "readme",
        "docs",
        "documentation",
        "changelog",
        "license",
        "code",
        "tests",
        "test",
        "config",
        "comments",
    }
)

_JOINERS = frozenset({"and", "&", "+", "or"})

# What may follow an unversioned package for the task to still be an upgrade
# OF it: "upgrade flask to latest", "update flask in pyproject", "bump x from y".
_AFTER_PACKAGE = frozenset({"to", "in", "from", "latest"})


@dataclass(frozen=True)
class UpgradeIntent:
    package: str
    version: str | None


@dataclass(frozen=True)
class AdverseUpgrade:
    package: str
    version: str
    run_id: str
    reason: str
    when: str


@dataclass(frozen=True)
class GuardDecision:
    blocked: bool
    intent: UpgradeIntent | None
    evidence: tuple[AdverseUpgrade, ...]

    def explanation(self) -> str:
        """The block text: the change asked for, the record, how to override."""
        if not self.blocked or self.intent is None:
            return ""
        target = self.intent.package
        if self.intent.version is not None:
            target = f"{target} {self.intent.version}"
        lines = [f"BLOCKED: upgrading {target} was recorded as harmful in an earlier run."]
        for record in self.evidence:
            lines.append(
                f"  - {record.package} {record.version}: {record.reason} "
                f"(run {record.run_id}, {record.when})"
            )
        lines.append(
            "To proceed anyway, state explicitly that this upgrade is intended "
            "despite the record above; otherwise choose a different version."
        )
        return "\n".join(lines)


def _intent_in(text: str) -> UpgradeIntent | None:
    raw = text.lower().split()
    words = [word.strip(_EDGE_PUNCTUATION) for word in raw]
    for index, word in enumerate(words):
        if word not in UPGRADE_VERBS or index + 1 >= len(words):
            continue
        package = words[index + 1]
        if package in _NOT_A_PACKAGE or not _PACKAGE.fullmatch(package):
            continue
        # "upgrade flask, requests" / "upgrade flask and requests"
        if raw[index + 1].endswith(","):
            continue
        rest = words[index + 2 :]
        if rest and rest[0] in _JOINERS:
            continue
        if rest and rest[0] == "to":
            rest = rest[1:]
            if not rest:
                continue
        match = _VERSION.fullmatch(rest[0]) if rest else None
        if match is None and rest and rest[0] not in _AFTER_PACKAGE:
            # "update flask routes to ...": a noun follows, so this edits
            # code that uses flask rather than upgrading it.
            continue
        return UpgradeIntent(package=package, version=match.group(1) if match else None)
    return None


def detect_upgrade_intent(description: str, facts: Mapping[str, Any]) -> UpgradeIntent | None:
    """The single package upgrade a task asks for, or None.

    Reads the description first, then each fact's VALUE (never its key).
    """
    found = _intent_in(description)
    if found is not None:
        return found
    for fact in facts.values():
        value = getattr(fact, "value", None)
        if isinstance(value, str):
            found = _intent_in(value)
            if found is not None:
                return found
    return None


def evaluate_guard(
    intent: UpgradeIntent | None, adverse: Sequence[AdverseUpgrade]
) -> GuardDecision:
    """Block iff an adverse record names this package and this version (or any, if unversioned)."""
    if intent is None:
        return GuardDecision(blocked=False, intent=None, evidence=())
    hits = tuple(
        record
        for record in adverse
        if record.package == intent.package
        and (intent.version is None or intent.version == record.version)
    )
    return GuardDecision(blocked=bool(hits), intent=intent, evidence=hits)


__all__ = [
    "UPGRADE_VERBS",
    "AdverseUpgrade",
    "GuardDecision",
    "UpgradeIntent",
    "detect_upgrade_intent",
    "evaluate_guard",
]
