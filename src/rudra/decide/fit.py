"""S3 fit-from-archive pipeline — builder half (Task 1).

Turns archived runs into ``(state, questions, targets)`` triples for the
questions ``LABEL-SOURCES.md`` marks archive-derivable (``complexity``,
``domain``). ``targets[qid]`` is a one-hot over the catalog ``criteria`` order —
the shape ``laya_mlx.calibrate.records_from_labeled`` consumes (it reads the gold
option as ``int(argmax(target[:k]))``). No laya import here: the dataset is pure
Python over the archive JSON, so it builds with the sidecar absent.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rudra.decide.catalog import TASK_TRIAGE

_log = logging.getLogger("rudra.decide")

CALIBRATION_FILE = "calibration.json"
META_FILE = "meta.json"


def resolve_calibration_dir(cfg) -> Path:
    """The calibration directory: ``[decide] calibration_dir`` if set, else the
    global default ``$XDG_STATE_HOME/rudra/calibration``. Both ``rudra decide
    fit`` and ``DecisionEngine.build`` resolve through here so they agree."""
    explicit = getattr(getattr(cfg, "decide", None), "calibration_dir", None)
    if explicit:
        return Path(explicit)
    from rudra.state.archive import calibration_home  # noqa: PLC0415 - avoid import cycle

    return calibration_home()


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


# --- Task 2: fit + calibrate + honest floor ---------------------------------


@dataclass(frozen=True)
class QFit:
    n_rows: int
    served: bool
    note: str


@dataclass(frozen=True)
class FitReport:
    per_question: dict[str, QFit]
    calibration_written: bool


def _lazy_calibrate():
    """Import laya's calibrate module. Lazy: laya is a sidecar, absent in core."""
    import laya_mlx.calibrate as calibrate  # noqa: PLC0415 - guarded optional import

    return calibrate


def _bucket_serves(qtype: str, thresholds: dict) -> bool:
    """A question is served iff a fitted abstention bucket covers its qtype.

    Thresholds are keyed by option-count bucket (``"score:1-3"``, ``"noul"``);
    the key starts with the qtype name, so a family match is enough and avoids
    reproducing laya's exact bucket ranges here.
    """
    return any(k == qtype or str(k).startswith(f"{qtype}:") for k in thresholds)


def _row_counts(rows, question_set: dict) -> dict[str, int]:
    counts = {qid: 0 for qid in question_set}
    for _state, _questions, targets in rows:
        for qid in targets:
            if qid in counts:
                counts[qid] += 1
    return counts


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".meta.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fit_calibration(
    agent,
    rows,
    question_set: dict,
    out_dir,
    *,
    backend: str,
    model_id: str,
    calibrate=None,
    now: str | None = None,
) -> FitReport:
    """Fit laya calibration from labelled ``rows`` and save it under ``out_dir``.

    Writes ``calibration.json`` (laya's temperatures, via ``save_calibration``)
    and ``meta.json`` (backend, model id, fitted_at, row counts, and the
    abstention-threshold map — which ``save_calibration`` does NOT persist)
    **only when at least one question is served**. laya's own floor decides:
    ``fit_abstention_thresholds`` returns ``{}`` when no option-count bucket
    reached its sample minimum, and an unserved question stays zero-shot. Never
    fabricates a threshold. ``calibrate`` is injectable so tests stay weight-free.
    """
    out = Path(out_dir)
    counts = _row_counts(rows, question_set)

    if not rows:
        per_q = {
            qid: QFit(n_rows=0, served=False, note="no label in archive") for qid in question_set
        }
        _log_report(per_q, backend)
        return FitReport(per_question=per_q, calibration_written=False)

    # The fitters can fail for a reason that is not "bad data": laya-mlx's
    # fit_temperatures does `import torch`, absent in the mlx-only install, so
    # once a qtype clears its sample floor it raises. A fit that cannot run is
    # reported and the questions stay zero-shot -- never a crash, never a
    # fabricated gate (CLAUDE.md 8a: bookkeeping may not end a run).
    cal = calibrate or _lazy_calibrate()
    fit_error: str | None = None
    thresholds: dict = {}
    try:
        records = cal.records_from_labeled(agent, rows)
        agent.fit_temperatures(records)
        thresholds = dict(
            cal.fit_abstention_thresholds(records, agent.temperature, agent.temperature_by_options)
            or {}
        )
    except Exception as exc:  # noqa: BLE001 - a failed fit degrades, never raises
        fit_error = f"fit unavailable: {type(exc).__name__}: {exc}"
        _log.warning("decide fit degraded to zero-shot: %s", fit_error)

    per_q: dict[str, QFit] = {}
    for qid, q in question_set.items():
        n = counts.get(qid, 0)
        qtype = q.get("type", "?") if isinstance(q, dict) else "?"
        if n == 0:
            per_q[qid] = QFit(n, False, "no label in archive")
        elif fit_error is not None:
            per_q[qid] = QFit(n, False, fit_error)
        elif _bucket_serves(qtype, thresholds):
            per_q[qid] = QFit(n, True, "served")
        else:
            per_q[qid] = QFit(n, False, "declined: too few rows for a gate")

    served_any = any(qf.served for qf in per_q.values())
    if served_any:
        agent.save_calibration(str(out / CALIBRATION_FILE))
        _atomic_write_json(
            out / META_FILE,
            {
                "backend": backend,
                "model_id": model_id,
                "fitted_at": now or datetime.now(timezone.utc).isoformat(),
                "row_counts": counts,
                "thresholds": thresholds,
            },
        )

    _log_report(per_q, backend)
    return FitReport(per_question=per_q, calibration_written=served_any)


def _log_report(per_q: dict[str, QFit], backend: str) -> None:
    try:
        _log.info(
            "decide_fit",
            extra={
                "event": {
                    "kind": "decide_fit",
                    "backend": backend,
                    "per_question": {
                        qid: {"n_rows": qf.n_rows, "served": qf.served, "note": qf.note}
                        for qid, qf in per_q.items()
                    },
                }
            },
        )
    except Exception:  # noqa: BLE001 - a diagnostic must never fail a fit
        pass


# --- Run-end auto-fit (OPEN-182) --------------------------------------------
#
# Calibration refreshes itself from the archive at run end WHERE IT CAN. The
# fit needs torch (fit-time only; runtime loads the JSON torch-free): the ONNX
# build bundles torch, so auto-fit calibrates with no user action; the MLX build
# ships none, so the hook is a logged no-op and that machine stays zero-shot
# until the user installs torch and runs `rudra decide fit` once (see
# Documentation/16-laya.md). The hook never raises (CLAUDE.md 8a).

# Only refit once this many archived runs have landed since the last fit, so an
# active machine does not re-fit the whole archive on every single run end.
AUTO_FIT_MIN_NEW_RUNS = 5


def _torch_available() -> bool:
    """Cheap probe for torch (the fit-time dependency) without importing it.

    The whole MLX-vs-ONNX split turns on this: ``find_spec`` returns None on an
    MLX box (no torch) so the hook short-circuits before building any dataset.
    """
    return importlib.util.find_spec("torch") is not None


def _runs_newer_than(runs_dir, since_iso: str) -> int:
    """Count archived runs whose ``ledger.json`` was written after ``since_iso``."""
    try:
        since = datetime.fromisoformat(since_iso).timestamp()
    except Exception:  # noqa: BLE001 - an unparseable stamp means "fit now"
        return AUTO_FIT_MIN_NEW_RUNS
    count = 0
    for ledger_path in Path(runs_dir).glob("*/*/ledger.json"):
        try:
            if ledger_path.stat().st_mtime > since:
                count += 1
        except OSError:
            continue
    return count


def should_auto_fit(cfg, runs_dir, out_dir) -> bool:
    """Whether the run-end hook should fit now. Pure read; never raises.

    True iff ``[decide] auto_fit`` is on, torch is importable (so the fit can
    actually run), and either nothing has been fitted yet or at least
    ``AUTO_FIT_MIN_NEW_RUNS`` archived runs are newer than the last fit.
    """
    decide = getattr(cfg, "decide", None)
    if not getattr(decide, "auto_fit", False):
        return False
    if not _torch_available():
        return False
    meta_path = Path(out_dir) / META_FILE
    if not meta_path.is_file():
        return True
    try:
        fitted_at = json.loads(meta_path.read_text()).get("fitted_at")
    except Exception:  # noqa: BLE001 - a bad meta reads as "never fitted"
        return True
    if not fitted_at:
        return True
    return _runs_newer_than(runs_dir, fitted_at) >= AUTO_FIT_MIN_NEW_RUNS


def run_fit(cfg, runs_dir=None):
    """The one fit path, shared by ``rudra decide fit`` and the auto-fit hook.

    Selects the backend, builds the dataset from the archive, fits, and lets the
    backend write ``calibration.json`` + ``meta.json``. Returns the ``FitReport``,
    or None when no backend is available (laya absent / ``backend = "off"``).
    """
    from rudra.decide.backends import select_backend  # noqa: PLC0415 - avoid import cycle
    from rudra.state.archive import archive_home  # noqa: PLC0415 - avoid import cycle

    backend = select_backend(cfg)
    if backend is None:
        return None
    rd = runs_dir if runs_dir is not None else archive_home()
    rows = build_dataset(rd, TASK_TRIAGE)
    out_dir = resolve_calibration_dir(cfg)
    return backend.fit(rows, TASK_TRIAGE, out_dir)


def maybe_auto_fit(cfg):
    """Run-end hook: fit when ``should_auto_fit`` allows it, else a no-op.

    Returns the ``FitReport`` when it fit, else None. Never raises -- a failed or
    skipped calibration must not end a run (CLAUDE.md 8a). The ``decide_fit``
    diagnostic is logged by ``fit_calibration`` itself.
    """
    try:
        from rudra.state.archive import archive_home  # noqa: PLC0415 - avoid import cycle

        runs_dir = archive_home()
        out_dir = resolve_calibration_dir(cfg)
        if not should_auto_fit(cfg, runs_dir, out_dir):
            return None
        return run_fit(cfg, runs_dir)
    except Exception as exc:  # noqa: BLE001 - auto-fit may never end a run
        _log.warning("auto-fit skipped: %s", exc)
        return None
