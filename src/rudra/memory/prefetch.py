"""The one-time embedding-model download, made explicit (C8.5, D12).

D12 says to pre-fetch the model so nothing downloads mid-task, and names
the wrong cache. Measured against mempalace 3.7.1: the default
`embedding_model` is `minilm` (config.py:746), which is *chromadb's*
bundled ONNX MiniLM (embedding.py:160-215), cached under
`~/.cache/chroma/onnx_models/`. HuggingFace hub is reached only by
`embeddinggemma` (embedding.py:344), which Rudra does not select.

Warming calls mempalace's own embedding function and embeds one throwaway
string, so the download happens through the vendor's code path. Rudra
never names a URL or reconstructs a cache layout that can drift.

The cache is user-global and shared by every project: a one-time cost,
which is exactly what D12 wanted.
"""

from __future__ import annotations

from pathlib import Path

# Measured on disk 2026-08-19, not read off a README.
MODEL_SIZE_MB = 167


def model_cache_dir() -> Path:
    """Where chromadb keeps the ONNX MiniLM it embeds with.

    Read off the class rather than hardcoded, so a chromadb that moves its
    cache moves this too.
    """
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    return Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH)


def is_warm() -> bool:
    """Is the model already on disk?

    Checks the model file, not the directory: an interrupted download
    leaves the directory behind, and calling that warm is how a run
    discovers a partial model mid-task -- the thing this exists to prevent.
    """
    return (model_cache_dir() / "onnx" / "model.onnx").is_file()


def warm_model() -> tuple[bool, str]:
    """Download the model if absent. Returns (ok, message).

    Never raises: a failed warm costs a slower first run, not the command
    the user actually asked for.
    """
    if is_warm():
        return True, f"already present ({model_cache_dir()})"
    try:
        from mempalace import embedding

        embedding.get_embedding_function()(["warm"])
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"downloaded to {model_cache_dir()}"


__all__ = ["MODEL_SIZE_MB", "is_warm", "model_cache_dir", "warm_model"]
