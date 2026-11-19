"""Capability-based backend selection for the decision layer.

Order is MLX, then ONNX, else None. Selection asks what can be imported and
loaded, never which OS this is. Every laya import lives inside the function
that needs it, so ``import rudra.decide`` works without laya installed.

``predict`` is synchronous and may block on a model forward pass; callers
decide about threading.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("rudra.decide.backends")

DEFAULT_MLX_REPO = "convaiinnovations/laya"


@runtime_checkable
class DecideBackend(Protocol):
    name: str
    model_id: str

    def predict(self, state: Any, questions: dict, min_confidence: float) -> dict: ...

    def load_calibration(self, path: str) -> None: ...

    def fit(self, rows, question_set: dict, out_dir): ...


class _AgentBackend:
    """Holds a loaded laya agent and delegates ``predict`` to it."""

    def __init__(self, name: str, agent: Any, model_id: str = "") -> None:
        self.name = name
        self._agent = agent
        self.model_id = model_id

    def load_calibration(self, path: str) -> None:
        """Install a saved calibration onto the agent (temperatures/binning)."""
        self._agent.load_calibration(path)

    def fit(self, rows, question_set: dict, out_dir):
        """Fit calibration from labelled ``rows`` and save under ``out_dir``.

        Keeps the raw laya agent encapsulated: ``fit_calibration`` needs the
        agent itself (``fit_temperatures``/``save_calibration``), which only
        this wrapper holds.
        """
        from rudra.decide.fit import fit_calibration  # noqa: PLC0415 - avoid import cycle

        return fit_calibration(
            self._agent, rows, question_set, out_dir, backend=self.name, model_id=self.model_id
        )

    def predict(self, state: Any, questions: dict, min_confidence: float) -> dict:
        """Gated prediction: ``{qid: answer}`` with ``abstention`` populated.

        Plain ``agent.predict`` omits the ``abstention`` key, which the
        normalizer reads as abstain; only the gated ``decide`` carries it.
        """
        if self.name == "mlx":
            import laya_mlx

            result = laya_mlx.decide(
                self._agent,
                state,
                questions=questions,
                return_details=True,
                min_confidence=min_confidence,
            )
            return result.values
        # TODO(S3): onnx gated call is unverified on this machine
        import laya

        return laya.decide(
            self._agent,
            state,
            questions=questions,
            return_details=True,
            min_confidence=min_confidence,
        ).values


def _decide_setting(cfg: Any, field: str, default: Any) -> Any:
    # cfg.decide does not exist until the config task lands; read defensively.
    return getattr(getattr(cfg, "decide", None), field, default)


def _load_mlx(cfg: Any) -> Any:
    import laya_mlx  # guarded: laya is an optional extra

    repo = _decide_setting(cfg, "mlx_repo", DEFAULT_MLX_REPO)
    return laya_mlx.load(repo)


def _load_onnx(cfg: Any) -> Any:
    # TODO(S3): confirm exact onnx construction + predict call
    import laya

    return laya.onnx_agent()


def _try_mlx(cfg: Any) -> DecideBackend | None:
    try:
        return _AgentBackend(
            "mlx", _load_mlx(cfg), _decide_setting(cfg, "mlx_repo", DEFAULT_MLX_REPO)
        )
    except Exception as exc:  # ImportError, missing MLX, checkpoint/download failure
        log.debug("mlx backend unavailable: %s", exc)
        return None


def _try_onnx(cfg: Any) -> DecideBackend | None:
    try:
        return _AgentBackend(
            "onnx", _load_onnx(cfg), _decide_setting(cfg, "onnx_model", DEFAULT_MLX_REPO)
        )
    except Exception as exc:
        log.debug("onnx backend unavailable: %s", exc)
        return None


def select_backend(cfg: Any) -> DecideBackend | None:
    """Return the first usable backend, or None when the layer must stay off."""
    choice = _decide_setting(cfg, "backend", "auto")
    if choice == "off":
        return None
    if choice in ("auto", "mlx"):
        backend = _try_mlx(cfg)
        if backend is not None:
            return backend
    if choice in ("auto", "onnx"):
        return _try_onnx(cfg)
    return None


def _importable(module: str) -> bool:
    """True iff ``import module`` succeeds. Imports only; never loads a model."""
    try:
        __import__(module)
    except Exception as exc:  # ImportError, or a broken native dependency
        log.debug("%s not importable: %s", module, exc)
        return False
    return True


def laya_build(cfg: Any) -> str | None:
    """Which laya build is importable here ("mlx" / "onnx"), else None.

    Honours a forced ``backend``; "off" still probes, because off means
    installed-but-unused. Never calls ``load``.
    """
    choice = _decide_setting(cfg, "backend", "auto")
    if choice in ("auto", "off", "mlx") and _importable("laya_mlx"):
        return "mlx"
    if choice in ("auto", "off", "onnx") and _importable("laya"):
        return "onnx"
    return None


def laya_installed(cfg: Any) -> bool:
    """True iff a laya package usable for ``cfg`` is importable on this machine."""
    return laya_build(cfg) is not None
