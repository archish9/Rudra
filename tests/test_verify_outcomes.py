"""Every outcome a command stage can produce -- §5 of the spec, row by row."""

from __future__ import annotations

import json

from rudra.shell.runner import CommandResult
from rudra.stacks.registry import NODE, PYTHON
from rudra.verify import pipeline
from rudra.verify.result import DENIED, FAILED, MISSING_TOOL, NOT_APPLICABLE, PASSED


class FakeCfg:
    # `models` is not decoration: scrubbed_env reads cfg.models to find the
    # api_key_env names it must strip (permissions/env.py:34), and every
    # command stage passes env=scrubbed_env(cfg).
    models: dict = {}

    class tools:  # noqa: N801 - mirrors the Config attribute path
        test_timeout = 5


def fake_run(result):
    def runner(argv, **kwargs):
        return result

    return runner


def command_result(**kwargs):
    base = dict(argv=("x",), command="x", exit_code=0, stdout="", stderr="")
    base.update(kwargs)
    return CommandResult(**base)


def test_a_clean_command_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "run_gated", fake_run(command_result(exit_code=0)))
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == PASSED


def test_a_denied_command_escalates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_gated",
        fake_run(
            command_result(exit_code=None, denied=True, denial_reason="<auto:shell-not-opted-in>")
        ),
    )
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == DENIED
    assert result.escalate is True
    assert "shell-not-opted-in" in result.detail


def test_a_timeout_fails_without_escalating(tmp_path, monkeypatch):
    # An infinite loop in generated code is a real defect the fix loop can
    # fix, so this must not escalate.
    monkeypatch.setattr(
        pipeline, "run_gated", fake_run(command_result(exit_code=None, timed_out=True))
    )
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.escalate is False
    assert "test_timeout" in result.detail


def test_a_binary_that_will_not_start_is_a_missing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "run_gated",
        fake_run(command_result(exit_code=None, stderr="mypy: No such file or directory")),
    )
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True
    assert result.docs_anchor is not None


def test_a_nonzero_exit_fails_and_parses_findings(tmp_path, monkeypatch):
    output = "src/models.py:14: error: Argument 1 has incompatible type\n"
    monkeypatch.setattr(pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout=output)))
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.findings[0].file == "src/models.py"
    assert result.findings[0].line == 14


def test_tsc_style_findings_are_parsed():
    findings = pipeline._parse_findings("src/app.ts(12,3): error TS2345: Argument type\n")
    assert findings[0].file == "src/app.ts"
    assert findings[0].line == 12


def test_plain_javascript_typecheck_is_not_applicable(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")
    result = pipeline.typecheck_stage(tmp_path, NODE, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == NOT_APPLICABLE
    assert result.escalate is False


def test_a_declared_but_uninstalled_tool_escalates_with_a_docs_anchor(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"typescript": "^5"}}), encoding="utf-8"
    )
    result = pipeline.typecheck_stage(tmp_path, NODE, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == MISSING_TOOL
    assert result.escalate is True
    assert result.docs_anchor == "Documentation/10-verification.md#typescript"


def test_lint_is_advisory_even_when_it_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout="a.py:1:1: F401\n"))
    )
    result = pipeline.lint_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.blocking is False
    assert result.halts is False
    assert result.findings, "advisory governs the verdict, not the visibility"


def make_venv_mypy(tmp_path):
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    executable = binaries / "mypy"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def test_bundled_mypy_type_errors_are_advisory(tmp_path, monkeypatch):
    """OPEN-38. `--ignore-missing-imports` makes the bundled verdict a
    function of Rudra's OWN site-packages, so the same file gets opposite
    answers on two machines. Measured 2026-08-27 on run6's `src/models.py`
    at its second attempt (`Model = SQLAlchemy.Model`):

        Rudra's venv has flask_sqlalchemy   3 errors
        a clean Rudra install               2 errors

    A verdict that cannot be reproduced must not fail a task. It is still
    reported in full -- this is lint's disposition (S9a.2), not silence.
    """
    output = "src/models.py:6: error: Cannot access instance-only attribute\n"
    monkeypatch.setattr(pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout=output)))
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.blocking is False
    assert result.halts is False
    assert result.findings, "advisory governs the verdict, not the visibility"


def test_a_project_venv_mypy_still_blocks(tmp_path, monkeypatch):
    """The other half of OPEN-38. A project that installed mypy has adopted
    it: the tool sees the project's real dependencies and reads the project's
    own config, so its verdict is reproducible and keeps blocking."""
    make_venv_mypy(tmp_path)
    output = "src/models.py:6: error: Incompatible return value type\n"
    monkeypatch.setattr(pipeline, "run_gated", fake_run(command_result(exit_code=1, stdout=output)))
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == FAILED
    assert result.blocking is True
    assert result.halts is True


def test_bundled_mypy_denial_still_escalates(tmp_path, monkeypatch):
    """Advisory is about the VERDICT, not about the stage being unrunnable.
    Under `--auto` without `--allow-shell` the command is denied, which no
    model can fix, so it must still stop the run (CLAUDE.md §8)."""
    monkeypatch.setattr(
        pipeline,
        "run_gated",
        fake_run(
            command_result(exit_code=None, denied=True, denial_reason="<auto:shell-not-opted-in>")
        ),
    )
    result = pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert result.outcome == DENIED
    assert result.blocking is True
    assert result.halts is True
    assert result.escalate is True


def capturing_run(result, sink):
    def runner(argv, **kwargs):
        sink.update(kwargs)
        sink["argv"] = argv
        return result

    return runner


def test_a_resolutions_environment_reaches_the_command(tmp_path, monkeypatch):
    """OPEN-38 commit 2. The override is useless if it stops at the
    resolution, and `scrubbed_env` inherits os.environ wholesale
    (permissions/env.py:34) -- so an ambient MYPYPATH would otherwise pass
    straight through."""
    monkeypatch.setenv("MYPYPATH", "/somewhere/the/user/happened/to/have")
    sink: dict = {}
    monkeypatch.setattr(pipeline, "run_gated", capturing_run(command_result(exit_code=0), sink))
    pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert sink["env"]["MYPYPATH"] == ""


def test_the_rest_of_the_environment_survives_the_override(tmp_path, monkeypatch):
    """It is an override, not a replacement. LocalShellBackend's empty-env
    default already cost a session once (A1.44) -- a stage that dropped PATH
    would find no toolchain at all."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    sink: dict = {}
    monkeypatch.setattr(pipeline, "run_gated", capturing_run(command_result(exit_code=0), sink))
    pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert sink["env"]["PATH"] == "/usr/bin:/bin"


def test_a_project_venv_mypy_gets_the_plain_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MYPYPATH", "/the/projects/own/stubs")
    make_venv_mypy(tmp_path)
    sink: dict = {}
    monkeypatch.setattr(pipeline, "run_gated", capturing_run(command_result(exit_code=0), sink))
    pipeline.typecheck_stage(tmp_path, PYTHON, gate=object(), console=None, cfg=FakeCfg())
    assert sink["env"]["MYPYPATH"] == "/the/projects/own/stubs"
