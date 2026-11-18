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


def test_canonical_when_is_total():
    from rudra.memory.kg import _canonical_when

    assert _canonical_when("2026-10-02") == "2026-10-02"
    assert _canonical_when("") == ""
    assert _canonical_when("2026-10-02T14:30:00+02:00") == "2026-10-02T12:30:00Z"
    assert _canonical_when("2026-10-02T14:30:00.5") == "2026-10-02T14:30:00Z"
    assert _canonical_when("2026-10-02 garbage") == "2026-10-02"
    assert _canonical_when("garbage") == ""
