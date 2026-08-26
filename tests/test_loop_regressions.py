"""OPEN-23: a task must not be blocked by a failure that predates it.

Measured 2026-08-26, run `ee29dd3ebf51`: the `t1` tester wrote
`tests/test_cli.py` against a CLI that task `t8` had not built yet. The gate
is project-wide, so every later task's verify ran the whole suite and failed
on that file. Six tasks blocked, the sixth exceeded MAX_BLOCKED_CONSULTS, and
`t8` -- the task that would have made those tests pass -- was never attempted.

The rule this module implements: **a task answers for what it broke, not for
what it inherited.** Both halves matter. A failure already failing before the
task ran is not its blocker; a failure that is new IS, whether or not it is
in a file the task touched.

That second half is why file ownership was rejected. In the same run `t2`
touched only `todo/storage.py`, yet `tests/test_cli.py` failed *through* it:
`todo/cli.py:34` calls storage, which raised at `todo/storage.py:96`. A rule
keyed on which file a finding names would have called that out of scope and
let a real regression pass.
"""

from __future__ import annotations

from rudra.loop.regressions import INHERITED, PASSED, REGRESSED, failure_keys, verdict_for


class _Finding:
    def __init__(self, file: str, line: int | None, message: str = "boom"):
        self.file = file
        self.line = line
        self.message = message


class _Report:
    def __init__(self, findings=(), passed: bool = False):
        self.passed = passed
        self.blocker = None if passed else _Blocker(findings)


class _Blocker:
    def __init__(self, findings):
        self.findings = tuple(findings)


CLI_FAILURE = (_Finding("tests/test_cli.py", 119), _Finding("tests/test_cli.py", 138))
STORAGE_FAILURE = (_Finding("todo/storage.py", 96),)


# --------------------------------------------------------------------------
# failure_keys
# --------------------------------------------------------------------------


def test_a_passing_report_has_no_failures():
    assert failure_keys(_Report(passed=True)) == frozenset()


def test_each_located_finding_becomes_one_key():
    assert failure_keys(_Report(CLI_FAILURE)) == {
        "tests/test_cli.py:119",
        "tests/test_cli.py:138",
    }


def test_a_finding_with_no_line_still_keys():
    """`Finding.line` is None whenever the tool reported no line
    (verify/result.py:29-31). Dropping those would make a whole class of
    failure invisible to the comparison."""
    assert failure_keys(_Report((_Finding("todo/storage.py", None),))) == {"todo/storage.py:None"}


def test_a_failure_with_no_located_findings_keys_to_nothing():
    """pytest output the shallow parser could not locate
    (verify/pipeline.py:291-309). It must NOT read as "no failures" -- see
    the verdict test below, which keeps such a report REGRESSED."""
    assert failure_keys(_Report(())) == frozenset()


# --------------------------------------------------------------------------
# verdict_for
# --------------------------------------------------------------------------


def test_a_passing_report_passes():
    assert verdict_for(_Report(passed=True), inherited=frozenset()) is PASSED


def test_a_passing_report_passes_even_with_a_baseline():
    """The baseline is what WAS failing. A green gate says it no longer is."""
    assert verdict_for(_Report(passed=True), inherited={"tests/test_cli.py:119"}) is PASSED


def test_a_failure_with_no_baseline_is_a_regression():
    """The first task of a run, and every task after `--continue` starts a
    fresh process. No baseline means today's behaviour, unchanged."""
    assert verdict_for(_Report(CLI_FAILURE), inherited=frozenset()) is REGRESSED


def test_a_failure_that_was_already_failing_is_inherited():
    """`t3` exactly: its blocker named tests/test_cli.py and todo/cli.py, and
    every one of those was already failing when `t2` ran."""
    inherited = {"tests/test_cli.py:119", "tests/test_cli.py:138"}
    assert verdict_for(_Report(CLI_FAILURE), inherited=inherited) is INHERITED


def test_a_subset_of_the_baseline_is_still_inherited():
    """A task that fixes SOME pre-existing failures and introduces none has
    made things better. Blocking it would be perverse."""
    inherited = {"tests/test_cli.py:119", "tests/test_cli.py:138", "tests/test_x.py:9"}
    assert verdict_for(_Report(CLI_FAILURE), inherited=inherited) is INHERITED


def test_one_new_failure_among_inherited_ones_is_a_regression():
    """`t2` exactly, and the reason file ownership was rejected: its findings
    mixed pre-existing CLI failures with a genuine storage bug of its own."""
    inherited = {"tests/test_cli.py:119", "tests/test_cli.py:138"}
    report = _Report((*CLI_FAILURE, *STORAGE_FAILURE))
    assert verdict_for(report, inherited=inherited) is REGRESSED


def test_a_regression_in_an_untouched_file_is_still_a_regression():
    """The invariant OPEN-23 says must survive: a task that breaks a
    sibling's tests must not pass. Nothing here consults files_touched, so
    breaking a file the task never opened is caught the same as any other."""
    assert verdict_for(_Report(CLI_FAILURE), inherited={"todo/storage.py:96"}) is REGRESSED


def test_an_unlocatable_failure_is_a_regression_not_an_inheritance():
    """A report that failed but yielded no parsed finding keys to the empty
    set, and the empty set is a subset of everything. Treating that as
    "inherited" would pass every unparseable failure in the run."""
    assert verdict_for(_Report(()), inherited={"tests/test_cli.py:119"}) is REGRESSED


def test_the_three_verdicts_are_distinct():
    assert len({PASSED, REGRESSED, INHERITED}) == 3
