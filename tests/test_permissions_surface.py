"""C2.5's Step 6 half: the surface exists, enforcement does not, and the UI
says so. An inert default of "ask" claims MORE safety than the user gets —
that is the A1.40 mistake applied to a safety setting.

Also covers C1.8: both D4 middlewares are gated on [compat] and default off.
"""

import os
from pathlib import Path

import pytest

from rudra.config import build_config, reset_config


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_config()
    yield
    reset_config()


def _write_project_toml(root: Path, body: str) -> None:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(body, encoding="utf-8")


def test_default_mode_is_ask(tmp_path: Path) -> None:
    assert build_config(tmp_path).permissions.mode == "ask"


def test_auto_flag_sets_the_mode(tmp_path: Path) -> None:
    assert build_config(tmp_path, permission_mode="auto").permissions.mode == "auto"


def test_notice_fires_for_the_default_not_only_for_flags(tmp_path: Path) -> None:
    """The dangerous misreading is "ask means it will ask me"."""
    from rudra.cli import _permission_notice

    notice = _permission_notice(build_config(tmp_path))
    assert "NOT ENFORCED" in notice
    assert "ask" in notice


def test_notice_fires_for_auto_too(tmp_path: Path) -> None:
    from rudra.cli import _permission_notice

    assert "NOT ENFORCED" in _permission_notice(build_config(tmp_path, permission_mode="auto"))


def test_compat_flags_default_off(tmp_path: Path) -> None:
    """D4: both surviving middlewares are opt-in."""
    compat = build_config(tmp_path).compat
    assert compat.task_anchor is False
    assert compat.sandbox_paths is False


def test_compat_can_be_switched_on_from_toml(tmp_path: Path) -> None:
    _write_project_toml(tmp_path, "[compat]\ntask_anchor = true\n")
    assert build_config(tmp_path).compat.task_anchor is True


def test_task_anchor_is_absent_from_the_planner_by_default(tmp_path: Path) -> None:
    from rudra.agent.planner_agent import build_planner_middleware

    names = [type(m).__name__ for m in build_planner_middleware("a task", compat_task_anchor=False)]
    assert "TaskAnchorMiddleware" not in names
    assert "FixWriteParamsMiddleware" in names


def test_task_anchor_is_present_when_compat_enables_it(tmp_path: Path) -> None:
    from rudra.agent.planner_agent import build_planner_middleware

    names = [type(m).__name__ for m in build_planner_middleware("a task", compat_task_anchor=True)]
    assert "TaskAnchorMiddleware" in names


def test_task_anchor_is_absent_from_the_coder_by_default(tmp_path: Path) -> None:
    from rudra.agent.coder_agent import build_coder_middleware

    names = [type(m).__name__ for m in build_coder_middleware(compat_task_anchor=False)]
    assert "TaskAnchorMiddleware" not in names
    assert "FixWriteParamsMiddleware" in names


def test_sandbox_prefix_stripping_is_off_by_default() -> None:
    """D4 splits FixWriteParams: fence-strip always on, sandbox paths opt-in."""
    from rudra.middleware import FixWriteParamsMiddleware

    middleware = FixWriteParamsMiddleware()
    request = _FakeRequest({"file_path": "/workspace/main.py", "content": "x = 1"})
    middleware._fix_args(request)
    assert request.tool_call["args"]["file_path"] == "/workspace/main.py"


def test_sandbox_prefix_stripping_happens_when_enabled() -> None:
    from rudra.middleware import FixWriteParamsMiddleware

    middleware = FixWriteParamsMiddleware(strip_sandbox_prefixes=True)
    request = _FakeRequest({"file_path": "/workspace/main.py", "content": "x = 1"})
    middleware._fix_args(request)
    assert request.tool_call["args"]["file_path"] == "main.py"


def test_a_real_absolute_path_is_untouched_when_stripping_is_off() -> None:
    """The reason sandbox stripping is opt-in: it rewrites legitimate paths.

    /src/ is a sandbox prefix, but it is also a perfectly ordinary absolute
    directory on a real machine.
    """
    from rudra.middleware import FixWriteParamsMiddleware

    off = FixWriteParamsMiddleware()
    request = _FakeRequest({"file_path": "/src/main.py", "content": "x = 1"})
    off._fix_args(request)
    assert request.tool_call["args"]["file_path"] == "/src/main.py"

    on = FixWriteParamsMiddleware(strip_sandbox_prefixes=True)
    request = _FakeRequest({"file_path": "/src/main.py", "content": "x = 1"})
    on._fix_args(request)
    assert request.tool_call["args"]["file_path"] == "main.py"


def test_fence_stripping_is_always_on_regardless_of_compat() -> None:
    """Required, not optional: 0.7.4's write() no longer strips (U.3/U.15)."""
    from rudra.middleware import FixWriteParamsMiddleware

    for flag in (False, True):
        middleware = FixWriteParamsMiddleware(strip_sandbox_prefixes=flag)
        request = _FakeRequest({"file_path": "main.py", "content": "```python\nx = 1\n```"})
        middleware._fix_args(request)
        assert request.tool_call["args"]["content"].strip() == "x = 1"


def test_arg_aliasing_is_always_on_regardless_of_compat() -> None:
    from rudra.middleware import FixWriteParamsMiddleware

    middleware = FixWriteParamsMiddleware()
    request = _FakeRequest({"filename": "main.py", "content": "x = 1"})
    middleware._fix_args(request)
    assert request.tool_call["args"]["file_path"] == "main.py"


class _FakeRequest:
    """Minimal stand-in for a deepagents tool-call request."""

    def __init__(self, args: dict) -> None:
        self.tool_call = {"name": "write_file", "args": args}
