"""ProjectKG: real round-trips against a temp-dir SQLite graph."""

from __future__ import annotations

from rudra.memory.kg import ProjectKG


def test_adverse_then_query_roundtrips(tmp_path):
    kg = ProjectKG(str(tmp_path))
    kg.record_adverse_upgrade(
        "Flask", "3.0", run_id="abc123", reason="broke 4 tests", when="2026-10-02"
    )
    hits = kg.adverse_upgrades("flask")
    assert [(h.package, h.version, h.run_id, h.reason) for h in hits] == [
        ("flask", "3.0", "abc123", "broke 4 tests")
    ]


def test_empty_graph_returns_no_adverse(tmp_path):
    assert ProjectKG(str(tmp_path)).adverse_upgrades("flask") == []


def test_successful_upgrade_clears_the_adverse_record(tmp_path):
    kg = ProjectKG(str(tmp_path))
    kg.record_adverse_upgrade("flask", "3.0", run_id="r1", reason="x", when="2026-10-02")
    kg.record_successful_upgrade("flask", "3.0", when="2026-11-01")
    assert kg.adverse_upgrades("flask") == []


def test_a_broken_graph_degrades_to_empty(tmp_path):
    (tmp_path / "knowledge_graph.sqlite3").write_text("not a database")
    assert ProjectKG(str(tmp_path)).adverse_upgrades("flask") == []


def test_time_bearing_when_writes_a_real_edge(tmp_path):
    from datetime import datetime

    for i, when in enumerate(["2026-10-02T14:30:00+00:00", datetime.now().isoformat()]):
        kg = ProjectKG(str(tmp_path / str(i)))
        assert kg.record_adverse_upgrade("flask", "3.0", run_id="r", reason="x", when=when)
        assert [h.version for h in kg.adverse_upgrades("flask")] == ["3.0"]


def test_successful_upgrade_closes_an_open_predecessor_at_one_instant(tmp_path):
    # Native supersede must CLOSE an open same-version edge at the heal instant
    # and open the success at that same instant -- not leave two open edges
    # (what a plain add_triple would do).
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = ProjectKG(str(tmp_path))
    with KnowledgeGraph(db_path=kg.graph_path) as g:
        g.add_triple(
            "flask", "upgraded_to", "3.0", valid_from="2026-10-02T00:00:00Z", confidence=0.6
        )
    assert kg.record_successful_upgrade("flask", "3.0", when="2026-11-01T09:00:00Z")
    with KnowledgeGraph(db_path=kg.graph_path) as g:
        rows = [
            r
            for r in g.query_entity("flask", direction="outgoing")
            if r["predicate"] == "upgraded_to"
        ]
    current = [r for r in rows if r["current"]]
    closed = [r for r in rows if not r["current"]]
    assert len(current) == 1 and current[0]["confidence"] == 1.0
    assert current[0]["valid_from"] == "2026-11-01T09:00:00Z"
    assert len(closed) == 1 and closed[0]["valid_to"] == "2026-11-01T09:00:00Z"


def test_adverse_upgrade_is_a_single_closed_edge(tmp_path):
    # record_adverse_upgrade writes ONE closed [T,T] edge (not-current), via a
    # single add_triple(valid_from, valid_to) rather than add_triple+invalidate.
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = ProjectKG(str(tmp_path))
    kg.record_adverse_upgrade("flask", "3.0", run_id="r", reason="x", when="2026-10-02T12:00:00Z")
    with KnowledgeGraph(db_path=kg.graph_path) as g:
        rows = [
            r
            for r in g.query_entity("flask", direction="outgoing")
            if r["predicate"] == "upgraded_to"
        ]
    assert len(rows) == 1
    assert rows[0]["current"] is False
    assert rows[0]["valid_from"] == "2026-10-02T12:00:00Z"
    assert rows[0]["valid_to"] == "2026-10-02T12:00:00Z"


def test_canonical_when_is_total():
    from rudra.memory.kg import _canonical_when

    assert _canonical_when("2026-10-02") == "2026-10-02"
    assert _canonical_when("") == ""
    assert _canonical_when("2026-10-02T14:30:00+02:00") == "2026-10-02T12:30:00Z"
    assert _canonical_when("2026-10-02T14:30:00.5") == "2026-10-02T14:30:00Z"
    assert _canonical_when("2026-10-02 garbage") == "2026-10-02"
    assert _canonical_when("garbage") == ""
