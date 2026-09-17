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


def test_a_python_file_is_parsed_even_before_the_project_has_a_marker_file(tmp_path):
    """CR-E1: the stage dispatched on the DETECTED STACK, and detect()
    returns nothing until pyproject.toml/setup.py/requirements.txt exists.
    So on a greenfield tree -- `rudra "build a flask app"`, task 1 -- a .py
    file was routed to the JavaScript checker, came back NOT_APPLICABLE
    ("no JavaScript files changed"), and passed the whole gate while lint,
    typecheck and test all abstained for want of a stack. stubs could not
    catch it either: _scan_python swallows SyntaxError precisely because
    this stage is supposed to have blocked first. The loop then marked the
    task DONE on report.passed.
    """
    (tmp_path / "app.py").write_text("def main(:\n    retur\n", encoding="utf-8")

    result = syntax_stage(tmp_path, None, ["app.py"], gate=None, console=None, cfg=None)

    assert result.outcome == FAILED
    assert result.findings[0].file == "app.py"


def test_a_valid_python_file_still_passes_with_no_stack(tmp_path):
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")

    result = syntax_stage(tmp_path, None, ["app.py"], gate=None, console=None, cfg=None)

    assert result.outcome == PASSED


def test_a_deleted_file_is_skipped_not_failed(tmp_path):
    """CR-E2: a missing file raised OSError inside the parse loop and was
    recorded as "does not parse" -- a blocking failure whose message was an
    absolute host path, handed to the fix loop verbatim until the signature
    repeated and the task went BLOCKED. Deletions reach here by design:
    changed_since keeps them and `git status` reports them as ` D path`.
    verify/stubs.py already skips missing files; the two native stages must
    not disagree about what a deletion means.
    """
    result = syntax_stage(tmp_path, PYTHON, ["gone.py"], gate=None, console=None, cfg=None)

    assert result.outcome == PASSED
    assert result.findings == ()


def test_a_deleted_javascript_file_is_skipped_not_failed(tmp_path):
    """OPEN-123: CR-E2's rule, for the stage CR-E2 did not reach. `node
    --check` on a path that is gone exits 1 with `Cannot find module`, which
    was recorded as a parse failure. It cost one attempt while a gate judged
    one attempt's diff; once the loop hands the gate every file the task
    touched, a deleted path stays in scope and would fail every later gate.
    Needs no node on PATH: there is nothing left to check."""
    result = syntax_stage(tmp_path, None, ["gone.js"], gate=None, console=None, cfg=FakeCfg())

    assert result.outcome != FAILED
    assert result.findings == ()
