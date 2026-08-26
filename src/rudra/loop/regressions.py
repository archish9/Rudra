"""Which failures a task answers for.

Imports nothing from Rudra, for the reason `ledger.py` and `bounds.py` do
not: this is the arithmetic the loop's hardest decision rests on, and it is
worth reading and testing without a graph, a gate or a model.

**The rule: a task answers for what it broke, not for what it inherited.**

The gate is project-wide on purpose -- a task that breaks a sibling's tests
must not pass -- and the coder is task-scoped on purpose -- an agent that
wanders is the failure Step 9b exists to prevent. Neither is wrong, and
before OPEN-23 the composition of the two was: the whole suite runs, a
failure in a file the task does not own comes back verbatim, and the coder
has nothing it is allowed to fix. Measured 2026-08-26 (run `ee29dd3ebf51`):
one test file, written by the `t1` tester against a CLI that task `t8` had
not built yet, blocked six consecutive tasks and then ended the run --
`t8` among the five never attempted.

**Why this is a time comparison and not a file comparison.** The obvious fix
is to ask whether a finding names a file the task touched. It is wrong, and
the same run says so: `t2` touched only `todo/storage.py`, yet
`tests/test_cli.py` failed *through* it -- `todo/cli.py:34` called storage,
which raised at `todo/storage.py:96`. A rule keyed on the file would have
called that out of scope and passed a genuine regression. Asking *when* the
failure started answers both cases with one comparison, and keeps the
project-wide invariant strictly: a NEW failure blocks the task whether or not
it is in a file the task opened.

The baseline is per run and in memory. A fresh process -- the first task, or
`--continue` -- has none, every failure reads as new, and the loop behaves
exactly as it did before this module existed. That is the safe direction: the
degraded mode is the old blocking one, never the passing one.
"""

from __future__ import annotations

from typing import Any

PASSED = "passed"
"""The gate is green. Nothing to attribute."""

REGRESSED = "regressed"
"""At least one failure is new since this task started. The task owns it."""

INHERITED = "inherited"
"""Every failure was already failing. This task introduced none of them."""


def failure_keys(report: Any) -> frozenset[str]:
    """A stable identity per located failure in this report.

    `file:line`, not `file:line:message`: an assertion's rendered text
    carries the values that differed, which change between runs for reasons
    that have nothing to do with whether it is the same failure.

    A finding with no line keys on `None` rather than being dropped --
    `Finding.line` is None whenever the tool reported none
    (verify/result.py:29-31), and discarding those would hide a whole class
    of failure from the comparison.
    """
    if report.passed or report.blocker is None:
        return frozenset()
    return frozenset(f"{finding.file}:{finding.line}" for finding in report.blocker.findings)


def verdict_for(report: Any, *, inherited: frozenset[str]) -> str:
    """PASSED, REGRESSED or INHERITED for one gate run.

    Args:
        report: A VerifyReport.
        inherited: The failure keys that were already failing when this task
            started -- NOT when this attempt started. A regression introduced
            by attempt 1 must still be the task's own on attempt 2.

    An empty current set is REGRESSED, never INHERITED, and that asymmetry is
    the load-bearing part: the empty set is a subset of every set, so the
    subset test alone would wave through every failure the shallow parser
    could not locate (verify/pipeline.py:291-309). A failure Rudra cannot
    place is a failure it cannot attribute, and an unattributable failure
    belongs to whoever is holding it.
    """
    if report.passed:
        return PASSED
    current = failure_keys(report)
    if current and current <= inherited:
        return INHERITED
    return REGRESSED


__all__ = ["INHERITED", "PASSED", "REGRESSED", "failure_keys", "verdict_for"]
