"""The one-time model download, made explicit (C8.5, D12).

D12 said the model comes from HuggingFace. It does not: the default
`minilm` is chromadb's own bundled ONNX MiniLM, cached at
~/.cache/chroma/onnx_models/all-MiniLM-L6-v2 -- 167 MB measured. HF hub is
reached only by `embeddinggemma`, which Rudra does not select.

Warming goes through mempalace's own embedding function so Rudra never
reimplements a cache layout that can drift under it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.memory.prefetch import MODEL_SIZE_MB, is_warm, model_cache_dir


def test_the_cache_dir_is_chromadbs_not_huggingfaces() -> None:
    """The D12 correction, as a test rather than a comment."""
    path = str(model_cache_dir())
    assert "chroma" in path
    assert "huggingface" not in path


def test_the_cache_dir_is_reported_as_an_absolute_path() -> None:
    assert model_cache_dir().is_absolute()


def test_warmth_is_decided_by_the_model_file_not_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted download leaves the directory behind. Treating that
    as warm is how a run discovers a partial model mid-task, which is the
    exact thing the prefetch exists to prevent."""
    fake = tmp_path / "all-MiniLM-L6-v2"
    (fake / "onnx").mkdir(parents=True)
    monkeypatch.setattr("rudra.memory.prefetch.model_cache_dir", lambda: fake)
    assert is_warm() is False

    (fake / "onnx" / "model.onnx").write_bytes(b"x")
    assert is_warm() is True


def test_the_documented_size_is_a_plausible_number() -> None:
    """A number in a user-facing message. If it drifts far from reality the
    message is worse than none."""
    assert 50 < MODEL_SIZE_MB < 500
