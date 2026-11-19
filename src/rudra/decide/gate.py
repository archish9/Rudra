"""Run-entry requirement: laya must be installed unless the layer is disabled.

Additive to the engine's own degradation (an unavailable engine abstains and
Vaayu answers); this only decides whether a RUN may start. Never imports laya
at module scope.
"""

from __future__ import annotations

from typing import Any

from rudra.decide.backends import laya_installed

DOC = "Documentation/16-laya.md"

INSTALL_HINT = "Apple Silicon: pip install laya-mlx   |   elsewhere: pip install 'laya[onnx]'"


def require_laya(cfg: Any) -> str | None:
    """A user-facing message when the run must be blocked, else None."""
    if not getattr(getattr(cfg, "decide", None), "enabled", True):
        return None
    if laya_installed(cfg):
        return None
    return (
        "laya is required to run Rudra and is not installed. "
        f"Install it ({INSTALL_HINT}); see {DOC} for why and how. "
        "To run without it, set [decide] enabled = false."
    )
