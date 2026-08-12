"""The public entry point: stack selection, logging, changed-file discovery."""

from __future__ import annotations

import json

from rich.console import Console

from rudra.verify import changed_files_from_git, verify_project
from rudra.verify.result import PASSED


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


OVERRIDES = {"lint": ["true"], "typecheck": ["true"], "test": ["true"]}


def test_a_clean_python_project_passes(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    return 1\n")
    report = verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    assert report.passed is True
    assert [stage.outcome for stage in report.stages].count(PASSED) >= 3


def test_a_stub_blocks_even_when_everything_else_passes(tmp_path):
    # C6.6's entire justification: tests pass, placeholder remains.
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def handler():\n    pass\n")
    report = verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    assert report.passed is False
    assert report.blocker.name == "stubs"
    assert report.escalate is False


def test_the_most_specific_profile_wins(tmp_path):
    # A React project also matches plain node; profiles[0] is the answer,
    # the same choice run_tests makes (testing/runner.py:126-127).
    write(tmp_path, "package.json", json.dumps({"dependencies": {"react": "^19"}}))
    report = verify_project(
        tmp_path,
        changed_files=[],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    stacks = {stage.stack for stage in report.stages if stage.stack}
    assert stacks == {"react"}


def test_a_greenfield_directory_does_not_crash(tmp_path):
    report = verify_project(tmp_path, changed_files=[], gate=None, console=Console(), cfg=FakeCfg())
    assert isinstance(report.passed, bool)


def test_the_full_output_is_logged(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "a.py", "def broken(\n")
    verify_project(
        tmp_path,
        changed_files=["a.py"],
        gate=None,
        console=Console(),
        cfg=FakeCfg(),
        _command_override=OVERRIDES,
    )
    log = tmp_path / ".rudra" / "run" / "logs" / "verify.log"
    assert log.is_file()
    assert "syntax" in log.read_text(encoding="utf-8")


def test_changed_files_outside_a_repo_is_none(tmp_path, monkeypatch):
    import rudra.verify as verify

    monkeypatch.setattr(verify, "is_repo", lambda *a, **k: False)
    assert changed_files_from_git(tmp_path, gate=None, console=Console(), cfg=FakeCfg()) is None
