"""Stage ordering, short-circuit, and the two native stages."""

from __future__ import annotations

from rudra.stacks.registry import PYTHON, RUST
from rudra.verify.pipeline import run_pipeline, stubs_stage, syntax_stage
from rudra.verify.result import COVERED_BY, FAILED, PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def names(stages):
    return [stage.name for stage in stages]


def test_python_syntax_stage_runs_no_command(tmp_path):
    name = write(tmp_path, "a.py", "x = 1\n")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == PASSED
    assert result.command is None, "ast.parse is native -- no subprocess, no gate"


def test_python_syntax_stage_catches_a_truncated_file(tmp_path):
    # The exact failure main_agent.py:439 currently records as success.
    name = write(tmp_path, "a.py", "def broken(\n")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == FAILED
    assert result.blocking is True
    assert result.findings[0].file == name


def test_python_syntax_stage_passes_an_empty_file(tmp_path):
    # An empty file parses fine; emptiness is the stub scan's job, not this
    # stage's. Documented so the boundary is not re-litigated later.
    name = write(tmp_path, "a.py", "")
    result = syntax_stage(tmp_path, PYTHON, [name], gate=None, console=None, cfg=None)
    assert result.outcome == PASSED


def test_rust_syntax_is_covered_by_typecheck(tmp_path):
    result = syntax_stage(tmp_path, RUST, [], gate=None, console=None, cfg=None)
    assert result.outcome == COVERED_BY
    assert "typecheck" in result.detail


def test_stubs_stage_reports_findings_and_blocks(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    pass\n")
    result = stubs_stage(tmp_path, [name])
    assert result.outcome == FAILED
    assert result.blocking is True
    assert len(result.findings) == 1


def test_stubs_stage_passes_on_clean_code(tmp_path):
    name = write(tmp_path, "a.py", "def handler():\n    return 1\n")
    assert stubs_stage(tmp_path, [name]).outcome == PASSED


def test_pipeline_stops_at_the_first_blocking_failure(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def broken(\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
    )
    assert names(stages) == ["syntax"], "lint, typecheck, test and stubs must not run"


def test_pipeline_runs_every_stage_when_nothing_blocks(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    return 1\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
        _command_override={"lint": ["true"], "typecheck": ["true"], "test": ["true"]},
    )
    assert names(stages) == ["syntax", "lint", "typecheck", "test", "stubs"]


def test_an_internal_error_in_a_stage_does_not_kill_the_run(tmp_path, monkeypatch):
    # A bug in Rudra must be reported as a bug in Rudra -- escalate, never
    # hand it to the fix loop as if it were the user's code (A1.39 class).
    import rudra.verify.pipeline as pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(pipeline, "scan_stubs", boom)
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "x = 1\n")
    stages = run_pipeline(
        tmp_path,
        profile=PYTHON,
        changed_files=["a.py"],
        gate=None,
        console=None,
        cfg=FakeCfg(),
        _command_override={"lint": ["true"], "typecheck": ["true"], "test": ["true"]},
    )
    last = stages[-1]
    assert last.name == "stubs"
    assert last.outcome == FAILED
    assert last.escalate is True
    assert "internal error" in last.detail
