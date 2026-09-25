"""When the loop is stuck, and when it should ask for tests.

Pure functions over a VerifyReport. No I/O, no model, nothing from Rudra
except the report types -- so a test can drive every branch directly.
"""

from __future__ import annotations

import re
from hashlib import sha256

from rudra.verify.result import NOT_APPLICABLE, STAGE_ORDER, StageResult, VerifyReport

_SIGNATURE_LENGTH = 16

# One exception line, as a runner prints it: `ModuleNotFoundError: No module
# named 'main'`, `pkg.errors.ConfigError: ...`. Matched after the runner's own
# prefixes are stripped -- pytest's `E` column and its `INTERNALERROR>` gutter.
_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception):")
_RUNNER_PREFIX = re.compile(r"^(?:INTERNALERROR>|E)\s+")
# What differs between two runs of ONE failure: where the project lives, and
# where an object sat in memory. ABSOLUTE paths only -- the lookbehind refuses
# a separator inside a word, so `data/users.json` is left as it is: a relative
# path names what failed, and `data/users.json` against `data/orders.json` is
# progress. `C:\...` is matched by shape on every host (CLAUDE.md §1.8).
_ABSOLUTE_PATH = re.compile(r"(?<![\w.])(?:[A-Za-z]:)?[\\/][^\s'\"():,]+")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
# pytest's own spelling of a failed bare `assert`, in its `E` column.
_REWRITTEN_ASSERT = re.compile(r"^E\s+(assert\s.*)$")


def _exception_lines(tail: str) -> list[str]:
    """The exception lines in a runner's output, normalised and sorted.

    What a finding-less blocker is signed by beside its `detail` (OPEN-129).
    Only exception lines, never the whole tail, for the reason the tail is
    excluded at all: durations, temp paths and ordering seeds would make two
    runs of one failure never match. Paths and addresses are masked for the
    same reason, and the list is sorted so order cannot count as progress.
    """
    found = set()
    for raw in tail.splitlines():
        line = _RUNNER_PREFIX.sub("", raw.strip())
        if _EXCEPTION_LINE.match(line):
            found.add(_ADDRESS.sub("<addr>", _ABSOLUTE_PATH.sub("<path>", line)))
    return sorted(found)


def failure_signature(report: VerifyReport) -> str | None:
    """A stable fingerprint of *what is wrong*, ignoring how it was said.

    Two identical signatures in a row mean the last attempt changed nothing
    the gate can see -- C6.5a's no-progress rule.

    `output_tail` is deliberately excluded: it carries durations, absolute
    temp paths, and pytest's ordering seed, so hashing it would almost
    never match and the detector would never fire. 9a already parses tool
    output into structured Findings, which is what makes this stable.

    A changed line number counts as progress, correctly: the model moved
    the defect rather than leaving it where it was.

    A blocker with no findings is signed by its `detail` AND the exception
    lines in its output (OPEN-129). `detail` alone describes the stage, not
    the failure: run a4196786280d's t1 failed at conftest import, then --
    after a real fix -- inside a pytest plugin, and "the test command
    collected nothing" was the detail of both, so the task was BLOCKED for
    making no progress. A tail with no exception line adds nothing, so every
    such signature is exactly what it was.

    Returns None for a report with no blocker -- there is nothing to be
    stuck on.
    """
    blocker = report.blocker
    if blocker is None:
        return None

    if blocker.findings:
        parts = sorted(
            f"{finding.file}:{finding.line}:{finding.message}" for finding in blocker.findings
        )
    else:
        parts = [blocker.detail, *_exception_lines(blocker.output_tail)]

    digest = sha256("\n".join([blocker.name, *parts]).encode("utf-8"))
    return digest.hexdigest()[:_SIGNATURE_LENGTH]


# What `convergence` says of an attempt that ran the fix budget out (OPEN-160).
# Strings, because they are written to ledger.json as they are.
CONVERGING = "converging"
NOT_CONVERGING = "not converging"
UNREADABLE = "unreadable"


def _failures(stage: StageResult) -> set[str]:
    """What a failing stage says is wrong, spelled so two gates compare.

    A test stage by its exception lines -- its findings are pytest frames,
    which carry no exception (OPEN-119) -- AND by pytest's rewritten
    assertions, `E       assert 422 == 200`, which carry no exception name
    and so are not an exception line. They are the commonest test failure
    there is: run 566076f2af28's t1 read as unreadable without them. Read
    here and NOT added to `_exception_lines`, which signs C6.5a's
    no-progress rule and would change every signature it has written. Any other stage by `file: message`
    WITHOUT the line: an error that moved is `failure_signature`'s progress,
    rightly, but it is the same error still there.
    """
    if stage.name != "test" and stage.findings:
        return {f"{finding.file}: {finding.message}" for finding in stage.findings}
    found = set(_exception_lines(stage.output_tail))
    for raw in stage.output_tail.splitlines():
        match = _REWRITTEN_ASSERT.match(raw)
        if match:
            line = f"AssertionError: {match.group(1).strip()}"
            found.add(_ADDRESS.sub("<addr>", _ABSOLUTE_PATH.sub("<path>", line)))
    return found


def convergence(previous: VerifyReport | None, current: VerifyReport) -> tuple[str, str] | None:
    """Was the attempt between these two gates converging? (verdict, reason).

    OPEN-160, the owner's option B': a DIAGNOSTIC for a task that ran its fix
    budget out, never a reason to extend it. Replayed over the archive, this
    reading names the three converging exhaustions and the three that were
    not, but where it held mid-task the next attempt passed 0 times in 4 -- so
    it is good enough to say, not to spend on
    (`docs/superpowers/plans/2026-09-25-open-160-replay.py`).

    Converging: the gate got further in STAGE_ORDER, or none of the previous
    gate's failures is still there and it now fails on new ones. OPEN-154's
    "failed count fell" is deliberately not read: 51 -> 50 on one error is
    flat, and 17 tests -> 1 is collection collapsing.

    UNREADABLE where either gate shows nothing to compare. The replay's first
    version called run 566076f2af28's t1 converging because its previous gate
    had NO exception line in its tail, and "the old failure is gone" is true
    of an empty set.

    None when there is no pair of failing gates -- a first attempt, or a gate
    that passed.
    """
    if previous is None or previous.blocker is None or current.blocker is None:
        return None
    before, after = previous.blocker, current.blocker
    if before.name != after.name and {before.name, after.name} <= set(STAGE_ORDER):
        if STAGE_ORDER.index(after.name) > STAGE_ORDER.index(before.name):
            return CONVERGING, f"the gate got further, from {before.name} to {after.name}"
        return NOT_CONVERGING, f"the gate stopped earlier, at {after.name} after {before.name}"
    old, new = _failures(before), _failures(after)
    if not old or not new:
        which = "previous" if not old else "last"
        return UNREADABLE, f"the {which} gate's output shows no failure line to compare"
    kept = old & new
    if kept:
        return (
            NOT_CONVERGING,
            f"{len(kept)} of the previous gate's {len(old)} failure(s) are still there",
        )
    return (
        CONVERGING,
        "none of the previous gate's failures is still there, and it now fails on new ones",
    )


def tests_produced_no_judgement(report: VerifyReport) -> bool:
    """Did the test stage run and decline to say anything?

    True only when the stage is present and `not_applicable` -- no test
    command declared, or nothing collected. That is the one case where
    "no tests" is a gap the tester subagent can fill.

    An *absent* test stage means the pipeline short-circuited before
    reaching it, which is a blocked run rather than a project without
    tests; dispatching a tester there would be answering the wrong
    question.
    """
    stage = next((stage for stage in report.stages if stage.name == "test"), None)
    return stage is not None and stage.outcome == NOT_APPLICABLE


# pytest collects any callable matching `test_*` (pyproject.toml's
# python_functions), and this name matches. Without the marker it is
# collected as a test in every module that imports it and errors on a
# missing `report` fixture. Same guard, same reason, as TestResult's
# __test__ = False (testing/runner.py:46-48).
tests_produced_no_judgement.__test__ = False


__all__ = [
    "CONVERGING",
    "NOT_CONVERGING",
    "UNREADABLE",
    "convergence",
    "failure_signature",
    "tests_produced_no_judgement",
]
