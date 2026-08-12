"""What one verification run found. Pure data, plus one derivation.

Six outcomes rather than pass/fail, because collapsing them is the A1.57
failure mode: pytest exits 5 when it collects nothing, and reading that as
"tests failed" sends a fix loop to repair working code. TestResult already
refuses that collapse (testing/runner.py:39-43); this record inherits it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

PASSED = "passed"
FAILED = "failed"
NOT_APPLICABLE = "not_applicable"
COVERED_BY = "covered_by"
MISSING_TOOL = "missing_tool"
DENIED = "denied"

# The order stages run in, and the order the report renders them in.
STAGE_ORDER: tuple[str, ...] = ("syntax", "lint", "typecheck", "test", "stubs")

# Outcomes that stop a blocking stage's pipeline.
_HALTING = frozenset({FAILED, MISSING_TOOL, DENIED})


@dataclass(frozen=True)
class Finding:
    """One problem, located. `line` is None when the tool did not report one."""

    file: str
    line: int | None
    message: str


@dataclass(frozen=True)
class StageResult:
    """What one stage did.

    `escalate` is deliberately a field rather than a function of `outcome`.
    A missing tool and a denied command escalate because no model can fix
    them, but so does an internal error in Rudra itself -- which reports
    `failed`, because from the caller's side the stage did not complete.
    Deriving it from the outcome could not express that third case.
    """

    name: str
    outcome: str
    blocking: bool
    escalate: bool = False
    command: tuple[str, ...] | None = None
    stack: str | None = None
    findings: tuple[Finding, ...] = ()
    output_tail: str = ""
    detail: str = ""
    docs_anchor: str | None = None

    @property
    def halts(self) -> bool:
        """Does this result stop the pipeline?

        Advisory stages never halt, however badly they went. That is what
        "advisory" means -- it governs the verdict, not the visibility.
        """
        return self.blocking and self.outcome in _HALTING


@dataclass(frozen=True)
class VerifyReport:
    """The verdict, and every stage that ran to reach it.

    `escalate` is the field Step 9c branches on: False means the fix loop
    receives `blocker.output_tail` and iterates, True means stop and hand
    back to the user. Without the split, the loop would spend its whole
    attempt budget (C6.5a) asking a model to install a toolchain.
    """

    passed: bool
    stages: tuple[StageResult, ...]
    blocker: StageResult | None
    escalate: bool

    @classmethod
    def from_stages(cls, stages: Iterable[StageResult]) -> VerifyReport:
        ordered = tuple(stages)
        blocker = next((stage for stage in ordered if stage.halts), None)
        return cls(
            passed=blocker is None,
            stages=ordered,
            blocker=blocker,
            escalate=blocker is not None and blocker.escalate,
        )


__all__ = [
    "COVERED_BY",
    "DENIED",
    "FAILED",
    "MISSING_TOOL",
    "NOT_APPLICABLE",
    "PASSED",
    "STAGE_ORDER",
    "Finding",
    "StageResult",
    "VerifyReport",
]
