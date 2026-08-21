"""Rendering, the verdict line, and the exit-code contract."""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from rudra.verify.report import exit_code, render, to_dict, verdict_line
from rudra.verify.result import (
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


def stage(name, outcome, *, blocking=True, escalate=False, **kwargs):
    return StageResult(name=name, outcome=outcome, blocking=blocking, escalate=escalate, **kwargs)


def test_a_clean_run_exits_zero():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("test", PASSED)])
    assert exit_code(report) == 0


def test_a_blocking_failure_exits_one():
    report = VerifyReport.from_stages([stage("typecheck", FAILED)])
    assert exit_code(report) == 1


def test_an_escalation_exits_two():
    # 2 already means "the environment is wrong, not your code" in this CLI.
    report = VerifyReport.from_stages([stage("typecheck", MISSING_TOOL, escalate=True)])
    assert exit_code(report) == 2
    report = VerifyReport.from_stages([stage("test", DENIED, escalate=True)])
    assert exit_code(report) == 2


def test_the_verdict_never_says_a_bare_passed():
    report = VerifyReport.from_stages(
        [
            stage("syntax", PASSED),
            stage("lint", PASSED, blocking=False),
            stage("typecheck", NOT_APPLICABLE, detail="plain JavaScript project"),
            stage("test", NOT_APPLICABLE, detail="no tests were collected"),
            stage("stubs", PASSED),
        ]
    )
    line = verdict_line(report)
    assert line.startswith("passed")
    assert "2 of 5 stages did not run" in line
    assert "plain JavaScript project" in line
    assert "no tests were collected" in line


def test_a_fully_clean_verdict_says_so_plainly():
    report = VerifyReport.from_stages(
        [
            stage(name, PASSED, blocking=name != "lint")
            for name in ("syntax", "lint", "typecheck", "test", "stubs")
        ]
    )
    assert verdict_line(report) == "passed — all 5 stages ran clean"


def test_a_failed_verdict_names_the_blocker():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    assert verdict_line(report).startswith("failed — typecheck")


def test_the_docs_anchor_reaches_the_verdict():
    report = VerifyReport.from_stages(
        [
            stage(
                "typecheck",
                MISSING_TOOL,
                escalate=True,
                detail="node_modules/.bin/tsc is absent",
                docs_anchor="Documentation/10-verification.md#typescript",
            )
        ]
    )
    assert "Documentation/10-verification.md#typescript" in verdict_line(report)


def test_to_dict_is_json_serialisable_and_complete():
    report = VerifyReport.from_stages(
        [
            stage("syntax", PASSED, detail="1 file parsed"),
            stage(
                "stubs",
                FAILED,
                findings=(Finding("a.py", 3, "handler: function body is a bare `pass`"),),
            ),
        ]
    )
    payload = to_dict(report)
    json.dumps(payload)
    assert payload["passed"] is False
    assert payload["escalate"] is False
    assert payload["blocker"] == "stubs"
    assert payload["stages"][1]["findings"][0]["line"] == 3
    assert [entry["name"] for entry in payload["stages"]] == ["syntax", "stubs"]


def test_render_marks_stages_that_never_ran():
    report = VerifyReport.from_stages([stage("syntax", PASSED), stage("typecheck", FAILED)])
    console = Console(record=True, width=100)
    render(report, console)
    text = console.export_text()
    assert "not run" in text
    assert "stopped at typecheck" in text


def test_tool_output_with_brackets_is_neither_swallowed_nor_fatal():
    """CR-E4: `finding.message` and `stage.output_tail` come straight from
    mypy/ruff/eslint/pytest and were printed through Rich with markup on.
    mypy emits `list[int]` constantly, and it rendered as `list` -- the
    reader lost the part that matters. Worse, an output_tail containing
    `[/dim]` raised MarkupError out of render() AFTER the table had printed,
    so the user got no failure output at all. Same class as A1.67/A1.48/
    A1.91, already escaped for in trace/render.py.
    """
    buffer = StringIO()
    console = Console(file=buffer, width=120)
    typecheck = StageResult(
        name="typecheck",
        outcome=FAILED,
        blocking=True,
        findings=(Finding("a.py", 3, 'incompatible type "list[int]"; expected "dict[str, Any]"'),),
        detail="1 error",
    )
    test = StageResult(
        name="test",
        outcome=FAILED,
        blocking=True,
        output_tail="assert '[/dim]' in out",
    )

    render(
        VerifyReport(passed=False, stages=(typecheck, test), blocker=typecheck, escalate=False),
        console,
    )

    output = buffer.getvalue()
    assert "list[int]" in output
    assert "dict[str, Any]" in output
    assert "[/dim]" in output
