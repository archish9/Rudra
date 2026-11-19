"""S3 Task 1 — archive -> labelled rows builder.

Weight-free: no laya, no checkpoint. Builds ``(state, questions, targets)``
triples from archived runs for the questions ``LABEL-SOURCES.md`` marks
derivable (``complexity``, ``domain``). ``targets[qid]`` is a one-hot over the
catalog ``criteria`` order, the shape ``records_from_labeled`` consumes.
"""

from __future__ import annotations

import json
from pathlib import Path

from rudra.decide.catalog import TASK_TRIAGE
from rudra.decide.fit import build_dataset, derivable_questions, one_hot


def _write_run(
    runs_dir: Path,
    name: str,
    tasks: list[dict],
    facts: dict | None = None,
) -> None:
    run = runs_dir / "proj" / name
    run.mkdir(parents=True)
    (run / "ledger.json").write_text(json.dumps({"request": "r", "saved_at": "t", "tasks": tasks}))
    (run / "facts.json").write_text(json.dumps({"facts": facts or {}}))


def test_one_hot_puts_mass_at_index():
    assert one_hot(0, 3) == [1.0, 0.0, 0.0]
    assert one_hot(2, 3) == [0.0, 0.0, 1.0]
    assert one_hot(1, 2) == [0.0, 1.0]


def test_derivable_questions_keeps_only_archive_backed_qids():
    out = derivable_questions(TASK_TRIAGE)
    assert set(out) == {"complexity", "domain"}
    # needs_tests is NOT archive-derivable (LABEL-SOURCES) -> excluded.
    assert "needs_tests" not in out


def test_build_dataset_extracts_state_and_onehot_targets_for_derivable_questions(tmp_path):
    _write_run(
        tmp_path,
        "run-a",
        tasks=[
            {
                "id": 1,
                "description": "add a REST endpoint",
                "status": "done",
                "attempts": 1,
                "seconds": 20.0,
            },
        ],
        facts={"stack": {"value": "python project", "why": "w", "source": "inferred"}},
    )
    rows = build_dataset(tmp_path, TASK_TRIAGE)
    assert len(rows) == 1
    state, questions, targets = rows[0]
    assert state == {"task": "add a REST endpoint"}
    # complexity: done, attempts<=1, seconds<=60 -> level 0 (trivial) one-hot width 3
    assert targets["complexity"] == [1.0, 0.0, 0.0]
    # domain: "python project" -> python is index 0 of _domain_criteria order
    crit = list(TASK_TRIAGE["domain"]["criteria"].keys())
    assert targets["domain"] == one_hot(crit.index("python"), len(crit))
    # questions carried are exactly the labelled ones
    assert set(questions) == {"complexity", "domain"}


def test_complexity_levels_follow_the_proxy(tmp_path):
    _write_run(
        tmp_path,
        "run-b",
        tasks=[
            {
                "id": 1,
                "description": "hard",
                "status": "blocked",
                "attempts": 2,
                "seconds": 5.0,
            },  # blocked -> 2
            {
                "id": 2,
                "description": "many tries",
                "status": "done",
                "attempts": 3,
                "seconds": 5.0,
            },  # attempts>=3 -> 2
            {
                "id": 3,
                "description": "normal",
                "status": "done",
                "attempts": 2,
                "seconds": 300.0,
            },  # else -> 1
            {
                "id": 4,
                "description": "trivial",
                "status": "done",
                "attempts": 1,
                "seconds": 10.0,
            },  # <=1 & <=60 -> 0
        ],
        facts={},
    )
    rows = build_dataset(tmp_path, {"complexity": TASK_TRIAGE["complexity"]})
    levels = {state["task"]: targets["complexity"] for state, _q, targets in rows}
    assert levels["hard"] == one_hot(2, 3)
    assert levels["many tries"] == one_hot(2, 3)
    assert levels["normal"] == one_hot(1, 3)
    assert levels["trivial"] == one_hot(0, 3)


def test_pending_and_dropped_tasks_have_no_complexity_label(tmp_path):
    _write_run(
        tmp_path,
        "run-c",
        tasks=[
            {
                "id": 1,
                "description": "never ran",
                "status": "pending",
                "attempts": 0,
                "seconds": 0.0,
            },
            {"id": 2, "description": "cut", "status": "dropped", "attempts": 0, "seconds": 0.0},
        ],
        facts={},
    )
    rows = build_dataset(tmp_path, {"complexity": TASK_TRIAGE["complexity"]})
    assert rows == []  # no finished task -> no label -> no row


def test_domain_absent_stack_fact_skips_domain_but_keeps_complexity(tmp_path):
    _write_run(
        tmp_path,
        "run-d",
        tasks=[
            {"id": 1, "description": "x", "status": "done", "attempts": 1, "seconds": 10.0},
        ],
        facts={"application_type": {"value": "a todo app", "why": "w", "source": "x"}},
    )
    rows = build_dataset(tmp_path, TASK_TRIAGE)
    assert len(rows) == 1
    _state, questions, targets = rows[0]
    assert "domain" not in targets  # no stack-ish fact -> no wrong 'other'
    assert "complexity" in targets


def test_domain_unmapped_stack_value_is_other(tmp_path):
    _write_run(
        tmp_path,
        "run-e",
        tasks=[
            {"id": 1, "description": "x", "status": "done", "attempts": 1, "seconds": 10.0},
        ],
        facts={"language": {"value": "COBOL", "why": "w", "source": "x"}},
    )
    rows = build_dataset(tmp_path, {"domain": TASK_TRIAGE["domain"]})
    crit = list(TASK_TRIAGE["domain"]["criteria"].keys())
    _state, _q, targets = rows[0]
    assert targets["domain"] == one_hot(crit.index("other"), len(crit))


def test_onehot_order_matches_catalog_criteria_order(tmp_path):
    # react must land at its real index, not be confused with 'other' or python.
    _write_run(
        tmp_path,
        "run-f",
        tasks=[{"id": 1, "description": "x", "status": "done", "attempts": 1, "seconds": 10.0}],
        facts={"framework": {"value": "a React SPA", "why": "w", "source": "x"}},
    )
    rows = build_dataset(tmp_path, {"domain": TASK_TRIAGE["domain"]})
    crit = list(TASK_TRIAGE["domain"]["criteria"].keys())
    _state, _q, targets = rows[0]
    assert targets["domain"] == one_hot(crit.index("react"), len(crit))


def test_malformed_run_is_skipped_not_raised(tmp_path):
    good = tmp_path / "proj" / "good"
    good.mkdir(parents=True)
    (good / "ledger.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": 1, "description": "x", "status": "done", "attempts": 1, "seconds": 10.0}
                ]
            }
        )
    )
    (good / "facts.json").write_text(json.dumps({"facts": {}}))
    bad = tmp_path / "proj" / "bad"
    bad.mkdir(parents=True)
    (bad / "ledger.json").write_text("{not json")
    rows = build_dataset(tmp_path, {"complexity": TASK_TRIAGE["complexity"]})
    assert len(rows) == 1  # bad run skipped, good run kept


def test_empty_archive_returns_no_rows(tmp_path):
    assert build_dataset(tmp_path, TASK_TRIAGE) == []
