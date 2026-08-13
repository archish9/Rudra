"""Whatever this project's agents have established, and why.

Replaces the fixed 4-field ProjectContext (TODO.md §0.5). Keys are not
enumerated anywhere in code -- validation covers types and size, never
names, because the moment code lists the legal keys the questionnaire is
back. A fact carries its rationale because C6.7 must persist decisions
AND their reasoning, and a second store for the reasoning would let the
two drift.

Pure data plus one file. Imports nothing from Rudra, so its tests need no
model, no backend and no gate -- the rule loop/ledger.py follows.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Lowercase, because a store that distinguishes `Language` from `language`
# holds the same fact twice and shows the model both.
KEY_PATTERN = re.compile(r"^[a-z0-9_.-]{1,64}$")

# How the fact came to be believed. `asked` is the user's word, `inferred`
# is the model's reading of the request, `detected` is read off the
# project. The distinction matters to a human reading facts.json later.
VALID_SOURCES = ("asked", "inferred", "detected")

MAX_TEXT = 512
MAX_FACTS = 100


class FactRejected(ValueError):
    """A fact failed validation.

    The message is written to be shown to a model verbatim: it says what
    was wrong and what would be acceptable, because the next thing that
    happens is the model trying again.
    """


@dataclass(frozen=True)
class Fact:
    """One thing believed about this project, and why."""

    value: str
    why: str
    source: str


def _check_text(name: str, text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        msg = f"{name} must be a non-empty string, got {text!r}."
        raise FactRejected(msg)
    stripped = text.strip()
    if len(stripped) > MAX_TEXT:
        msg = f"{name} must be at most {MAX_TEXT} characters, got {len(stripped)}."
        raise FactRejected(msg)
    return stripped


@dataclass
class FactStore:
    """Every fact this project has established, in the order established.

    One store per run, shared by reference: the planner's tools mutate it
    and every later subagent build renders it into a prompt. A copy would
    leave the coder reading a stale store -- the failure the shared Ledger
    already exists to avoid (main_agent.py:412-414).
    """

    facts: dict[str, Fact] = field(default_factory=dict)

    def record(self, key: str, value: str, why: str, source: str) -> Fact:
        """Validate and store one fact.

        Raises FactRejected and changes nothing on failure.
        """
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            msg = (
                f"'{key}' is not a valid key: use 1-64 characters from "
                "a-z, 0-9, underscore, dot and hyphen."
            )
            raise FactRejected(msg)
        if source not in VALID_SOURCES:
            msg = f"source must be one of {', '.join(VALID_SOURCES)}, got {source!r}."
            raise FactRejected(msg)
        clean_value = _check_text("value", value)
        clean_why = _check_text("why", why)
        # An existing key is a correction, not a new fact, so it is never
        # blocked by the cap -- a full store must still be fixable.
        if key not in self.facts and len(self.facts) >= MAX_FACTS:
            msg = f"the fact store is full ({MAX_FACTS} facts); nothing was recorded."
            raise FactRejected(msg)

        fact = Fact(value=clean_value, why=clean_why, source=source)
        self.facts[key] = fact
        return fact

    def get(self, key: str) -> Fact | None:
        return self.facts.get(key)

    def items(self) -> list[tuple[str, Fact]]:
        """Every fact in insertion order. Overwriting a key keeps its place."""
        return list(self.facts.items())

    def save(self, path: Path) -> None:
        """Write atomically: a private temp file, then os.replace.

        A crash mid-write must leave the previous file readable rather
        than a truncated one -- the guarantee Ledger.save gives
        (loop/ledger.py:95-107).

        The temp name is unique per writer, not a fixed `.tmp` (A1.71).
        deepagents runs one turn's tool calls concurrently, and the model
        emits one record_fact per fact, so two saves genuinely overlap: on
        a shared name the first os.replace consumes the file and the
        second raises FileNotFoundError, killing the run. Unlinking on
        failure keeps a crashed write from leaving debris beside the file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"facts": {key: asdict(fact) for key, fact in self.facts.items()}}
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path) -> FactStore:
        """Read a store, or an empty one when the file is absent or unreadable.

        Entries that fail validation are dropped rather than raising: this
        file is durable and hand-editable, and one bad line must not stop
        a run that has nothing to do with it.
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()

        store = cls()
        entries = payload.get("facts", {}) if isinstance(payload, dict) else {}
        if not isinstance(entries, dict):
            return store
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                store.record(
                    key,
                    entry.get("value"),
                    entry.get("why"),
                    entry.get("source"),
                )
            except FactRejected:
                continue
        return store


__all__ = [
    "KEY_PATTERN",
    "MAX_FACTS",
    "MAX_TEXT",
    "VALID_SOURCES",
    "Fact",
    "FactRejected",
    "FactStore",
]
