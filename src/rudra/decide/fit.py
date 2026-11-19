"""S3 fit-from-archive pipeline — builder half (Task 1).

Turns archived runs into ``(state, questions, targets)`` triples for the
questions ``LABEL-SOURCES.md`` marks archive-derivable (``complexity``,
``domain``). ``targets[qid]`` is a one-hot over the catalog ``criteria`` order —
the shape ``laya_mlx.calibrate.records_from_labeled`` consumes (it reads the gold
option as ``int(argmax(target[:k]))``). No laya import here: the dataset is pure
Python over the archive JSON, so it builds with the sidecar absent.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rudra.decide.catalog import TASK_TRIAGE

_log = logging.getLogger("rudra.decide")

# Only these two questions have a label derivable from today's archive
# (LABEL-SOURCES.md). Everything else ships zero-shot until S4-S6 instrument it.
_DERIVABLE = ("complexity", "domain")

# domain: a fact is "stack-ish" when its key names a stack/language/framework,
# mirroring scripts/decide_audit_archive.py so the audit and the builder agree.
_STACK_KEY_WORDS = ("stack", "language", "lang", "domain", "framework")

# complexity proxy thresholds (noisy but real; see the plan's Task 1).
_HARD_ATTEMPTS = 3
_TRIVIAL_ATTEMPTS = 1
_TRIVIAL_SECONDS = 60.0
_FINISHED = ("done", "blocked")


def one_hot(index: int, width: int) -> list[float]:
    vec = [0.0] * width
    vec[index] = 1.0
    return vec


def derivable_questions(question_set: dict) -> dict:
    """The subset of ``question_set`` with an archive-derivable label today."""
    return {qid: q for qid, q in question_set.items() if qid in _DERIVABLE}


def _complexity_level(task: dict) -> int | None:
    """0 trivial / 1 normal / 2 hard, or None when the task did not finish."""
    status = task.get("status")
    if status not in _FINISHED:
        return None
    attempts = task.get("attempts") or 0
    seconds = task.get("seconds") or 0.0
    if status == "blocked" or attempts >= _HARD_ATTEMPTS:
        return 2
    if attempts <= _TRIVIAL_ATTEMPTS and seconds <= _TRIVIAL_SECONDS:
        return 0
    return 1


def _fact_value(fact: object) -> str:
    if isinstance(fact, dict):
        return str(fact.get("value", ""))
    return str(fact)


def _domain_label(facts: dict, criteria_order: list[str]) -> str | None:
    """A PROFILE name, ``other``, or None when no stack-ish fact exists."""
    stack_text: str | None = None
    for key, fact in (facts or {}).items():
        if any(w in str(key).lower() for w in _STACK_KEY_WORDS):
            stack_text = _fact_value(fact).lower()
            break
    if stack_text is None:
        return None
    for name in criteria_order:
        if name == "other":
            continue
        if name in stack_text:
            return name
    return "other"


def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 - a malformed run is skipped, not fatal
        _log.warning("skipping unreadable archive file %s: %s", path, exc)
        return None


def build_dataset(runs_dir, question_set: dict) -> list[tuple[object, dict, dict]]:
    """Archive -> ``[(state, questions, targets)]`` for derivable questions.

    ``runs_dir`` holds ``<project>/<run>/`` dirs with ``ledger.json`` and
    ``facts.json``. A row is one finished task; a task with no label for any
    requested question is omitted. A malformed run is logged and skipped.
    """
    root = Path(runs_dir)
    wanted = derivable_questions(question_set)
    rows: list[tuple[object, dict, dict]] = []
    if not wanted:
        return rows

    domain_crit = list(TASK_TRIAGE["domain"]["criteria"].keys())
    complexity_width = len(TASK_TRIAGE["complexity"]["criteria"])

    for ledger_path in sorted(root.glob("*/*/ledger.json")):
        run = ledger_path.parent
        ledger = _load_json(ledger_path)
        if not isinstance(ledger, dict):
            continue
        tasks = ledger.get("tasks")
        if not isinstance(tasks, list):
            continue

        domain_label = None
        if "domain" in wanted:
            facts_doc = _load_json(run / "facts.json")
            facts = {}
            if isinstance(facts_doc, dict):
                facts = facts_doc.get("facts", facts_doc)
            domain_label = _domain_label(facts if isinstance(facts, dict) else {}, domain_crit)

        for task in tasks:
            if not isinstance(task, dict):
                continue
            targets: dict[str, list[float]] = {}
            questions: dict = {}

            if "complexity" in wanted:
                level = _complexity_level(task)
                if level is not None:
                    targets["complexity"] = one_hot(level, complexity_width)
                    questions["complexity"] = wanted["complexity"]

            if "domain" in wanted and domain_label is not None:
                targets["domain"] = one_hot(domain_crit.index(domain_label), len(domain_crit))
                questions["domain"] = wanted["domain"]

            if not targets:
                continue
            rows.append(({"task": str(task.get("description", ""))}, questions, targets))

    return rows
