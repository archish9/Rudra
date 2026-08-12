"""When the loop is stuck, and when it should ask for tests.

Pure functions over a VerifyReport. No I/O, no model, nothing from Rudra
except the report types -- so a test can drive every branch directly.
"""

from __future__ import annotations

from hashlib import sha256

from rudra.verify.result import NOT_APPLICABLE, VerifyReport

_SIGNATURE_LENGTH = 16


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
        parts = [blocker.detail]

    digest = sha256("\n".join([blocker.name, *parts]).encode("utf-8"))
    return digest.hexdigest()[:_SIGNATURE_LENGTH]


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


__all__ = ["failure_signature", "tests_produced_no_judgement"]
