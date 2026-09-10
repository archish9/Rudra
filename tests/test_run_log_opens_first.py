"""The run log exists before anything that can fail (OPEN-108).

`configure_debug_logging` used to run at `main_agent.py:959`, after the
skills cache render, the backend, the gate, `.mcp.json` and its subprocess,
`aiosqlite.connect` and `checkpointer.setup()`. Every one of those can raise
on a machine that is not the maintainer's -- a locked-down cache root, an
MCP server that will not spawn, a sqlite file on a filesystem without
locking -- and each of those failures reached a terminal and wrote nothing:
`debug-<id>.jsonl` did not exist yet, and neither did the console tee that
catches a traceback (OPEN-76).

**The complete record was absent exactly in the class of failure it exists
for**, which is the whole of `CLAUDE.md` §8a in one line.

These are ordering pins over the source, in the shape
`test_agent_wiring.py` already uses: the behaviour they protect cannot be
observed without a real failing MCP server, and a comment asserting two
things are in the right order is the reason nobody checks (CLAUDE.md §3,
`is_build_output`'s lesson).
"""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path

import pytest

from rudra.agent.main_agent import _open_run_log
from rudra.state.paths import ensure_layout


def _create_main_agent_body() -> list[ast.stmt]:
    import rudra.agent.main_agent as module

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_main_agent":
            return node.body
    raise AssertionError("create_main_agent is gone from main_agent.py")


def _first_line_calling(body: list[ast.stmt], name: str) -> int:
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func = node.func
            called = getattr(func, "id", None) or getattr(func, "attr", None)
            if called == name:
                return node.lineno
    raise AssertionError(f"create_main_agent no longer calls {name}()")


@pytest.mark.parametrize(
    "later",
    [
        "ensure_cache",  # the skills corpus render
        "build_backend",
        "build_gate",
        "read_mcp_json",
        "connect",  # aiosqlite
        "setup",  # the checkpointer
    ],
)
def test_the_run_log_is_opened_before_everything_that_can_fail(later: str) -> None:
    body = _create_main_agent_body()

    assert _first_line_calling(body, "_open_run_log") < _first_line_calling(body, later), (
        f"{later}() now runs before the run log is open; a failure in it would "
        "leave no debug-<id>.jsonl at all (OPEN-108)"
    )


def test_the_run_id_is_generated_before_the_log_that_is_named_after_it() -> None:
    """`session_id` used to be assigned after four of those six lines."""
    body = _create_main_agent_body()
    module = ast.Module(body=body, type_ignores=[])

    assigned = [
        node.lineno
        for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", None) == "session_id" for target in node.targets)
    ]

    assert assigned, "create_main_agent no longer assigns session_id"
    assert min(assigned) < _first_line_calling(body, "_open_run_log")


def test_the_meta_file_is_written_before_the_run_can_fail() -> None:
    """OPEN-106's half of the same move: what ran, on what, in which mode."""
    body = _create_main_agent_body()

    assert _first_line_calling(body, "write_run_meta") < _first_line_calling(body, "build_gate")


# --- the helper itself ------------------------------------------------------


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str = "") -> None:
        self.lines.append(str(text))


class _Cfg:
    class agent:  # noqa: N801 - a stand-in for the config dataclass
        debug_log = True


def _detach() -> None:
    logger = logging.getLogger("rudra")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_open_run_log_writes_where_the_run_can_be_found(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path / "app")
    try:
        handler = _open_run_log(paths, "run1", cfg=_Cfg(), debug=None, console=_Console())
        assert handler is not None
        logging.getLogger("rudra.test").debug("setup got this far")
        handler.flush()
    finally:
        _detach()

    assert "setup got this far" in (Path(paths.logs) / "debug-run1.jsonl").read_text(
        encoding="utf-8"
    )


def test_no_debug_opens_nothing(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path / "app")
    try:
        assert _open_run_log(paths, "r", cfg=_Cfg(), debug=False, console=_Console()) is None
    finally:
        _detach()
    assert not list(Path(paths.logs).glob("debug-*.jsonl"))


def test_a_log_that_cannot_be_opened_says_so_and_does_not_raise(tmp_path: Path) -> None:
    """Bookkeeping must not end a run (loop/engine.py's write_usage_log)."""
    paths = ensure_layout(tmp_path / "app")
    console = _Console()
    wall = Path(paths.logs) / "debug-r.jsonl"
    wall.mkdir()  # a directory where the file must go

    try:
        assert _open_run_log(paths, "r", cfg=_Cfg(), debug=None, console=console) is None
    finally:
        _detach()

    assert any("Could not open the run log" in line for line in console.lines)
