"""Deterministic verification -- Rudra's definition of a completed task (C6.6).

Replaces "the file exists on disk" (agent/main_agent.py:438-439), under
which a zero-byte file, a truncated file, and `def main(): pass` all count
as a completed task and get ticked off the plan. That is A1.8.

No model is involved anywhere in this package. The verdict is the test
runner's exit code, a parser, and a scanner -- nothing that can be talked
out of an answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.git.core import is_repo, status
from rudra.stacks.detect import detect
from rudra.state.paths import rudra_paths
from rudra.verify.pipeline import run_pipeline
from rudra.verify.report import exit_code, render, to_dict, verdict_line
from rudra.verify.result import Finding, StageResult, VerifyReport
from rudra.verify.stubs import source_files


def _write_log(project_path: Path, report: VerifyReport) -> None:
    """Full stage output to .rudra/run/logs/verify.log.

    Mirrors testing/runner.py:95-108, including its refusal to fail a run
    over a log that could not be written. D15's volatile subtree.
    """
    lines = [verdict_line(report), ""]
    for stage in report.stages:
        lines.append(f"## {stage.name}: {stage.outcome}")
        if stage.command:
            lines.append(f"command: {' '.join(stage.command)}")
        if stage.detail:
            lines.append(stage.detail)
        for finding in stage.findings:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            lines.append(f"  {location}: {finding.message}")
        if stage.output_tail:
            lines.append(stage.output_tail)
        lines.append("")

    logs = rudra_paths(project_path).logs
    try:
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "verify.log").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def verify_project(
    project_path: Path,
    *,
    changed_files: Sequence[str] = (),
    gate: Any,
    console: Console,
    cfg: Any,
    _command_override: dict[str, list[str]] | None = None,
) -> VerifyReport:
    """Run the gate and report what it found.

    `changed_files` are project-relative paths and scope the stub scan only
    (spec S9a.5) -- every other stage is whole-project, because a new file
    can break an old one's imports and a test suite is whole-project by
    nature. Step 9c passes the exact paths its coder wrote; `rudra verify`
    derives them from git.

    gate, console and cfg are threaded rather than read from module state,
    the same shape run_tests uses (testing/runner.py:111). A1.52 is an open
    defect about factories reading Path.cwd() instead of their argument;
    this signature does not repeat it.

    `_command_override` is test-only, following the precedent set at
    testing/runner.py:117-123: it exercises the missing-binary and timeout
    paths without requiring the machine to lack a toolchain.
    """
    project_path = Path(project_path)
    profiles = detect(project_path)
    # profiles[0] -- the most specific match, the same choice run_tests
    # makes (testing/runner.py:126-127). A Tauri app is genuinely both Rust
    # and Node; running every matched toolchain would triple the cost on a
    # monorepo and produce a verdict nobody asked for.
    profile = profiles[0] if profiles else None

    stages = run_pipeline(
        project_path,
        profile=profile,
        changed_files=list(changed_files),
        gate=gate,
        console=console,
        cfg=cfg,
        _command_override=_command_override,
    )
    report = VerifyReport.from_stages(stages)
    _write_log(project_path, report)
    return report


def changed_files_from_git(
    project_path: Path, *, gate: Any, console: Console, cfg: Any
) -> tuple[str, ...] | None:
    """Modified and untracked paths, or None when this is not a git repo.

    None is a real answer the caller acts on: `rudra verify` falls back to
    scanning everything and says so, because scanning nothing would report
    a clean stub stage over an unexamined tree.
    """
    project_path = Path(project_path)
    if not is_repo(project_path, gate=gate, console=console, cfg=cfg):
        return None
    entries = status(project_path, gate=gate, console=console, cfg=cfg)
    return tuple(sorted({entry.path for entry in entries if entry.path}))


__all__ = [
    "Finding",
    "StageResult",
    "VerifyReport",
    "changed_files_from_git",
    "exit_code",
    "render",
    "source_files",
    "to_dict",
    "verdict_line",
    "verify_project",
]
