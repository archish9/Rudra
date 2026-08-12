"""Rendering only. Nothing here decides anything.

The verdict never says a bare "passed": a gate reporting success while
three stages were skipped is A1.57 at report level, so every stage that
did not produce a result is named.
"""

from __future__ import annotations

from dataclasses import asdict

from rich.console import Console
from rich.table import Table

from rudra.verify.result import (
    COVERED_BY,
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    STAGE_ORDER,
    VerifyReport,
)

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_ESCALATE = 2

# Outcomes that mean the stage produced no judgement about the code.
_INCONCLUSIVE = frozenset({NOT_APPLICABLE, COVERED_BY})

_STYLES = {
    PASSED: "green",
    FAILED: "red",
    DENIED: "yellow",
    MISSING_TOOL: "yellow",
    NOT_APPLICABLE: "dim",
    COVERED_BY: "dim",
}

_MAX_RENDERED_FINDINGS = 20


def exit_code(report: VerifyReport) -> int:
    """0 passed, 1 a blocking stage failed, 2 escalation.

    2 already means "the environment is wrong, not your code" in this CLI --
    a non-TTY under mode = "ask" exits 2 before any model call -- so
    escalation maps onto it rather than inventing a code.
    """
    if report.escalate:
        return _EXIT_ESCALATE
    return _EXIT_OK if report.passed else _EXIT_FAILED


def verdict_line(report: VerifyReport) -> str:
    """One line, and it always accounts for all five stages."""
    if not report.passed and report.blocker is not None:
        blocker = report.blocker
        line = f"failed — {blocker.name} ({blocker.outcome})"
        if blocker.detail:
            line = f"{line}: {blocker.detail}"
        if blocker.docs_anchor:
            line = f"{line} — see {blocker.docs_anchor}"
        return line

    inconclusive = [stage for stage in report.stages if stage.outcome in _INCONCLUSIVE]
    accounted = {stage.name for stage in report.stages}
    absent = [name for name in STAGE_ORDER if name not in accounted]

    skipped = len(inconclusive) + len(absent)
    if skipped == 0:
        return f"passed — all {len(STAGE_ORDER)} stages ran clean"

    reasons = [f"{stage.name}: {stage.detail or stage.outcome}" for stage in inconclusive]
    reasons.extend(f"{name}: did not run" for name in absent)
    return f"passed — {skipped} of {len(STAGE_ORDER)} stages did not run ({'; '.join(reasons)})"


def to_dict(report: VerifyReport) -> dict:
    """The --json payload. Every field the human table shows, plus findings."""
    return {
        "passed": report.passed,
        "escalate": report.escalate,
        "blocker": report.blocker.name if report.blocker is not None else None,
        "verdict": verdict_line(report),
        "exit_code": exit_code(report),
        "stages": [
            {
                **asdict(stage),
                "command": list(stage.command) if stage.command else None,
                "findings": [asdict(finding) for finding in stage.findings],
            }
            for stage in report.stages
        ],
    }


def render(report: VerifyReport, console: Console) -> None:
    """The human view: one row per stage, then the verdict and the tail."""
    produced = {stage.name: stage for stage in report.stages}
    stopped_at = report.blocker.name if report.blocker is not None else None

    table = Table(title="rudra verify", header_style="bold")
    for column in ("Stage", "Outcome", "Detail"):
        table.add_column(column, overflow="fold")

    for name in STAGE_ORDER:
        stage = produced.get(name)
        if stage is None:
            reason = f"not run (stopped at {stopped_at})" if stopped_at else "not run"
            table.add_row(name, "—", f"[dim]{reason}[/dim]")
            continue
        outcome = stage.outcome + ("  (advisory)" if not stage.blocking else "")
        style = _STYLES.get(stage.outcome, "")
        detail = stage.detail
        if stage.docs_anchor:
            detail = f"{detail} — see {stage.docs_anchor}" if detail else f"see {stage.docs_anchor}"
        table.add_row(name, f"[{style}]{outcome}[/{style}]" if style else outcome, detail)

    console.print(table)

    marker = "✓" if report.passed else "✗"
    console.print(f"\n{marker} {verdict_line(report)}")

    for stage in report.stages:
        if stage.outcome != FAILED or not (stage.findings or stage.output_tail):
            continue
        console.print(f"\n[bold]{stage.name}[/bold]")
        for finding in stage.findings[:_MAX_RENDERED_FINDINGS]:
            location = f"{finding.file}:{finding.line}" if finding.line else finding.file
            console.print(f"  {location}: {finding.message}")
        if not stage.findings and stage.output_tail:
            console.print(stage.output_tail)


__all__ = ["exit_code", "render", "to_dict", "verdict_line"]
