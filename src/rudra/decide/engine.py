"""Decision type, the act gate, and the engine."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from rudra.decide.backends import DecideBackend, select_backend
from rudra.decide.fit import CALIBRATION_FILE, META_FILE, resolve_calibration_dir


@dataclass(frozen=True)
class Decision:
    question_id: str
    kind: str  # "choice" | "score" | "noul"
    value: object  # label (choice) | float level (score) | P(true) (noul)
    probabilities: dict[str, float] | None
    prediction_set: list[str] | None
    abstain: bool
    route: str  # "auto" | "review"
    confidence: float
    backend: str  # "mlx" | "onnx" | "unavailable"


def should_act(d: Decision) -> bool:
    return d.backend != "unavailable" and not d.abstain and d.route == "auto"


_log = logging.getLogger("rudra.decide")


def load_matching_calibration(backend, cfg) -> bool:
    """Load a saved calibration onto ``backend`` iff it is fresh; else zero-shot.

    STRICTER than laya's own ``load_calibration`` (which warns on a checkpoint
    mismatch and loads anyway): the sidecar ``meta.json`` must record the same
    ``backend`` name and ``model_id`` as the selected backend, or the file is
    ignored. Absent, mismatched, or corrupt meta all leave the engine zero-shot.
    Never raises.
    """
    try:
        dir_path = resolve_calibration_dir(cfg)
        meta_path = Path(dir_path) / META_FILE
        cal_path = Path(dir_path) / CALIBRATION_FILE
        if not meta_path.is_file() or not cal_path.is_file():
            return False
        meta = json.loads(meta_path.read_text())
        if meta.get("backend") != backend.name or meta.get("model_id") != backend.model_id:
            _log.info(
                "calibration ignored (stale): meta %s/%s vs backend %s/%s",
                meta.get("backend"),
                meta.get("model_id"),
                backend.name,
                backend.model_id,
            )
            return False
        backend.load_calibration(str(cal_path))
        return True
    except Exception as exc:  # noqa: BLE001 - a bad calibration must never fail build
        _log.warning("calibration load skipped: %s", exc)
        return False


# S1 has no per-question threshold; S2 adds them.
DEFAULT_MIN_CONFIDENCE = 0.75
_KNOWN_BACKENDS = {"auto", "mlx", "onnx", "off"}


def _qdict(q: object) -> dict:
    """A question value that is not a dict is treated as empty, never raised on."""
    return q if isinstance(q, dict) else {}


def _unavailable(questions: dict) -> dict[str, Decision]:
    return {
        qid: Decision(
            question_id=qid,
            kind=_qdict(q).get("type", "?"),
            value=None,
            probabilities=None,
            prediction_set=None,
            abstain=True,
            route="review",
            confidence=0.0,
            backend="unavailable",
        )
        for qid, q in questions.items()
    }


class DecisionEngine:
    """Backend-agnostic front for laya. Never raises from build or decide."""

    def __init__(self, backend: DecideBackend | None = None) -> None:
        self._backend = backend
        self._threshold = DEFAULT_MIN_CONFIDENCE

    @classmethod
    def build(cls, cfg) -> DecisionEngine:
        try:
            settings = getattr(cfg, "decide", None)
            if not getattr(settings, "enabled", True):
                return cls()
            choice = getattr(settings, "backend", "auto")
            if choice not in _KNOWN_BACKENDS:
                _log.warning("unknown [decide] backend %r; decision layer stays off", choice)
            backend = select_backend(cfg)
            if backend is not None:
                load_matching_calibration(backend, cfg)
            return cls(backend)
        except Exception as exc:  # noqa: BLE001 - build must never fail a run
            _log.warning("decision backend selection failed: %s", exc)
            return cls()

    def available(self) -> bool:
        return self._backend is not None

    def decide(self, state, questions: dict) -> dict[str, Decision]:
        """Decide every question. Synchronous: laya's forward pass blocks.

        Async-loop callers must offload via ``asyncio.to_thread(engine.decide, ...)``.
        """
        if self._backend is None:
            return _unavailable(questions)
        try:
            result = self._backend.predict(state, questions, self._threshold)
        except Exception as exc:  # noqa: BLE001 - one bad call must not end the run
            _log.warning("decision call failed (%s): %s", type(exc).__name__, exc)
            return _unavailable(questions)
        name = self._backend.name
        return {
            qid: to_decision(qid, _qdict(q).get("type", "?"), (result or {}).get(qid) or {}, name)
            for qid, q in questions.items()
        }


from rudra.decide.normalize import to_decision  # noqa: E402 - normalize imports Decision
