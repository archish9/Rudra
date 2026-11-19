"""Project knowledge graph: which dependency upgrades went badly.

A thin wrapper over mempalace's temporal knowledge graph (SQLite). Like
store.py, mempalace is imported lazily inside each method, and every public
method degrades to its default rather than failing a task.

An adverse upgrade is an `upgraded_to` edge that was opened and immediately
closed (`valid_to` set), so it reads as "not current". A successful upgrade
leaves the edge open. The run id and reason ride in `source_closet`, the only
provenance field `query_entity` returns.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from rudra.loop.upgrade_guard import AdverseUpgrade
from rudra.memory.degrade import degrades

UPGRADED_TO = "upgraded_to"
_SEP = "::"
_ADVERSE_CONFIDENCE = 0.6


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _canonical_when(when: str) -> str:
    """mempalace accepts only YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ; anything
    else is rejected and, behind @degrades, silently dropped. Never raises."""
    if not when or _DATE.fullmatch(when):
        return when
    try:
        parsed = datetime.fromisoformat(when)
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, OverflowError):
        head = str(when)[:10]
        return head if _DATE.fullmatch(head) else ""


class ProjectKG:
    def __init__(self, palace_path: str) -> None:
        self.palace_path = palace_path
        self.graph_path = str(Path(palace_path) / "knowledge_graph.sqlite3")

    @degrades(default=False)
    def record_adverse_upgrade(
        self, package: str, version: str, *, run_id: str, reason: str, when: str
    ) -> bool:
        from mempalace.knowledge_graph import KnowledgeGraph

        Path(self.palace_path).mkdir(parents=True, exist_ok=True)
        name = package.lower()
        when = _canonical_when(when)
        with KnowledgeGraph(db_path=self.graph_path) as kg:
            kg.add_triple(
                name,
                UPGRADED_TO,
                version,
                valid_from=when,
                confidence=_ADVERSE_CONFIDENCE,
                source_closet=f"{run_id}{_SEP}{reason[:200]}",
            )
            kg.invalidate(name, UPGRADED_TO, version, ended=when)
        return True

    @degrades(default=False)
    def record_successful_upgrade(self, package: str, version: str, *, when: str) -> bool:
        from mempalace.knowledge_graph import KnowledgeGraph

        Path(self.palace_path).mkdir(parents=True, exist_ok=True)
        name = package.lower()
        when = _canonical_when(when)
        with KnowledgeGraph(db_path=self.graph_path) as kg:
            # Re-opening the edge is what clears the adverse record: the
            # closed edge stays as history, a current one now exists.
            kg.add_triple(name, UPGRADED_TO, version, valid_from=when, confidence=1.0)
        return True

    @degrades(default=[])
    def adverse_upgrades(self, package: str) -> list[AdverseUpgrade]:
        from mempalace.knowledge_graph import KnowledgeGraph

        name = package.lower()
        with KnowledgeGraph(db_path=self.graph_path) as kg:
            rows = kg.query_entity(name, direction="outgoing")
        current = {r["object"] for r in rows if r["predicate"] == UPGRADED_TO and r["current"]}
        found: list[AdverseUpgrade] = []
        for row in rows:
            if row["predicate"] != UPGRADED_TO or row["current"] or row["object"] in current:
                continue
            run_id, _, reason = (row.get("source_closet") or "").partition(_SEP)
            found.append(
                AdverseUpgrade(
                    package=name,
                    version=row["object"],
                    run_id=run_id,
                    reason=reason,
                    when=row["valid_to"] or row["valid_from"] or "",
                )
            )
        return found
