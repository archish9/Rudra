import pytest

from rudra.config import ConfigError
from rudra.config.loader import build_config


def _write(tmp_path, body):
    d = tmp_path / ".rudra"
    d.mkdir()
    (d / "config.toml").write_text(body)
    return tmp_path


def test_decide_defaults(tmp_path):
    d = build_config(tmp_path).decide
    assert d.enabled is True
    assert d.backend == "auto"
    assert d.mlx_repo == "convaiinnovations/laya"
    assert d.onnx_model == "convaiinnovations/laya"
    assert d.calibration_dir is None


def test_decide_user_override(tmp_path):
    root = _write(tmp_path, '[decide]\nbackend = "onnx"\n')
    assert build_config(root).decide.backend == "onnx"


def test_unknown_decide_key_suggests_nearest(tmp_path):
    root = _write(tmp_path, "[decide]\nenabledd = true\n")
    with pytest.raises(ConfigError, match="Did you mean 'enabled'"):
        build_config(root)


def test_unknown_section_still_fatal(tmp_path):
    root = _write(tmp_path, "[decid]\nenabled = true\n")
    with pytest.raises(ConfigError, match="Did you mean 'decide'"):
        build_config(root)
